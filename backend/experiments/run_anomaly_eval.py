"""Anomaly-detector evaluation with INJECTED (synthetic, clearly labelled) shocks. Reproduce with:
    python backend/experiments/run_anomaly_eval.py
The real data has no anomaly labels, so this measures only whether artificial shocks of known size
are recovered. It says nothing about precision on real incidents. Writes results/anomaly_eval.json.
"""
import numpy as np
import pandas as pd

from common import load_case_study, save_json
from app.ml.anomalies import Z_ALERT, baseline, detect_isolation_forest, detect_statistical, compare_methods

FACTORS = [0.3, 0.5, 0.7, 1.5, 2.0, 3.0]
METRICS = ["net_revenue", "transactions", "customers"]
N_SEEDS, N_INJECT = 30, 10


def flags(daily):
    _, tables = detect_statistical(daily, METRICS)
    iso = detect_isolation_forest(tables)
    stat = pd.Series(False, index=daily.index)
    for t in tables.values():
        stat |= t["z"].abs() >= Z_ALERT
    return stat, iso["flag"].reindex(daily.index).fillna(False).astype(bool), tables, iso


if __name__ == "__main__":
    clean, daily, _ = load_case_study()
    daily = daily[daily["status"] != "partial"]
    stat0, iso0, tables, iso = flags(daily)
    scored = baseline(daily["net_revenue"].astype(float), daily["status"])["z"].notna()
    candidates = daily.index[(daily["status"] == "observed") & scored & ~stat0 & ~iso0]
    out = {"description": "Recall on artificially injected shocks; factor multiplies the real value on the injected day.",
           "seeds": N_SEEDS, "injections_per_seed": N_INJECT, "candidate_days": int(len(candidates)),
           "unmodified_data": compare_methods(tables, iso) | {"scored_days": int(scored.sum())},
           "scenarios": {}}
    for scenario, cols in {"all_metrics_shift": METRICS, "revenue_only_shift": ["net_revenue"]}.items():
        rows = []
        for f in FACTORS:
            rs, ri, extra_s, extra_i = [], [], [], []
            for seed in range(N_SEEDS):
                rng = np.random.default_rng(seed)
                days = rng.choice(candidates, N_INJECT, replace=False)
                d = daily.copy()
                d.loc[days, cols] = d.loc[days, cols] * f
                s, i, _, _ = flags(d)
                rs.append(s.loc[days].mean()); ri.append(i.loc[days].mean())
                # days flagged only after injection (knock-on effects on later baselines)
                extra_s.append(int((s & ~stat0).drop(days).sum())); extra_i.append(int((i & ~iso0).drop(days).sum()))
            rows.append({"factor": f, "recall_statistical": float(np.mean(rs)), "recall_isolation_forest": float(np.mean(ri)),
                         "extra_flags_statistical": float(np.mean(extra_s)), "extra_flags_isolation_forest": float(np.mean(extra_i))})
            print(scenario, rows[-1], flush=True)
        out["scenarios"][scenario] = rows
    print(save_json("anomaly_eval.json", out))
