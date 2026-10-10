"""Daily forecasting: seasonal naive vs SARIMA vs XGBoost, evaluated with rolling origins.

Conventions
-----------
* Input is the daily model series (see core.timeseries.model_series): NaN = gap/partial (masked),
  0 on closed weekdays (structural zero) and on isolated zero-sales days.
* Models LEARN from 'observed' trading days only. Closed weekdays are forecast as 0 by rule.
  Isolated zero-sales days (e.g. public holidays) stay in the EVALUATION as real outcomes, even
  though no model here can anticipate them without a holiday calendar.
* Metrics are computed on trading days (observed + zero); closed weekdays and gaps are excluded.

Multi-step strategy
-------------------
* Seasonal naive: repeats the last observed value of the same weekday.
* SARIMA: iterates its own state-space recursion (forecasts feed later steps internally).
* XGBoost: DIRECT multi-horizon. One model is trained on (origin t, horizon h) pairs. Every
  feature is either known at the origin (levels, weekday profiles, last year's values) or is a
  calendar property of the target day. No future lag is ever needed, so nothing is fed back and
  nothing leaks. The target is y[t+h] / level28[t] (scale-free, so trees can follow trend).

Splits
------
The last `test_days` are a final test never used for tuning or selection. Before it, rolling
validation origins choose hyper-parameters and the model per horizon. In the test window the
frozen configuration is re-fitted at each origin on data up to that origin only.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

HORIZONS = [7, 30, 90]
BUCKETS = [(1, 7), (8, 30), (31, 90)]
INTERVAL_LEVEL = 0.80
MAX_VALIDATION_WMAPE = 0.40

FEATURES = [
    "h", "dow", "dom", "month", "weekofyear",
    "level7_ratio", "level56_ratio", "trend28", "vol28",
    "wd_last_ratio", "wd_mean4_ratio", "wd_mean8_ratio",
    "ly_ratio", "ly_smooth_ratio", "yoy_level",
]
FEATURE_DESCRIPTIONS = {
    "h": "How many days ahead the forecast is",
    "dow": "Day of the week being forecast",
    "dom": "Day of the month being forecast",
    "month": "Month being forecast",
    "weekofyear": "Week of the year being forecast",
    "level7_ratio": "Last 7 days' average sales relative to the last 28 days",
    "level56_ratio": "Last 56 days' average sales relative to the last 28 days",
    "trend28": "Last 28 days' average relative to the 28 days before",
    "vol28": "How variable daily sales were in the last 28 days",
    "wd_last_ratio": "Most recent sales on the same weekday, relative to the recent level",
    "wd_mean4_ratio": "Average of the last 4 same weekdays, relative to the recent level",
    "wd_mean8_ratio": "Average of the last 8 same weekdays, relative to the recent level",
    "ly_ratio": "Same day last year, relative to last year's level at the equivalent date",
    "ly_smooth_ratio": "Same week last year (7-day average), relative to last year's level",
    "yoy_level": "Recent level compared with the same time last year",
}
DEFAULT_XGB = {"max_depth": 4, "learning_rate": 0.05, "n_estimators": 300, "subsample": 0.8,
               "colsample_bytree": 0.8, "min_child_weight": 5}
DEFAULT_SARIMA = {"order": [1, 0, 1], "seasonal_order": [0, 1, 1, 7], "log": True}
# Small candidate set used by the application pipeline (the experiment script searches wider grids).
FAST_CANDIDATES = {
    "seasonal_naive": [None],
    "sarima": [DEFAULT_SARIMA],
    "xgboost": [{**DEFAULT_XGB, "max_depth": 2, "learning_rate": 0.03, "n_estimators": 150}, DEFAULT_XGB],
}


# ----------------------------------------------------------------------------- metrics
def forecast_metrics(y: np.ndarray, yhat: np.ndarray) -> dict:
    """MAE = mean|e|; RMSE = sqrt(mean e^2); WMAPE = sum|e| / sum|y| (None if sum|y| = 0)."""
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    ok = ~np.isnan(y) & ~np.isnan(yhat)
    y, yhat = y[ok], yhat[ok]
    if len(y) == 0:
        return {"n": 0, "mae": None, "rmse": None, "wmape": None}
    e = y - yhat
    denom = float(np.abs(y).sum())
    return {"n": int(len(y)), "mae": float(np.abs(e).mean()), "rmse": float(np.sqrt((e ** 2).mean())),
            "wmape": float(np.abs(e).sum() / denom) if denom > 0 else None}


# ----------------------------------------------------------------------------- context
class ForecastContext:
    """Holds the series and feature tables. Every table at row t uses data up to and including t."""

    def __init__(self, series: pd.Series, status: pd.Series):
        self.series = series.astype(float)
        self.status = status
        self.index = series.index
        self.n = len(series)
        obs = self.series.where(status == "observed")
        self.obs = obs
        self.dow = np.asarray(self.index.dayofweek)
        self.closed_dows = sorted(set(self.dow[(status == "closed").values]))
        self.evaluable = status.isin(["observed", "zero"]).values
        self.level28 = obs.rolling(28, min_periods=8).mean()
        lvl = self.level28
        self.f_level7 = (obs.rolling(7, min_periods=2).mean() / lvl).values
        self.f_level56 = (obs.rolling(56, min_periods=16).mean() / lvl).values
        self.f_trend28 = (lvl / lvl.shift(28)).values
        self.f_vol28 = (obs.rolling(28, min_periods=8).std() / lvl).values
        self.f_yoy = (lvl / lvl.shift(364)).values
        # weekday tables [t, d]: statistics of weekday d using observations at or before t
        self.wd_last = np.full((self.n, 7), np.nan)
        self.wd_mean4 = np.full((self.n, 7), np.nan)
        self.wd_mean8 = np.full((self.n, 7), np.nan)
        for d in range(7):
            sub = obs[(self.dow == d)].dropna()
            if sub.empty:
                continue
            self.wd_last[:, d] = sub.reindex(self.index).ffill().values
            self.wd_mean4[:, d] = sub.rolling(4, min_periods=2).mean().reindex(self.index).ffill().values
            self.wd_mean8[:, d] = sub.rolling(8, min_periods=3).mean().reindex(self.index).ffill().values
        self.obs_v = obs.values
        self.level_v = lvl.values
        # centred 7-day mean: row j uses j-3..j+3; only ever read at j = target-364 <= t-274
        self.smooth7 = obs.rolling(7, center=True, min_periods=3).mean().values
        self.dom = np.asarray(self.index.day)
        self.month = np.asarray(self.index.month)
        self.woy = np.asarray(self.index.isocalendar().week, dtype=int)

    def future_calendar(self, origin: int, H: int):
        dates = pd.date_range(self.index[origin] + pd.Timedelta(days=1), periods=H, freq="D")
        return dates

    def pairs(self, origins: np.ndarray, horizons: np.ndarray, future_dates: pd.DatetimeIndex | None = None) -> pd.DataFrame:
        """Feature rows for (origin, horizon) pairs. Targets beyond the series get NaN."""
        t, h = np.asarray(origins), np.asarray(horizons)
        j = t + h
        inside = j < self.n
        if future_dates is not None:  # forecasting past the end of the data
            dates = future_dates
            dow, dom, month, woy = (np.asarray(dates.dayofweek), np.asarray(dates.day), np.asarray(dates.month),
                                    np.asarray(dates.isocalendar().week, dtype=int))
        else:
            jj = np.where(inside, j, 0)
            dow, dom, month, woy = self.dow[jj], self.dom[jj], self.month[jj], self.woy[jj]
        lvl = self.level_v[t]
        ly = j - 364
        ly_ok = (ly >= 0) & (ly <= t) & (t - 364 >= 0)
        lyi, tl = np.where(ly_ok, ly, 0), np.where(ly_ok, t - 364, 0)
        ly_level = np.where(ly_ok, self.level_v[tl], np.nan)
        df = pd.DataFrame({
            "origin": t, "h": h, "dow": dow, "dom": dom, "month": month, "weekofyear": woy,
            "level7_ratio": self.f_level7[t], "level56_ratio": self.f_level56[t],
            "trend28": self.f_trend28[t], "vol28": self.f_vol28[t],
            "wd_last_ratio": self.wd_last[t, dow] / lvl,
            "wd_mean4_ratio": self.wd_mean4[t, dow] / lvl,
            "wd_mean8_ratio": self.wd_mean8[t, dow] / lvl,
            "ly_ratio": np.where(ly_ok, self.obs_v[lyi], np.nan) / ly_level,
            "ly_smooth_ratio": np.where(ly_ok, self.smooth7[lyi], np.nan) / ly_level,
            "yoy_level": self.f_yoy[t],
            "level": lvl,
        })
        y = np.where(inside, self.obs_v[np.where(inside, j, 0)], np.nan)
        df["y"] = y
        df["target"] = y / lvl
        return df

    def training_pairs(self, origin: int, H: int) -> pd.DataFrame:
        """All pairs whose TARGET day is at or before `origin` (nothing later is visible)."""
        ts = np.arange(27, origin)
        t = np.repeat(ts, H)
        h = np.tile(np.arange(1, H + 1), len(ts))
        keep = (t + h) <= origin
        df = self.pairs(t[keep], h[keep])
        return df[df["target"].notna() & np.isfinite(df["target"]) & df["level"].gt(0)]


# ----------------------------------------------------------------------------- models
def _apply_closed(ctx: ForecastContext, dates: pd.DatetimeIndex, pred: np.ndarray) -> np.ndarray:
    pred = np.asarray(pred, float).copy()
    pred[np.isin(dates.dayofweek, ctx.closed_dows)] = 0.0
    return np.clip(pred, 0, None) if (ctx.obs.min() >= 0) else pred


def seasonal_naive(ctx: ForecastContext, origin: int, H: int, cfg=None):
    dates = ctx.future_calendar(origin, H)
    fallback = np.nanmean(ctx.obs_v[max(0, origin - 27): origin + 1]) if origin >= 0 else np.nan
    pred = np.array([ctx.wd_last[origin, d] for d in dates.dayofweek])
    pred = np.where(np.isnan(pred), fallback, pred)
    return _apply_closed(ctx, dates, pred), None


def sarima(ctx: ForecastContext, origin: int, H: int, cfg=None):
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    cfg = cfg or DEFAULT_SARIMA
    y = ctx.obs.iloc[: origin + 1].copy()
    y.index = pd.DatetimeIndex(y.index, freq="D")
    if cfg.get("log"):
        y = np.log(y.where(y > 0))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = SARIMAX(y, order=tuple(cfg["order"]), seasonal_order=tuple(cfg["seasonal_order"]),
                        enforce_stationarity=False, enforce_invertibility=False)
        res = model.fit(disp=False, maxiter=60)
        fc = np.asarray(res.forecast(H))
    if cfg.get("log"):
        fc = np.exp(fc)  # median of the log-normal forecast distribution
    dates = ctx.future_calendar(origin, H)
    return _apply_closed(ctx, dates, fc), None


def xgb_direct(ctx: ForecastContext, origin: int, H: int, cfg=None):
    import xgboost as xgb
    params = {**DEFAULT_XGB, **(cfg or {})}
    train = ctx.training_pairs(origin, H)
    dates = ctx.future_calendar(origin, H)
    if len(train) < 200 or not np.isfinite(ctx.level_v[origin]):
        return seasonal_naive(ctx, origin, H)[0], None
    model = xgb.XGBRegressor(objective="reg:squarederror", random_state=0, n_jobs=2, **params)
    model.fit(train[FEATURES], train["target"])
    fut = ctx.pairs(np.full(H, origin), np.arange(1, H + 1), future_dates=dates)
    pred = model.predict(fut[FEATURES]) * ctx.level_v[origin]
    return _apply_closed(ctx, dates, pred), {"model": model, "train": train, "future": fut}


MODELS = {"seasonal_naive": seasonal_naive, "sarima": sarima, "xgboost": xgb_direct}
MODEL_LABELS = {"seasonal_naive": "Seasonal naive (same weekday last week)", "sarima": "SARIMA",
                "xgboost": "XGBoost (direct multi-horizon)"}


# ----------------------------------------------------------------------------- backtesting
def backtest(ctx: ForecastContext, model: str, cfg, origins: list[int], H: int) -> pd.DataFrame:
    """Forecast H days from each origin using only data up to the origin. Returns long rows."""
    rows = []
    for o in origins:
        pred, _ = MODELS[model](ctx, o, H, cfg)
        for step in range(1, H + 1):
            j = o + step
            if j >= ctx.n:
                break
            rows.append((o, step, ctx.index[j], ctx.series.iloc[j] if ctx.evaluable[j] else np.nan, pred[step - 1]))
    return pd.DataFrame(rows, columns=["origin", "h", "date", "y", "yhat"])


def horizon_metrics(bt: pd.DataFrame, horizon: int, origins: list[int] | None = None) -> dict:
    d = bt[bt["h"] <= horizon]
    if origins is not None:
        d = d[d["origin"].isin(origins)]
    d = d.dropna(subset=["y"])
    m = forecast_metrics(d["y"].values, d["yhat"].values)
    tot = d.groupby("origin").agg(y=("y", "sum"), yhat=("yhat", "sum"))
    tot = tot[tot["y"].abs() > 0]
    m["total_ape_mean"] = float(((tot["yhat"] - tot["y"]).abs() / tot["y"].abs()).mean()) if len(tot) else None
    m["origins"] = int(d["origin"].nunique())
    return m


@dataclass
class SplitPlan:
    n: int
    test_days: int
    horizons: list[int]
    disabled: dict[int, str]
    val_origins: list[int]
    test_origins: dict[int, list[int]] = field(default_factory=dict)

    @property
    def test_start(self) -> int:
        return self.n - self.test_days

    def to_dict(self, index: pd.DatetimeIndex) -> dict:
        iso = lambda i: index[i].date().isoformat()
        return {
            "days": self.n, "test_days": self.test_days,
            "train_validation_end": iso(self.test_start - 1),
            "test_start": iso(self.test_start), "test_end": iso(self.n - 1),
            "validation_origins": [iso(o) for o in self.val_origins],
            "test_origins": {str(h): [iso(o) for o in v] for h, v in self.test_origins.items()},
            "horizons_evaluated": self.horizons, "horizons_disabled": {str(k): v for k, v in self.disabled.items()},
        }


def plan_splits(n: int, min_train: int = 84, max_val_origins: int = 11) -> SplitPlan:
    """Chronological plan. A horizon h needs test window h, at least 3 validation origins that
    each have `min_train`+ days of history and h days of future before the test window."""
    horizons, disabled = [], {}
    for h in HORIZONS:
        room = n - h - h - max(min_train, 2 * h)  # test + one validation forecast + history
        if room >= 2 * max(7, h // 6):
            horizons.append(h)
        else:
            need = h + h + max(min_train, 2 * h) + 2 * max(7, h // 6)
            disabled[h] = f"Needs about {need} days of history for a chronological test; this dataset has {n}."
    if not horizons:
        return SplitPlan(n, 0, [], disabled, [])
    H = max(horizons)
    test_days = H
    spacing = max(7, H // 6)
    latest = n - test_days - 1 - H
    earliest = max(min_train, 2 * H)
    val = [latest - k * spacing for k in range(max_val_origins) if latest - k * spacing >= earliest]
    plan = SplitPlan(n, test_days, horizons, disabled, sorted(val))
    for h in horizons:
        plan.test_origins[h] = [plan.test_start - 1 + k * h for k in range(test_days // h)]
    return plan


def _interval_quantiles(bt: pd.DataFrame, level: float = INTERVAL_LEVEL) -> list[dict]:
    """Empirical quantiles of out-of-sample relative errors (y - yhat)/yhat by horizon bucket."""
    d = bt.dropna(subset=["y"])
    d = d[d["yhat"] > 0]
    rel = (d["y"] - d["yhat"]) / d["yhat"]
    lo, hi = (1 - level) / 2, 1 - (1 - level) / 2
    out = []
    for a, b in BUCKETS:
        r = rel[(d["h"] >= a) & (d["h"] <= b)]
        if len(r) >= 20:
            out.append({"from": a, "to": b, "n": int(len(r)), "q_lo": float(r.quantile(lo)), "q_hi": float(r.quantile(hi))})
    return out


def apply_intervals(pred: np.ndarray, quantiles: list[dict]):
    lo, hi = np.full(len(pred), np.nan), np.full(len(pred), np.nan)
    for q in quantiles:
        sl = slice(q["from"] - 1, min(q["to"], len(pred)))
        lo[sl] = np.clip(pred[sl] * (1 + q["q_lo"]), 0, None)
        hi[sl] = pred[sl] * (1 + q["q_hi"])
    return lo, hi


def interval_coverage(bt: pd.DataFrame, quantiles: list[dict]) -> dict | None:
    d = bt.dropna(subset=["y"]).copy()
    if not quantiles or d.empty:
        return None
    d["lo"], d["hi"] = np.nan, np.nan
    for q in quantiles:
        m = (d["h"] >= q["from"]) & (d["h"] <= q["to"])
        d.loc[m, "lo"] = np.clip(d.loc[m, "yhat"] * (1 + q["q_lo"]), 0, None)
        d.loc[m, "hi"] = d.loc[m, "yhat"] * (1 + q["q_hi"])
    d = d.dropna(subset=["lo"])
    if d.empty:
        return None
    inside = (d["y"] >= d["lo"]) & (d["y"] <= d["hi"])
    return {"nominal": INTERVAL_LEVEL, "empirical": float(inside.mean()), "n": int(len(d))}


def explain_xgb(extra: dict, top: int = 10) -> dict:
    """Global importance = mean |TreeSHAP contribution| over the training rows (ratio units)."""
    import xgboost as xgb
    model, train = extra["model"], extra["train"]
    sample = train.sample(min(len(train), 5000), random_state=0)
    contrib = model.get_booster().predict(xgb.DMatrix(sample[FEATURES]), pred_contribs=True)[:, :-1]
    imp = np.abs(contrib).mean(axis=0)
    order = np.argsort(-imp)
    total = float(imp.sum()) or 1.0
    return {
        "method": "TreeSHAP (xgboost pred_contribs), mean absolute contribution on a sample of training rows",
        "features": [{"feature": FEATURES[i], "description": FEATURE_DESCRIPTIONS[FEATURES[i]],
                      "mean_abs_shap": float(imp[i]), "share": float(imp[i] / total)} for i in order[:top]],
        "note": "Importance shows what the model relies on to predict. It is not evidence of what causes sales.",
    }


def run_forecasting(series: pd.Series, status: pd.Series, candidates: dict[str, list] | None = None,
                    progress=None) -> dict:
    """Full procedure: plan -> validate candidates -> select per horizon -> final test -> deploy.

    candidates maps model name -> list of configurations to try on validation.
    """
    candidates = candidates or FAST_CANDIDATES
    ctx = ForecastContext(series, status)
    plan = plan_splits(ctx.n)
    result: dict = {"plan": plan.to_dict(ctx.index) if plan.horizons else {"horizons_disabled": {str(k): v for k, v in plan.disabled.items()}},
                    "metric_definitions": {
                        "mae": "Mean absolute error per trading day, in currency units.",
                        "rmse": "Root mean squared error per trading day; penalises large misses more.",
                        "wmape": "Sum of absolute errors divided by sum of actual values over the evaluated days "
                                 "(undefined, shown as null, if actuals sum to zero).",
                        "total_ape_mean": "Average absolute % error of the TOTAL over each forecast window.",
                        "evaluated_days": "Trading days only (observed sales and isolated zero-sales days); closed "
                                          "weekdays and data gaps are excluded.",
                    },
                    "horizons": {}, "validation": [], "models": {}}
    if not plan.horizons:
        return result
    H = max(plan.horizons)

    # 1) validation: every candidate config, rolling origins, never touching the test window
    val_bts, best_cfg = {}, {}
    for name, cfgs in candidates.items():
        scored = []
        for cfg in cfgs:
            if progress:
                progress(f"Validating {name}")
            bt = backtest(ctx, name, cfg, plan.val_origins, H)
            row = {"model": name, "config": cfg}
            for h in plan.horizons:
                row[f"h{h}"] = horizon_metrics(bt, h)
            result["validation"].append(row)
            score = np.mean([row[f"h{h}"]["wmape"] for h in plan.horizons if row[f"h{h}"]["wmape"] is not None])
            scored.append((score, cfg, bt))
        scored.sort(key=lambda x: x[0])
        best_cfg[name], val_bts[name] = scored[0][1], scored[0][2]
        result["models"][name] = {"label": MODEL_LABELS[name], "config": best_cfg[name],
                                  "configs_tried": len(cfgs), "validation_mean_wmape": float(scored[0][0])}

    # 2) final test with frozen configs: rolling origins inside the untouched window
    test_origin_set = sorted({o for v in plan.test_origins.values() for o in v})
    test_bts = {}
    for name in candidates:
        if progress:
            progress(f"Testing {name}")
        test_bts[name] = backtest(ctx, name, best_cfg[name], test_origin_set, H)

    # 3) per-horizon selection on validation, reporting on test
    last = ctx.n - 1
    deploy_cache: dict[str, tuple] = {}
    for h in plan.horizons:
        table = []
        for name in candidates:
            table.append({"model": name, "label": MODEL_LABELS[name],
                          "validation": horizon_metrics(val_bts[name], h),
                          "test": horizon_metrics(test_bts[name], h, plan.test_origins[h])})
        ranked = sorted([r for r in table if r["validation"]["wmape"] is not None], key=lambda r: r["validation"]["wmape"])
        entry = {"horizon": h, "comparison": table, "enabled": False}
        if not ranked:
            entry["reason"] = "Validation actuals sum to zero, so no model could be compared."
            result["horizons"][str(h)] = entry
            continue
        chosen = ranked[0]["model"]
        entry["selected_model"] = chosen
        entry["selected_label"] = MODEL_LABELS[chosen]
        entry["selection_rule"] = "Lowest validation WMAPE across rolling origins; the test window was not used."
        vw = ranked[0]["validation"]["wmape"]
        if len(plan.val_origins) < 3:
            entry["reason"] = "Fewer than 3 validation origins are available for this horizon."
        elif vw > MAX_VALIDATION_WMAPE:
            entry["reason"] = (f"Best validation WMAPE is {vw:.0%}, above the {MAX_VALIDATION_WMAPE:.0%} limit set for "
                               f"showing a forecast.")
        else:
            entry["enabled"] = True
        # intervals: calibrate on validation, check coverage on test, deploy with both pooled
        vb = val_bts[chosen][val_bts[chosen]["h"] <= h]
        tb = test_bts[chosen][(test_bts[chosen]["h"] <= h) & test_bts[chosen]["origin"].isin(plan.test_origins[h])]
        q_val = _interval_quantiles(vb)
        # release check: the test window never chooses the model, but a selected model that fails
        # on it is not shown to users.
        tw = next(r for r in table if r["model"] == chosen)["test"]["wmape"]
        if entry["enabled"] and tw is not None and tw > MAX_VALIDATION_WMAPE:
            entry["enabled"] = False
            entry["reason"] = (f"The selected model's WMAPE on the final test window was {tw:.0%}, above the "
                               f"{MAX_VALIDATION_WMAPE:.0%} limit, so this horizon is not offered.")
        entry["interval"] = {
            "method": "Empirical quantiles of out-of-sample relative errors from rolling-origin forecasts, "
                      "by horizon bucket (assumes future errors resemble past ones).",
            "level": INTERVAL_LEVEL,
            "test_coverage": interval_coverage(tb, q_val),
            "quantiles": _interval_quantiles(pd.concat([vb, tb])),
        }
        if chosen not in deploy_cache:
            if progress:
                progress(f"Fitting deployed {chosen}")
            deploy_cache[chosen] = MODELS[chosen](ctx, last, H, best_cfg[chosen])
        pred = deploy_cache[chosen][0][:h]
        dates = ctx.future_calendar(last, h)
        lo, hi = apply_intervals(pred, entry["interval"]["quantiles"])
        entry["forecast"] = [{"date": d.date().isoformat(), "yhat": float(p),
                              "lo": None if np.isnan(a) else float(a), "hi": None if np.isnan(b) else float(b),
                              "closed": bool(d.dayofweek in ctx.closed_dows)}
                             for d, p, a, b in zip(dates, pred, lo, hi)]
        entry["forecast_total"] = float(pred.sum())
        entry["origin"] = ctx.index[last].date().isoformat()
        result["horizons"][str(h)] = entry
    for h, why in plan.disabled.items():
        result["horizons"][str(h)] = {"horizon": h, "enabled": False, "reason": why}

    # 4) explainability for the ML model (fitted on all data)
    if "xgboost" in candidates:
        extra = deploy_cache.get("xgboost", (None, None))[1]
        if extra is None:
            extra = xgb_direct(ctx, last, H, best_cfg["xgboost"])[1]
        if extra is not None:
            result["explainability"] = explain_xgb(extra)
            result["explainability"]["applies_to"] = "xgboost"
    result["backtests"] = {"validation": val_bts, "test": test_bts}  # DataFrames; callers serialise as needed
    result["limitations"] = [
        "No holiday or promotion calendar is used, so public holidays and seasonal closures are not anticipated.",
        "Forecasts assume the closed-weekday pattern seen in the data continues.",
        "Intervals are empirical error bands, not model-based probability statements.",
    ]
    return result
