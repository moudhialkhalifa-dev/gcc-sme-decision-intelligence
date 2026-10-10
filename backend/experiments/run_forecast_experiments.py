"""Forecast experiment on the case study. Reproduce with:
    python backend/experiments/run_forecast_experiments.py
Writes results/forecast_experiments.json and results/forecast_backtests.csv.
"""
import itertools
import time

import pandas as pd

from common import load_case_study, save_json, RESULTS
from app.core.timeseries import model_series
from app.ml.forecasting import run_forecasting

SARIMA_GRID = [{"order": list(o), "seasonal_order": list(s), "log": lg}
               for o, s, lg in itertools.product([(1, 0, 1), (0, 1, 1), (2, 0, 0)],
                                                 [(0, 1, 1, 7), (1, 0, 1, 7)], [True, False])]
XGB_GRID = [{"max_depth": d, "learning_rate": lr, "n_estimators": n, "subsample": 0.8,
             "colsample_bytree": 0.8, "min_child_weight": 5}
            for d, lr, n in itertools.product([2, 4, 6], [0.03, 0.08], [150, 300, 600])]

if __name__ == "__main__":
    t0 = time.time()
    clean, daily, _ = load_case_study()
    daily = daily[daily["status"] != "partial"]
    res = run_forecasting(model_series(daily), daily["status"],
                          {"seasonal_naive": [None], "sarima": SARIMA_GRID, "xgboost": XGB_GRID},
                          progress=lambda m: print(f"[{time.time() - t0:6.0f}s] {m}", flush=True))
    bts = res.pop("backtests")
    frames = [df.assign(model=m, split=split) for split, d in bts.items() for m, df in d.items()]
    pd.concat(frames).to_csv(f"{RESULTS}/forecast_backtests.csv", index=False)
    res["target"] = "daily net revenue (GBP)"
    res["grids"] = {"sarima": SARIMA_GRID, "xgboost": XGB_GRID}
    res["runtime_seconds"] = round(time.time() - t0, 1)
    print(save_json("forecast_experiments.json", res))
    for h, e in res["horizons"].items():
        print(h, e.get("selected_model"), e.get("enabled"), e.get("reason"), e.get("interval", {}).get("test_coverage"))
        for r in e.get("comparison", []):
            print("   ", r["model"], "val", r["validation"]["wmape"], "test", r["test"]["wmape"], r["test"]["mae"])
