"""Shared loader for experiment scripts: raw CSV -> cleaned table -> daily table (cached as parquet)."""
import json
import os
import sys

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app.core.ingest import clean_transactions, read_raw_csv  # noqa: E402
from app.core.schema import IngestSettings  # noqa: E402
from app.core.timeseries import build_daily  # noqa: E402

RAW = os.path.join(ROOT, "data", "raw", "online_retail_II.csv")
CONFIG = os.path.join(ROOT, "configs", "online_retail_ii.json")
RESULTS = os.path.join(ROOT, "results")


def load_case_study():
    settings = IngestSettings.from_dict(json.load(open(CONFIG)))
    res = clean_transactions(read_raw_csv(RAW), settings)
    daily = build_daily(res.clean)
    return res.clean, daily, res.report


def save_json(name: str, obj) -> str:
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, name)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, default=str)
    return path
