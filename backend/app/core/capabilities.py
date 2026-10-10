"""Capability registry: which modules this dataset can support, and why not when it cannot."""
from __future__ import annotations

MIN_CUSTOMERS_RFM = 50
MIN_CUSTOMERS_KMEANS = 200
MIN_DAYS_ANOMALY = 70
MIN_DAYS_DIAGNOSTICS = 14


def _cap(key, label, ok, reason="", kind="core"):
    return {"key": key, "label": label, "enabled": bool(ok), "reason": "" if ok else reason, "kind": kind}


def build_capabilities(report: dict, day_summary: dict, forecast_horizons: dict | None = None) -> list[dict]:
    f = set(report["mapped_fields"])
    days = day_summary["days_total"]
    n_cust = report.get("customers_identified", 0)
    has_cust = "customer_id" in f and n_cust > 0
    caps = [
        _cap("kpis", "Executive KPIs", True),
        _cap("units", "Units sold", "quantity" in f, "No quantity column was mapped."),
        _cap("returns", "Returns analysis", report.get("rows_returns", 0) > 0,
             "No return or cancellation rows were found in this file."),
        _cap("customer_rfm", "Customer RFM segments", has_cust and n_cust >= MIN_CUSTOMERS_RFM,
             "No customer ID column was mapped." if "customer_id" not in f
             else f"Only {n_cust} identified customers; at least {MIN_CUSTOMERS_RFM} are needed."),
        _cap("customer_kmeans", "K-Means customer clusters", has_cust and n_cust >= MIN_CUSTOMERS_KMEANS,
             "No customer ID column was mapped." if "customer_id" not in f
             else f"Only {n_cust} identified customers; at least {MIN_CUSTOMERS_KMEANS} are needed."),
        _cap("anomalies", "Anomaly detection", days >= MIN_DAYS_ANOMALY,
             f"Needs at least {MIN_DAYS_ANOMALY} days of history; this file covers {days}."),
        _cap("diagnostics", "Revenue diagnostics", days >= MIN_DAYS_DIAGNOSTICS,
             f"Needs at least {MIN_DAYS_DIAGNOSTICS} days of history; this file covers {days}."),
        _cap("dim_product", "Breakdown by product", "product_id" in f, "No product column was mapped."),
        _cap("dim_category", "Breakdown by category", "category" in f, "No category column was mapped."),
        _cap("dim_location", "Breakdown by location", "location" in f, "No location column was mapped."),
        _cap("dim_channel", "Breakdown by channel", "channel" in f, "No channel column was mapped."),
    ]
    for h in (7, 30, 90):
        info = (forecast_horizons or {}).get(str(h))
        if info is None:
            caps.append(_cap(f"forecast_{h}", f"{h}-day forecast", False, "Forecasting has not been run yet."))
        else:
            caps.append(_cap(f"forecast_{h}", f"{h}-day forecast", info.get("enabled"), info.get("reason", "")))
    roadmap = "Not part of this MVP (see the roadmap in the documentation)."
    caps += [
        _cap("churn_model", "Supervised churn prediction", False, roadmap, "roadmap"),
        _cap("predicted_clv", "Predicted customer lifetime value", False, roadmap, "roadmap"),
        _cap("stockout", "Stock-out prediction", False,
             "Needs inventory levels and supplier lead times, which this file does not contain. " + roadmap, "roadmap"),
        _cap("ask", "Ask Your Business (natural language)", False, roadmap, "roadmap"),
    ]
    return caps


def enabled(caps: list[dict], key: str) -> bool:
    return any(c["key"] == key and c["enabled"] for c in caps)


def available_dimensions(caps: list[dict]) -> list[str]:
    m = {"dim_product": "product_id", "dim_category": "category", "dim_location": "location", "dim_channel": "channel"}
    return [col for key, col in m.items() if enabled(caps, key)]
