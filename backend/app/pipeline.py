"""Orchestrates one dataset: ingest -> daily table -> models -> diagnostics -> recommendations.

Heavy work (cleaning, model selection, clustering) runs once per dataset in a background job and
is saved. API requests only read saved outputs or run cheap filters on the cleaned table.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from .analytics.diagnostics import diagnose
from .analytics.recommendations import build_recommendations
from .core import capabilities as caps_mod
from .core.ingest import clean_transactions, read_raw_csv
from .core.kpis import compare_periods, compute_kpis, slice_period, trailing_period
from .core.schema import IngestSettings
from .core.timeseries import build_daily, last_complete_day, model_series, status_summary
from .ml.anomalies import run_anomaly_detection
from .ml.forecasting import run_forecasting
from .ml.segments import run_customer_intelligence


def jsonable(o):
    """Convert numpy/pandas scalars and NaN into JSON-safe values."""
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if (np.isnan(o) or np.isinf(o)) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    return o


def default_comparison(clean, daily, days: int = 28) -> dict:
    as_of = last_complete_day(daily)
    cur = trailing_period(as_of, days)
    out = compare_periods(clean, daily, cur)
    out["as_of"] = as_of.date().isoformat()
    out["recent30_net_revenue"] = compute_kpis(slice_period(clean, trailing_period(as_of, 30)))["net_revenue"]
    return out


def default_diagnosis(clean, daily, caps, segment_labels=None, days: int = 28):
    as_of = last_complete_day(daily)
    cur = trailing_period(as_of, days)
    prev = cur.previous()
    if prev.start < daily.index.min():
        return None
    return diagnose(clean, prev, cur, caps_mod.available_dimensions(caps),
                    caps_mod.enabled(caps, "customer_rfm"), segment_labels)


def run_pipeline(raw_path: str, out_dir: str, settings: IngestSettings, progress=lambda stage, pct: None,
                 forecast_candidates=None) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    progress("Reading and validating the file", 5)
    raw = read_raw_csv(raw_path)
    res = clean_transactions(raw, settings)
    del raw
    clean, report = res.clean, res.report
    clean.to_parquet(os.path.join(out_dir, "clean.parquet"), index=False)

    progress("Building daily metrics", 20)
    daily = build_daily(clean)
    days = status_summary(daily)
    report["calendar"] = days
    complete = daily[daily["status"] != "partial"]
    caps = caps_mod.build_capabilities(report, days)

    progress("Training and back-testing forecast models", 30)
    forecast = run_forecasting(model_series(complete), complete["status"], forecast_candidates,
                               progress=lambda m: progress(f"Forecasting: {m}", 45))
    bts = forecast.pop("backtests", None)
    if bts:
        for h, entry in forecast["horizons"].items():
            m = entry.get("selected_model")
            if not m:
                continue
            origins = [pd.Timestamp(d) for d in forecast["plan"]["test_origins"][h]]
            tb = bts["test"][m]
            tb = tb[(tb["h"] <= int(h)) & tb["origin"].map(lambda o: complete.index[o]).isin(origins)]
            entry["test_backtest"] = [{"date": r.date.date().isoformat(), "y": r.y, "yhat": r.yhat} for r in tb.itertuples()]
        pd.concat([df.assign(model=m, split=s) for s, d in bts.items() for m, df in d.items()]).to_csv(
            os.path.join(out_dir, "forecast_backtests.csv"), index=False)
    forecast["target"] = f"daily net revenue ({settings.currency})"
    caps = caps_mod.build_capabilities(report, days, forecast.get("horizons"))

    customers, seg_table = None, None
    if caps_mod.enabled(caps, "customer_rfm"):
        progress("Segmenting customers", 65)
        cutoff = last_complete_day(daily) + pd.Timedelta(days=1)
        customers = run_customer_intelligence(clean, cutoff, settings.currency,
                                              do_kmeans=caps_mod.enabled(caps, "customer_kmeans"))
        seg_table = customers.pop("table")
        seg_table.reset_index().to_parquet(os.path.join(out_dir, "customers.parquet"), index=False)

    anomalies = None
    if caps_mod.enabled(caps, "anomalies"):
        progress("Detecting anomalies", 80)
        dims = [d for d in caps_mod.available_dimensions(caps) if d in ("location", "product_id", "category")]
        anomalies = run_anomaly_detection(clean, daily, caps_mod.enabled(caps, "customer_rfm"), dims)

    progress("Building diagnostics and recommendations", 90)
    kpi28 = default_comparison(clean, daily)
    diag28 = default_diagnosis(clean, daily, caps, None if seg_table is None else seg_table["segment"])
    recs = build_recommendations(settings.currency, kpi28, diag28, forecast, customers, anomalies, report)

    out = {"quality": report, "capabilities": caps, "forecast": forecast, "customers": customers,
           "anomalies": anomalies, "recommendations": {"as_of": kpi28["as_of"], "items": recs,
                                                         "method": "Transparent rules over computed outputs; no language model."}}
    out = jsonable(out)
    with open(os.path.join(out_dir, "outputs.json"), "w") as f:
        json.dump(out, f)
    out["daily"] = daily
    progress("Done", 100)
    return out
