import numpy as np
import pandas as pd
import pytest

from app.core.ingest import IngestError, clean_transactions
from app.core.kpis import Period, compare_periods, compute_kpis, slice_period
from app.core.schema import IngestSettings, suggest_mapping
from app.core.timeseries import build_daily, model_series
from conftest import MAPPING, make_synthetic, tiny


def step(report, key):
    return next(s["rows"] for s in report["steps"] if s["key"] == key)


def test_cleaning_counts_every_removed_row():
    res = tiny([
        ["2024-03-01 10:00", "T1", "A", "X", 2, 5.0, "M"],
        ["2024-03-01 10:00", "T1", "A", "X", 2, 5.0, "M"],      # exact duplicate -> dropped
        ["2024-03-01 10:00", "T1", "A", "Y", 1, 3.0, "M"],
        ["not a date", "T2", "B", "X", 1, 5.0, "M"],            # invalid date -> dropped
        ["2024-03-02 11:00", "T3", None, "X", 1, 0.0, "M"],     # zero price -> not a sale
        ["2024-03-02 12:00", "T4", "B", "X", "abc", 5.0, "M"],  # non-numeric quantity
        ["2024-03-02 13:00", "CT1", "A", "X", -1, 5.0, "M"],    # return
        ["2024-03-02 14:00", "T5", None, "Y", 3, 3.0, "R"],     # no customer id
    ])
    r = res.report
    assert (step(r, "duplicate"), step(r, "invalid_date"), step(r, "non_sale"), step(r, "invalid_value")) == (1, 1, 1, 1)
    assert r["rows_raw"] == 8 and r["rows_clean"] == 4 and r["rows_returns"] == 1
    assert r["gross_sales"] == pytest.approx(10 + 3 + 9) and r["returns_value"] == pytest.approx(5)
    assert sum(s["rows"] for s in r["steps"]) + r["rows_clean"] == r["rows_raw"]


def test_returns_always_reduce_revenue_even_with_positive_quantity_and_prefix():
    res = tiny([["2024-03-01", "T1", "A", "X", 2, 5.0, "M"], ["2024-03-01", "C9", "A", "X", 1, 5.0, "M"]])
    assert compute_kpis(res.clean)["net_revenue"] == pytest.approx(5.0)


def test_non_product_lines_are_excluded_and_reported(synthetic_raw):
    s = IngestSettings(mapping=dict(MAPPING), currency="BHD", non_product_pattern="^P01$")
    res = clean_transactions(synthetic_raw, s)
    deduped = synthetic_raw.drop_duplicates()   # duplicates are removed first, so count on the de-duplicated file
    assert step(res.report, "non_product") == int((deduped["SKU"] == "P01").sum())
    assert "P01" not in set(res.clean["product_id"])


def test_mapping_validation_and_currency_is_never_assumed(synthetic_raw):
    cols = list(synthetic_raw.columns)
    assert IngestSettings(mapping={"timestamp": "Order Date"}, currency="BHD").validate(cols)       # no value column
    assert IngestSettings(mapping=dict(MAPPING), currency="").validate(cols)                         # currency required
    assert IngestSettings(mapping={**MAPPING, "amount": "Nope"}, currency="BHD").validate(cols)      # unknown column
    with pytest.raises(IngestError):
        clean_transactions(synthetic_raw, IngestSettings(mapping={"timestamp": "Order Date"}, currency="BHD"))


def test_mapping_suggestions_use_column_names():
    m = suggest_mapping(["InvoiceDate", "Invoice", "Customer ID", "StockCode", "Quantity", "Price", "Country"])
    assert m["timestamp"] == "InvoiceDate" and m["transaction_id"] == "Invoice" and m["unit_price"] == "Price"


def test_kpis_count_transactions_not_lines_and_identified_customers_only():
    res = tiny([
        ["2024-03-01", "T1", "A", "X", 2, 5.0, "M"], ["2024-03-01", "T1", "A", "Y", 1, 3.0, "M"],
        ["2024-03-01", "T2", None, "X", 1, 5.0, "M"], ["2024-03-02", "T3", "A", "X", 1, 5.0, "R"],
    ])
    k = compute_kpis(res.clean)
    assert k["transactions"] == 3 and k["active_customers"] == 1
    assert k["net_revenue"] == pytest.approx(23.0) and k["avg_transaction_value"] == pytest.approx(23.0 / 3)
    assert k["identified_revenue_share"] == pytest.approx(18 / 23)


def test_growth_not_reported_when_previous_period_predates_data(synthetic_clean):
    daily = build_daily(synthetic_clean)
    first = daily.index.min()
    out = compare_periods(synthetic_clean, daily, Period(first, first + pd.Timedelta(days=6)))
    assert out["previous"] is None and out["growth"]["net_revenue"] is None and out["notes"]
    later = Period(first + pd.Timedelta(days=28), first + pd.Timedelta(days=55))
    out = compare_periods(synthetic_clean, daily, later)
    prev = compute_kpis(slice_period(synthetic_clean, later.previous()))
    assert out["growth"]["net_revenue"] == pytest.approx(out["current"]["net_revenue"] / prev["net_revenue"] - 1)


def test_day_status_separates_closed_zero_gap_and_partial():
    raw = make_synthetic(days=120)
    ts = pd.to_datetime(raw["Order Date"])
    gap = (ts >= "2024-02-10") & (ts < "2024-02-15")       # 5-day hole -> gap
    one = ts.dt.normalize() == pd.Timestamp("2024-03-12")   # single missing Tuesday -> zero-sales day
    last = ts.dt.normalize() == ts.dt.normalize().max()
    raw = raw[~gap & ~one & ~(last & (ts.dt.hour >= 11))]   # final day stops at 11:00 -> partial
    clean = clean_transactions(raw, IngestSettings(mapping=dict(MAPPING), currency="BHD")).clean
    daily = build_daily(clean)
    assert set(daily.loc[daily.index.dayofweek == 4, "status"]) == {"closed"}
    assert daily.loc["2024-03-12", "status"] == "zero"
    assert (daily.loc["2024-02-10":"2024-02-14", "status"].isin(["gap", "closed"])).all()
    assert daily["status"].iloc[-1] == "partial"
    s = model_series(daily)
    assert np.isnan(s.loc["2024-02-12"]) and s.loc["2024-03-12"] == 0 and np.isnan(s.iloc[-1])
