"""Anomaly detection on daily metrics.

Detector A (transparent, used for alerts): seasonal robust z-score.
  expected(t) = median of the previous 8 observed values on the SAME weekday (past data only),
                so ordinary weekday/weekend patterns are part of the expectation;
  r(t)        = log(actual / expected), so a halving and a doubling are equally unusual;
  scale(t)    = 1.4826 x median absolute deviation of r over the previous 56 days (floor 0.05);
  z(t)        = r / scale.   Alert when |z| >= 3.5 (the usual cut-off for a modified z-score).
Detector B (comparison): Isolation Forest on the log deviations of all available metrics.

There are no labelled anomalies, so no accuracy is claimed. The experiment script measures recall
on clearly labelled, artificially injected shocks only.
False-positive risks: sustained seasonal ramps (the 8-week median lags them), promotions, and
days after a data gap all look unusual to this detector.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

Z_ALERT, Z_MEDIUM, Z_HIGH = 3.5, 4.5, 6.0
K_WEEKS, SCALE_WINDOW, SCALE_FLOOR, LOG_FLOOR = 8, 56, 0.05, 0.05
METRIC_LABELS = {"net_revenue": "Net revenue", "transactions": "Transactions", "customers": "Active customers"}


def baseline(values: pd.Series, status: pd.Series) -> pd.DataFrame:
    """Expected value, relative deviation and robust z for each trading day, from past data only."""
    obs = values.where(status == "observed")
    dow = np.asarray(values.index.dayofweek)
    expected = pd.Series(np.nan, index=values.index)
    for d in range(7):
        sub = obs[dow == d].dropna()
        if len(sub) > 4:
            exp = sub.shift(1).rolling(K_WEEKS, min_periods=4).median()
            expected.loc[dow == d] = exp.reindex(values.index[dow == d]).ffill().values
    eligible = status.isin(["observed", "zero"])
    actual = values.where(eligible)
    exp_pos = expected.where(expected > 0)
    rel = (actual - expected) / exp_pos
    # log ratio treats a halving and a doubling as equally unusual; values at or below zero are
    # floored at 5% of expected so that a no-sales day gets a large, finite score
    logr = np.log(np.maximum(actual, LOG_FLOOR * exp_pos) / exp_pos)
    logr_obs = logr.where(status == "observed")
    med = logr_obs.shift(1).rolling(SCALE_WINDOW, min_periods=15).median()
    mad = (logr_obs - med).abs().shift(1).rolling(SCALE_WINDOW, min_periods=15).median()
    scale = (1.4826 * mad).clip(lower=SCALE_FLOOR)
    z = logr / scale
    return pd.DataFrame({"actual": actual, "expected": expected, "rel": rel, "logr": logr, "scale": scale, "z": z,
                         "lo": expected * np.exp(-Z_ALERT * scale), "hi": expected * np.exp(Z_ALERT * scale)})


def severity(z: float) -> str:
    a = abs(z)
    return "high" if a >= Z_HIGH else "medium" if a >= Z_MEDIUM else "low"


def detect_statistical(daily: pd.DataFrame, metrics: list[str]) -> tuple[list[dict], dict[str, pd.DataFrame]]:
    alerts, tables = [], {}
    for m in metrics:
        b = baseline(daily[m].astype(float), daily["status"])
        tables[m] = b
        hits = b[b["z"].abs() >= Z_ALERT]
        for date, r in hits.iterrows():
            alerts.append({
                "metric": m, "metric_label": METRIC_LABELS.get(m, m), "date": date.date().isoformat(),
                "actual": float(r["actual"]), "expected": float(r["expected"]),
                "expected_lo": float(r["lo"]), "expected_hi": float(r["hi"]),
                "deviation": float(r["actual"] - r["expected"]), "deviation_pct": float(r["rel"]),
                "z": float(r["z"]), "direction": "above" if r["z"] > 0 else "below",
                "severity": severity(r["z"]), "method": "Seasonal robust z-score (same-weekday median, MAD scale)",
                "day_status": daily.loc[date, "status"],
            })
    return alerts, tables


def detect_isolation_forest(tables: dict[str, pd.DataFrame], contamination: float = 0.03, seed: int = 0) -> pd.DataFrame:
    """Multivariate comparison detector over the relative deviations of all metrics."""
    from sklearn.ensemble import IsolationForest
    x = pd.DataFrame({m: t["logr"] for m, t in tables.items()}).dropna()
    if len(x) < 60:
        return pd.DataFrame(columns=["score", "flag"])
    forest = IsolationForest(n_estimators=300, contamination=contamination, random_state=seed).fit(x.values)
    return pd.DataFrame({"score": -forest.score_samples(x.values), "flag": forest.predict(x.values) == -1}, index=x.index)


def compare_methods(tables: dict[str, pd.DataFrame], iso: pd.DataFrame) -> dict:
    stat_days = set()
    for t in tables.values():
        stat_days |= set(t.index[t["z"].abs() >= Z_ALERT])
    iso_days = set(iso.index[iso["flag"]]) if len(iso) else set()
    both = stat_days & iso_days
    union = stat_days | iso_days
    return {"statistical_days": len(stat_days), "isolation_forest_days": len(iso_days), "both": len(both),
            "jaccard": (len(both) / len(union)) if union else None,
            "note": "Agreement between two unlabelled detectors; it is not an accuracy figure."}


def alert_context(clean: pd.DataFrame, daily: pd.DataFrame, date: pd.Timestamp, dims: list[str]) -> dict:
    """What was different on the alert day versus the average of the same-weekday baseline days."""
    date = pd.Timestamp(date)
    same = daily[(daily.index < date) & (daily["dow"] == date.dayofweek) & (daily["status"] == "observed")].tail(K_WEEKS)
    day = clean[clean["date"] == date]
    base = clean[clean["date"].isin(same.index)]
    n_base = max(len(same), 1)
    out: dict = {"baseline_days": [d.date().isoformat() for d in same.index], "dimensions": {}}
    tx = daily.loc[date, "transactions"]
    exp_tx = float(same["transactions"].median()) if len(same) else None
    atv = (daily.loc[date, "net_revenue"] / tx) if tx else None
    base_atv = (same["net_revenue"] / same["transactions"].where(same["transactions"] > 0)).median() if len(same) else None
    out["transactions"] = {"actual": float(tx), "expected": exp_tx}
    out["avg_transaction_value"] = {"actual": None if atv is None else float(atv),
                                    "expected": None if base_atv is None or np.isnan(base_atv) else float(base_atv)}
    sales = day[day["row_type"] == "sale"]
    if len(sales):
        by_tx = sales.groupby("transaction_id")["amount"].sum()
        gross = float(by_tx.sum())
        out["largest_transaction"] = {"value": float(by_tx.max()), "share_of_day_sales": float(by_tx.max() / gross) if gross else None}
    rets = float(-day.loc[day["row_type"] == "return", "amount"].sum())
    out["returns_value"] = {"actual": rets, "expected": float(same["returns_value"].median()) if len(same) else None}
    for dim in dims:
        if dim not in clean.columns or clean[dim].isna().all():
            continue
        a = day.groupby(day[dim].fillna("(not recorded)"))["amount"].sum()
        b = base.groupby(base[dim].fillna("(not recorded)"))["amount"].sum() / n_base
        diff = a.sub(b, fill_value=0.0)
        top = diff.reindex(diff.abs().sort_values(ascending=False).index).head(3)
        names = None
        if dim == "product_id":
            both = pd.concat([day, base])
            names = both.dropna(subset=["product_name"]).groupby("product_id")["product_name"].first()
        out["dimensions"][dim] = [{"key": str(k), "label": (f"{names[k]} ({k})" if names is not None and k in names.index else str(k)),
                                   "difference": float(v)} for k, v in top.items()]
    return out


def run_anomaly_detection(clean: pd.DataFrame, daily: pd.DataFrame, has_customers: bool, dims: list[str],
                          context_limit: int = 40) -> dict:
    metrics = ["net_revenue", "transactions"] + (["customers"] if has_customers else [])
    d = daily[daily["status"] != "partial"]
    alerts, tables = detect_statistical(d, metrics)
    iso = detect_isolation_forest(tables)
    for a in alerts:
        ts = pd.Timestamp(a["date"])
        a["isolation_forest_flag"] = bool(iso.loc[ts, "flag"]) if ts in iso.index else None
        if a["day_status"] == "zero":
            a["note"] = "No sales were recorded on a normally trading weekday (possible holiday or closure)."
    alerts.sort(key=lambda a: (a["date"], abs(a["z"])), reverse=True)
    # contributing-factor context for the most recent alert days (bounded to keep the job fast)
    days = []
    for a in alerts:
        if a["date"] not in days:
            days.append(a["date"])
    ctx = {day: alert_context(clean, d, pd.Timestamp(day), dims) for day in days[:context_limit]}
    for a in alerts:
        a["context"] = ctx.get(a["date"])
    series = {m: [{"date": i.date().isoformat(), "actual": None if np.isnan(r.actual) else float(r.actual),
                   "expected": None if np.isnan(r.expected) else float(r.expected),
                   "lo": None if np.isnan(r.lo) else float(r.lo), "hi": None if np.isnan(r.hi) else float(r.hi)}
                  for i, r in t.iterrows()] for m, t in tables.items()}
    return {
        "alerts": alerts, "metrics": metrics, "series": series,
        "method_comparison": compare_methods(tables, iso),
        "thresholds": {"alert": Z_ALERT, "medium": Z_MEDIUM, "high": Z_HIGH,
                       "explanation": "z is the log ratio of actual to the same-weekday median, divided by a robust "
                                      "(MAD-based) measure of how much that ratio normally varies."},
        "gap_days": int((d["status"] == "gap").sum()),
        "caveats": [
            "There are no labelled anomalies in this data, so detection accuracy is not known.",
            "Seasonal ramps, promotions and the first days after a closure can trigger alerts without anything being wrong.",
            "Days inside data gaps are not scored.",
        ],
    }
