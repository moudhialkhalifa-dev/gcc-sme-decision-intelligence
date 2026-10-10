"""Test fixtures. All data here is SYNTHETIC, generated for tests only; it is never used as evidence."""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="smedi-test-")
os.environ.update({"DATABASE_URL": f"sqlite:///{_tmp}/test.db", "DATA_DIR": _tmp, "JOBS_SYNC": "1",
                   "MAX_UPLOAD_MB": "5", "API_KEY": "", "STATIC_DIR": ""})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app.core.ingest import clean_transactions  # noqa: E402
from app.core.schema import IngestSettings  # noqa: E402

MAPPING = {"timestamp": "Order Date", "transaction_id": "Receipt", "customer_id": "Member", "product_id": "SKU",
           "quantity": "Qty", "unit_price": "Unit Price", "category": "Category", "location": "Branch"}


def make_synthetic(days: int = 260, seed: int = 1, with_customers: bool = True) -> pd.DataFrame:
    """Synthetic shop: closed on Fridays, busier at weekends, two branches, 300 members."""
    rng = np.random.default_rng(seed)
    rows, receipt = [], 0
    start = pd.Timestamp("2024-01-01")
    for d in range(days):
        day = start + pd.Timedelta(days=d)
        if day.dayofweek == 4:
            continue
        n_tx = rng.poisson(14 * (1.5 if day.dayofweek in (5, 6) else 1.0))
        for _ in range(n_tx):
            receipt += 1
            cust = f"M{rng.integers(1, 301):04d}" if rng.random() < 0.8 else ""
            ts = day + pd.Timedelta(hours=int(rng.integers(9, 21)), minutes=int(rng.integers(0, 60)))
            for _ in range(int(rng.integers(1, 4))):
                sku = int(rng.integers(1, 21))
                rows.append({"Order Date": ts.strftime("%Y-%m-%d %H:%M:%S"), "Receipt": f"R{receipt}",
                             "Member": cust if with_customers else "", "SKU": f"P{sku:02d}",
                             "Qty": int(rng.integers(1, 5)), "Unit Price": round(1.5 + sku * 0.4, 3),
                             "Category": "Drinks" if sku <= 10 else "Food", "Branch": "Manama" if rng.random() < 0.6 else "Riffa"})
    df = pd.DataFrame(rows).astype(str)
    if not with_customers:
        df = df.drop(columns=["Member"])
    return df


@pytest.fixture(scope="session")
def synthetic_raw():
    return make_synthetic()


@pytest.fixture(scope="session")
def settings():
    return IngestSettings(mapping=dict(MAPPING), currency="BHD", timezone="Asia/Bahrain")


@pytest.fixture(scope="session")
def synthetic_clean(synthetic_raw, settings):
    return clean_transactions(synthetic_raw, settings).clean


def tiny(rows):
    """Hand-written rows -> cleaned table. Columns: date, txn, cust, sku, qty, price, branch."""
    raw = pd.DataFrame(rows, columns=["d", "t", "c", "s", "q", "p", "b"]).astype(str).replace({"None": None, "nan": None})
    s = IngestSettings(mapping={"timestamp": "d", "transaction_id": "t", "customer_id": "c", "product_id": "s",
                                "quantity": "q", "unit_price": "p", "location": "b"}, currency="BHD", return_prefixes=["C"])
    return clean_transactions(raw, s)
