"""KPI definitions used by every page (single source of truth).

net_revenue          = sum(sale line amounts) - sum(return line amounts) in the period
transactions         = number of DISTINCT sale transaction IDs (never line counts)
avg_transaction_value= net_revenue / transactions  (so revenue = transactions x ATV holds exactly)
active_customers     = distinct customer IDs with at least one sale line (identified customers only)
units                = sum of sale quantities
return_rate          = returns_value / gross_revenue
Growth compares a period with the immediately preceding period of equal length. Periods end on
the last COMPLETE day; a previous period that starts before the data does is not compared.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Period:
    start: pd.Timestamp
    end: pd.Timestamp  # inclusive

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> "Period":
        return Period(self.start - pd.Timedelta(days=self.days), self.start - pd.Timedelta(days=1))

    def to_dict(self) -> dict:
        return {"start": self.start.date().isoformat(), "end": self.end.date().isoformat(), "days": self.days}


def trailing_period(as_of: pd.Timestamp, days: int) -> Period:
    as_of = pd.Timestamp(as_of).normalize()
    return Period(as_of - pd.Timedelta(days=days - 1), as_of)


def slice_period(clean: pd.DataFrame, p: Period) -> pd.DataFrame:
    return clean[(clean["date"] >= p.start) & (clean["date"] <= p.end)]


def compute_kpis(rows: pd.DataFrame) -> dict:
    sales = rows[rows["row_type"] == "sale"]
    gross = float(sales["amount"].sum())
    returns_value = float(-rows.loc[rows["row_type"] == "return", "amount"].sum())
    net = gross - returns_value
    txns = int(sales["transaction_id"].nunique())
    ident = sales[sales["customer_id"].notna()]
    customers = int(ident["customer_id"].nunique())
    has_units = sales["quantity"].notna().any()
    return {
        "net_revenue": net,
        "gross_revenue": gross,
        "returns_value": returns_value,
        "return_rate": (returns_value / gross) if gross else None,
        "transactions": txns,
        "avg_transaction_value": (net / txns) if txns else None,
        "active_customers": customers,
        "identified_revenue_share": (float(ident["amount"].sum()) / gross) if gross else None,
        "units": float(sales["quantity"].sum()) if has_units else None,
    }


def growth(cur, prev):
    if cur is None or prev is None or prev == 0 or (isinstance(prev, float) and np.isnan(prev)):
        return None
    return (cur - prev) / abs(prev)


def period_coverage(daily: pd.DataFrame, p: Period) -> dict:
    d = daily[(daily.index >= p.start) & (daily.index <= p.end)]
    counts = d["status"].value_counts().to_dict()
    return {
        "days_in_data": int(len(d)),
        "days_expected": p.days,
        "observed": int(counts.get("observed", 0)),
        "gap": int(counts.get("gap", 0)),
        "closed": int(counts.get("closed", 0)),
        "zero": int(counts.get("zero", 0)),
        "partial": int(counts.get("partial", 0)),
    }


def compare_periods(clean: pd.DataFrame, daily: pd.DataFrame, current: Period, previous: Period | None = None) -> dict:
    previous = previous or current.previous()
    cur = compute_kpis(slice_period(clean, current))
    notes = []
    data_start = daily.index.min()
    comparable = previous.start >= data_start
    if not comparable:
        notes.append("The previous period starts before the first day of data, so growth is not shown.")
    prev = compute_kpis(slice_period(clean, previous)) if comparable else None
    cov_c, cov_p = period_coverage(daily, current), period_coverage(daily, previous)
    if cov_c["partial"]:
        notes.append("The current period includes an incomplete final day.")
    if cov_c["gap"] or (comparable and cov_p["gap"]):
        notes.append(f"Days without data (closure or missing export): {cov_c['gap']} in the current period, "
                     f"{cov_p['gap']} in the previous one. Growth partly reflects this difference.")
    deltas = {k: growth(cur[k], prev[k]) if prev else None for k in cur}
    return {
        "current_period": current.to_dict(), "previous_period": previous.to_dict(),
        "current": cur, "previous": prev, "growth": deltas,
        "coverage": {"current": cov_c, "previous": cov_p}, "notes": notes,
    }


def breakdown(rows: pd.DataFrame, dimension: str, top: int = 10) -> list[dict]:
    """Net revenue and distinct sale transactions by a dimension, top-N plus 'Other'."""
    if dimension not in rows.columns or rows[dimension].isna().all():
        return []
    key = rows[dimension].fillna("(not recorded)")
    net = rows.groupby(key)["amount"].sum().sort_values(ascending=False)
    sales = rows[rows["row_type"] == "sale"]
    tx = sales.groupby(sales[dimension].fillna("(not recorded)"))["transaction_id"].nunique()
    total = float(net.sum())
    out = [{"key": str(k), "net_revenue": float(v), "transactions": int(tx.get(k, 0)),
            "share": float(v / total) if total else None} for k, v in net.head(top).items()]
    if len(net) > top:
        rest = float(net.iloc[top:].sum())
        out.append({"key": f"Other ({len(net) - top})", "net_revenue": rest, "transactions": None,
                    "share": rest / total if total else None})
    return out


def resample_series(daily: pd.DataFrame, freq: str = "D") -> pd.DataFrame:
    """Aggregate additive daily metrics to W/M. Distinct counts (transactions per day, customers per
    day) are not additive across days, so only additive metrics are resampled here."""
    d = daily[daily["status"] != "partial"]
    if freq == "D":
        return d
    rule = {"W": "W-SUN", "M": "MS"}[freq]
    agg = d[["net_revenue", "gross_revenue", "returns_value", "transactions", "units"]].resample(rule).sum(min_count=1)
    agg["days_with_sales"] = (d["status"] == "observed").resample(rule).sum()
    agg["days_gap"] = (d["status"] == "gap").resample(rule).sum()
    return agg
