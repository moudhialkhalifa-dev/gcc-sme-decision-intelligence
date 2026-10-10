"""Rule-based recommendations. Every rule reads numbers already computed by another module and
copies them into the recommendation as evidence, so each one can be reproduced and audited.
No language model is involved. Actions are suggestions to consider, not optimal or guaranteed.
"""
from __future__ import annotations

REVENUE_MOVE = 0.05          # a +/-5% change between 28-day periods counts as a move
CONCENTRATION_SHARE = 0.30   # one group explaining 30%+ of a decline
SLIPPED_SHARE = 0.10         # 10%+ of previously valuable customers now inactive
TOP10_SHARE = 0.15           # top 10 customers above 15% of identified revenue
FORECAST_MOVE = 0.10         # forecast vs recent actuals
RETURN_RATE_RISE = 0.02      # +2 percentage points
MISSING_ID_REVENUE = 0.15    # 15%+ of revenue without customer ID
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _money(cur: str, v: float) -> str:
    return f"{cur} {v:,.0f}"


def _pct(v: float) -> str:
    return f"{v:+.1%}"


def _rec(id_, title, action, priority, evidence, uncertainty, assumptions, missing, sources):
    return {"id": id_, "title": title, "action": action, "priority": priority,
            "evidence": [{"label": k, "value": v} for k, v in evidence],
            "uncertainty": uncertainty, "assumptions": assumptions, "missing_information": missing,
            "source_modules": sources}


def build_recommendations(currency: str, kpi28: dict, diag28: dict | None, forecast: dict | None,
                          customers: dict | None, anomalies: dict | None, quality: dict) -> list[dict]:
    recs: list[dict] = []
    g = (kpi28 or {}).get("growth") or {}
    periods = f"{kpi28['current_period']['start']} to {kpi28['current_period']['end']} vs the previous {kpi28['current_period']['days']} days"
    rev_g = g.get("net_revenue")
    gap_note = " ".join(n for n in kpi28.get("notes", []) if "without data" in n)

    # 1-2: revenue decline - where it shows up, and whether it is volume or basket size
    if rev_g is not None and rev_g <= -REVENUE_MOVE and diag28:
        total = diag28["total_change"]
        two = {e["factor"]: e["effect"] for e in diag28["two_factor"]["effects"]}
        tx_eff, atv_eff = two["Number of transactions"], two["Average transaction value"]
        main = "fewer transactions" if tx_eff <= atv_eff else "lower value per transaction"
        action = ("Check what reduced order volume in this period (trading days, marketing activity, stock availability) "
                  "before changing prices." if main == "fewer transactions" else
                  "Review basket size: pricing, discounts and product mix in this period.")
        recs.append(_rec(
            "revenue_decline_driver", f"Revenue fell {abs(rev_g):.1%}, mainly through {main}", action,
            "high" if rev_g <= -0.15 else "medium",
            [("Periods compared", periods), ("Change in net revenue", f"{_money(currency, total)} ({_pct(rev_g)})"),
             ("Effect of transaction count", _money(currency, tx_eff)), ("Effect of average transaction value", _money(currency, atv_eff))],
            "The split is exact arithmetic, but it shows where the change appears, not why. " + gap_note,
            ["Both periods are comparable trading periods."],
            ["Marketing calendar", "Price or promotion changes", "Stock availability"], ["kpis", "diagnostics"]))
        for dim, label in (("location", "location"), ("category", "category"), ("product_id", "product")):
            d = diag28["dimensions"].get(dim)
            if not d or not d["top_negative"] or d["sum_negative"] == 0:
                continue
            top = d["top_negative"][0]
            share_of_decline = top["change"] / d["sum_negative"]
            if share_of_decline >= CONCENTRATION_SHARE and total < 0:
                recs.append(_rec(
                    f"decline_concentrated_{dim}", f"Review {top['label']}: largest falling {label}",
                    f"Look into {top['label']} first: it accounts for {share_of_decline:.0%} of all {label}-level declines in this period.",
                    "high" if share_of_decline >= 0.5 else "medium",
                    [("Periods compared", periods), (f"{top['label']} revenue", f"{_money(currency, top['before'])} -> {_money(currency, top['after'])}"),
                     ("Its change", _money(currency, top["change"])), (f"Sum of all falling {label}s", _money(currency, d["sum_negative"])),
                     (f"Sum of all rising {label}s (offset)", _money(currency, d["sum_positive"])), ("Net change", _money(currency, total))],
                    "A contribution is not a cause. One large order moving between periods can produce this pattern.",
                    [f"The {label} field is recorded consistently across both periods."],
                    ["Local events, stock or staffing information for this " + label], ["diagnostics"]))
                break
    elif rev_g is not None and rev_g >= REVENUE_MOVE and diag28:
        two = {e["factor"]: e["effect"] for e in diag28["two_factor"]["effects"]}
        recs.append(_rec(
            "revenue_growth_driver", f"Revenue rose {rev_g:.1%}: confirm what is driving it",
            "Identify whether this is seasonal or the result of a specific action, so that it can be repeated or planned for.",
            "low",
            [("Periods compared", periods), ("Change in net revenue", f"{_money(currency, diag28['total_change'])} ({_pct(rev_g)})"),
             ("Effect of transaction count", _money(currency, two["Number of transactions"])),
             ("Effect of average transaction value", _money(currency, two["Average transaction value"]))],
            "Growth between adjacent periods often reflects seasonality rather than a lasting change. " + gap_note,
            [], ["At least one earlier year of data for the same weeks to separate seasonality"], ["kpis", "diagnostics"]))

    # 3: valuable customers going quiet
    oot = (customers or {}).get("out_of_time")
    if oot and oot["valuable_then"]:
        share = oot["valuable_slipped"] / oot["valuable_then"]
        if share >= SLIPPED_SHARE:
            rates = {r["segment"]: r["repurchase_rate"] for r in oot["by_segment"]}
            recs.append(_rec(
                "retention_outreach", f"{oot['valuable_slipped']:,} previously valuable customers have gone quiet",
                "Consider a retention contact (reminder, account call or offer) for customers who were High-value or Loyal "
                f"{oot['window_days']} days ago and are now At-risk or Dormant.",
                "high" if share >= 0.2 else "medium",
                [("High-value or Loyal customers on " + oot["as_of"], f"{oot['valuable_then']:,}"),
                 ("Of those, now At-risk or Dormant", f"{oot['valuable_slipped']:,} ({share:.1%})"),
                 ("Their historical spend before " + oot["as_of"], _money(currency, oot["valuable_slipped_prior_value"])),
                 (f"Share of At-risk customers who bought again within {oot['window_days']} days (historical)",
                  f"{rates.get('At-risk', 0):.0%}")],
                "Segments are rule-based inactivity labels, not churn probabilities. Some of these customers buy seasonally "
                "and may return without any action.",
                ["Customer IDs are stable over time."],
                ["Contact permissions", "Cost and expected response of an outreach", "Whether an outreach test group is possible"],
                ["customers"]))

    # 4: dependence on a few customers
    top10 = (customers or {}).get("top10_customer_revenue_share")
    if top10 is not None and top10 >= TOP10_SHARE:
        recs.append(_rec(
            "customer_concentration", f"Top 10 customers bring {top10:.0%} of identified revenue",
            "Review account coverage for the largest customers and consider how exposed revenue is to losing one of them.",
            "medium" if top10 < 0.3 else "high",
            [("Top-10 share of historical identified revenue", f"{top10:.1%}"),
             ("Identified customers", f"{customers['customers']:,}"), ("Cutoff date", customers["cutoff"])],
            "Based on historical spend of customers with an ID only.",
            ["Each customer ID is one real customer."], ["Contract or relationship status of these accounts"], ["customers"]))

    # 5: forecast versus recent run-rate
    fh = ((forecast or {}).get("horizons") or {}).get("30")
    if fh and fh.get("enabled") and kpi28["current"]["net_revenue"]:
        recent30 = kpi28.get("recent30_net_revenue")
        if recent30:
            change = fh["forecast_total"] / recent30 - 1
            test = next((c["test"] for c in fh["comparison"] if c["model"] == fh["selected_model"]), {})
            if abs(change) >= FORECAST_MOVE:
                up = change > 0
                recs.append(_rec(
                    "forecast_shift", f"Next 30 days forecast {abs(change):.0%} {'above' if up else 'below'} the last 30 days",
                    ("Plan capacity and purchasing for higher demand, after checking the forecast against what you know is coming."
                     if up else "Plan for lower revenue: review planned spending and check whether a seasonal slowdown is expected."),
                    "medium",
                    [("Forecast total, next 30 days", _money(currency, fh["forecast_total"])),
                     ("Actual total, last 30 days", _money(currency, recent30)),
                     ("Model", fh["selected_label"]),
                     ("Test error of 30-day totals (average)", f"{test.get('total_ape_mean', 0):.0%}" if test.get("total_ape_mean") is not None else "n/a"),
                     ("Daily test WMAPE", f"{test.get('wmape', 0):.0%}" if test.get("wmape") is not None else "n/a")],
                    "The forecast has no knowledge of holidays, closures or promotions. Typical error of a 30-day total in "
                    "back-testing is shown above; the real figure can fall outside it.",
                    ["Trading pattern continues as in the recent past."],
                    ["Inventory levels and supplier lead times (needed before making a stock recommendation)", "Holiday and promotion calendar"],
                    ["forecasting", "kpis"]))

    # 6: recent high-severity anomalies
    if anomalies:
        end = kpi28["current_period"]["end"]
        recent = [a for a in anomalies["alerts"] if a["severity"] in ("high", "medium") and _days_between(a["date"], end) <= 14]
        if recent:
            a = max(recent, key=lambda x: abs(x["z"]))
            recs.append(_rec(
                "investigate_anomaly", f"Check {a['metric_label'].lower()} on {a['date']}: {a['deviation_pct']:+.0%} vs expected",
                "Confirm whether this day reflects a real event (large order, promotion, outage) or a data problem.",
                "high" if a["severity"] == "high" else "medium",
                [("Actual", f"{a['actual']:,.0f}"), ("Expected", f"{a['expected']:,.0f}"),
                 ("Expected range", f"{a['expected_lo']:,.0f} to {a['expected_hi']:,.0f}"), ("Robust z-score", f"{a['z']:.1f}"),
                 ("Unusual days (medium or high) in the last 14 days", str(len({x['date'] for x in recent})))],
                "The detector has not been validated against labelled incidents; seasonal ramps can trigger it.",
                [], ["Operational log for that day"], ["anomalies"]))

    # 7: rising return rate
    cur, prev = kpi28["current"], kpi28.get("previous")
    if prev and cur.get("return_rate") is not None and prev.get("return_rate") is not None:
        rise = cur["return_rate"] - prev["return_rate"]
        if rise >= RETURN_RATE_RISE:
            recs.append(_rec(
                "returns_rising", f"Return rate rose to {cur['return_rate']:.1%}",
                "Review which products and customers account for the returns in this period.",
                "medium",
                [("Periods compared", periods), ("Return rate now", f"{cur['return_rate']:.1%}"),
                 ("Return rate before", f"{prev['return_rate']:.1%}"), ("Returns value now", _money(currency, cur["returns_value"]))],
                "A single large cancellation can move this rate.", ["Returns are dated when recorded, not when the original sale happened."],
                ["Return reasons"], ["kpis"]))

    # 8: data quality limiting the analysis
    cov = quality.get("customer_id_coverage_revenue")
    if "customer_id" in quality.get("mapped_fields", []) and cov is not None and (1 - cov) >= MISSING_ID_REVENUE:
        recs.append(_rec(
            "capture_customer_ids", f"{1 - cov:.0%} of revenue has no customer ID",
            "Capture a customer identifier on more sales so customer analysis covers more of the business.", "low",
            [("Revenue with a customer ID", f"{cov:.1%}"), ("Sales rows with a customer ID", f"{quality['customer_id_coverage_rows']:.1%}")],
            "Customer segments describe identified customers only and may not represent anonymous buyers.", [], [], ["data_quality"]))

    recs.sort(key=lambda r: PRIORITY_ORDER[r["priority"]])
    return recs


def _days_between(a: str, b: str) -> int:
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days
