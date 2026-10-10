import numpy as np
import pandas as pd
import pytest

from app.analytics.diagnostics import diagnose, shapley_product
from app.analytics.recommendations import build_recommendations
from app.core.kpis import Period, compute_kpis, slice_period
from app.core.timeseries import build_daily
from app.ml.anomalies import Z_ALERT, baseline, detect_statistical
from app.ml.segments import add_scores, rfm_table, rule_segments, segment_customers
from app.pipeline import default_comparison
from conftest import tiny


def test_shapley_reconciles_exactly_including_zero_factors():
    rng = np.random.default_rng(0)
    for _ in range(50):
        a = dict(zip("xyz", rng.uniform(0, 100, 3)))
        b = dict(zip("xyz", rng.uniform(0, 100, 3)))
        assert sum(shapley_product(a, b).values()) == pytest.approx(np.prod(list(b.values())) - np.prod(list(a.values())))
    assert sum(shapley_product({"n": 0, "v": 0}, {"n": 10, "v": 5}).values()) == pytest.approx(50)


def test_diagnosis_reconciles_to_total_change(synthetic_clean):
    start = synthetic_clean["date"].min()
    a = Period(start + pd.Timedelta(days=30), start + pd.Timedelta(days=57))
    b = Period(start + pd.Timedelta(days=58), start + pd.Timedelta(days=85))
    seg = segment_customers(synthetic_clean, b.end + pd.Timedelta(days=1))["segment"]
    d = diagnose(synthetic_clean, a, b, ["product_id", "category", "location"], True, seg)
    total = compute_kpis(slice_period(synthetic_clean, b))["net_revenue"] - compute_kpis(slice_period(synthetic_clean, a))["net_revenue"]
    assert d["total_change"] == pytest.approx(total)
    for block in (d["two_factor"], d["three_factor"]):
        assert sum(e["effect"] for e in block["effects"]) == pytest.approx(total, abs=1e-6)
    for name, dim in d["dimensions"].items():
        if name == "weekday":
            assert sum(r["change"] for r in dim["rows"]) == pytest.approx(total, abs=1e-6)
        else:  # shown groups + remainder = total, and falls + rises = total
            shown = sum(r["change"] for r in dim["top_negative"] + dim["top_positive"]) + dim["other"]["change"]
            assert shown == pytest.approx(total, abs=1e-6)
            assert dim["sum_negative"] + dim["sum_positive"] == pytest.approx(total, abs=1e-6)


def test_rfm_values_by_hand_and_cutoff_is_respected():
    res = tiny([
        ["2024-01-01", "T1", "A", "X", 2, 10.0, "M"], ["2024-01-10", "T2", "A", "X", 1, 10.0, "M"],
        ["2024-01-10", "T2", "A", "Y", 1, 4.0, "M"], ["2024-01-12", "C1", "A", "X", -1, 10.0, "M"],
        ["2024-01-05", "T3", "B", "X", 1, 10.0, "M"], ["2024-02-01", "T4", "B", "X", 5, 10.0, "M"],  # after cutoff
    ])
    rfm = rfm_table(res.clean, pd.Timestamp("2024-01-20"))
    assert rfm.loc["A", ["recency_days", "frequency", "monetary"]].tolist() == [10, 2, 24.0]
    assert rfm.loc["B", ["recency_days", "frequency", "monetary"]].tolist() == [15, 1, 10.0]


def test_scores_share_ties_and_segments_follow_the_rules():
    rfm = pd.DataFrame({"recency_days": [1, 5, 50, 300, 300], "frequency": [1, 1, 1, 1, 9], "monetary": [10, 10, 10, 10, 900]})
    s = add_scores(rfm)
    assert s["f_score"].iloc[:4].nunique() == 1 and s["r_score"].iloc[3] == s["r_score"].iloc[4]
    assert s["r_score"].iloc[0] == 5 and s["m_score"].iloc[4] == 5
    seg = rule_segments([5, 3, 2, 1, 5], [5, 3, 4, 1, 5], [5, 2, 2, 1, 5], [400, 400, 400, 400, 30])
    assert seg.tolist() == ["High-value", "Loyal", "At-risk", "Dormant", "New"]


def test_anomaly_detector_ignores_weekly_pattern_and_catches_a_large_shock(synthetic_clean):
    daily = build_daily(synthetic_clean)
    alerts, _ = detect_statistical(daily, ["transactions"])
    weekend_share = np.mean([pd.Timestamp(a["date"]).dayofweek in (5, 6) for a in alerts]) if alerts else 0
    assert len(alerts) <= 0.03 * len(daily) and weekend_share < 1  # busier weekends are not flagged as such
    shocked = daily.copy()
    day = shocked.index[(shocked["status"] == "observed")][-20]
    shocked.loc[day, "transactions"] *= 6
    z = baseline(shocked["transactions"].astype(float), shocked["status"]).loc[day, "z"]
    assert z >= Z_ALERT
    past = baseline(daily["transactions"].astype(float), daily["status"])
    assert past.loc[:day].iloc[:-1]["expected"].equals(baseline(shocked["transactions"].astype(float), shocked["status"]).loc[:day].iloc[:-1]["expected"])


def test_recommendation_evidence_is_copied_from_computed_outputs(synthetic_clean):
    daily = build_daily(synthetic_clean)
    kpi = default_comparison(synthetic_clean, daily)
    customers = {"customers": 300, "cutoff": "2024-09-17", "top10_customer_revenue_share": 0.42,
                 "out_of_time": {"as_of": "2024-06-19", "window_days": 90, "valuable_then": 100, "valuable_slipped": 25,
                                 "valuable_slipped_prior_value": 1234.0, "by_segment": [{"segment": "At-risk", "repurchase_rate": 0.2}]}}
    quality = {"mapped_fields": ["customer_id"], "customer_id_coverage_revenue": 0.6, "customer_id_coverage_rows": 0.55}
    recs = {r["id"]: r for r in build_recommendations("BHD", kpi, None, None, customers, None, quality)}
    ev = lambda r: {e["label"]: e["value"] for e in recs[r]["evidence"]}
    assert "25 (25.0%)" in ev("retention_outreach").values() and "BHD 1,234" in ev("retention_outreach").values()
    assert recs["retention_outreach"]["priority"] == "high"
    assert ev("customer_concentration")["Top-10 share of historical identified revenue"] == "42.0%"
    assert ev("capture_customer_ids")["Revenue with a customer ID"] == "60.0%"
    for r in recs.values():
        assert r["evidence"] and r["action"] and r["uncertainty"] and "priority" in r
    # no trigger -> no recommendation, never a filler one
    quiet = build_recommendations("BHD", {**kpi, "growth": {}, "previous": None}, None, None, None, None,
                                  {"mapped_fields": [], "customer_id_coverage_revenue": None})
    assert quiet == []
