"""Customer segmentation experiment. Reproduce with:
    python backend/experiments/run_segmentation.py
Writes results/segmentation.json (aggregates only; no customer IDs).
"""
import pandas as pd

from common import load_case_study, save_json
from app.core.timeseries import last_complete_day
from app.ml.segments import run_customer_intelligence
from app.pipeline import jsonable

if __name__ == "__main__":
    clean, daily, _ = load_case_study()
    cutoff = last_complete_day(daily) + pd.Timedelta(days=1)
    res = run_customer_intelligence(clean, cutoff, "GBP")
    res.pop("table")
    print(save_json("segmentation.json", jsonable(res)))
    for s in res["segments"]:
        print(s["segment"], s["customers"], round(s["revenue_share"], 3))
    print(res["kmeans"]["k"], res["kmeans"]["agreement_with_rfm_ari"], res["kmeans"]["diagnostics"])
