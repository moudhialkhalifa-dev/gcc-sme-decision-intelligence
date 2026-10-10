"""Daily metric table with an explicit status for every calendar day.

A date with no rows is ambiguous in transaction data, so each day gets a status:
  observed - at least one sales row
  partial  - last day of the file and it stops well before the usual last sale of the day
  closed   - a weekday on which the business (almost) never trades: structural zero
  zero     - an isolated day with no rows on a normally trading weekday: genuine zero sales
  gap      - 3+ consecutive trading days with no rows: closure or missing export, cause unknown.
             Gap days are NOT counted as zero demand: they are masked in models and metrics.
These are heuristics inferred from the data; a business calendar would replace them (see roadmap).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

GAP_RUN_DAYS = 3
CLOSED_WEEKDAY_MAX_ACTIVE_SHARE = 0.10
METRICS = ["net_revenue", "gross_revenue", "returns_value", "transactions", "customers", "units"]


def build_daily(clean: pd.DataFrame) -> pd.DataFrame:
    sales = clean[clean["row_type"] == "sale"]
    returns = clean[clean["row_type"] == "return"]
    idx = pd.date_range(clean["date"].min(), clean["date"].max(), freq="D", name="date")
    daily = pd.DataFrame(index=idx)
    daily["gross_revenue"] = sales.groupby("date")["amount"].sum()
    daily["returns_value"] = -returns.groupby("date")["amount"].sum()
    daily["transactions"] = sales.groupby("date")["transaction_id"].nunique()
    daily["customers"] = sales.dropna(subset=["customer_id"]).groupby("date")["customer_id"].nunique()
    daily["units"] = sales.groupby("date")["quantity"].sum() if sales["quantity"].notna().any() else np.nan
    has_sales = daily["transactions"].fillna(0) > 0
    daily[["gross_revenue", "returns_value", "transactions", "customers"]] = (
        daily[["gross_revenue", "returns_value", "transactions", "customers"]].fillna(0.0))
    if sales["quantity"].notna().any():
        daily["units"] = daily["units"].fillna(0.0)
    daily["net_revenue"] = daily["gross_revenue"] - daily["returns_value"]

    status = pd.Series("observed", index=idx)
    dow = pd.Series(idx.dayofweek, index=idx)
    active_share = has_sales.groupby(dow).mean()
    occurrences = has_sales.groupby(dow).size()
    closed_dows = [d for d in active_share.index
                   if active_share[d] <= CLOSED_WEEKDAY_MAX_ACTIVE_SHARE and occurrences[d] >= 4]
    is_closed = dow.isin(closed_dows) & ~has_sales
    status[is_closed] = "closed"

    trading = idx[~dow.isin(closed_dows).values]
    empty = (~has_sales).reindex(trading)
    run_id = (empty != empty.shift()).cumsum()
    run_len = empty.groupby(run_id).transform("size")
    status.loc[empty.index[(empty & (run_len >= GAP_RUN_DAYS)).values]] = "gap"
    status.loc[empty.index[(empty & (run_len < GAP_RUN_DAYS)).values]] = "zero"

    # Partial last day: the file stops clearly before that weekday's usual last sale.
    if (clean["timestamp"] != clean["date"]).any() and len(idx) > 14:
        last_ts = sales.groupby("date")["timestamp"].max()
        last_hour = last_ts.dt.hour + last_ts.dt.minute / 60
        last_day = idx[-1]
        if last_day in last_hour.index and len(last_hour) > 10:
            typical = last_hour.iloc[:-1].quantile(0.05)
            if last_hour.loc[last_day] < typical - 1.0:
                status.loc[last_day] = "partial"
    daily["status"] = status
    daily["dow"] = dow
    return daily[METRICS + ["status", "dow"]]


def last_complete_day(daily: pd.DataFrame) -> pd.Timestamp:
    ok = daily.index[daily["status"] != "partial"]
    return ok[-1]


def model_series(daily: pd.DataFrame, metric: str = "net_revenue") -> pd.Series:
    """Series used by models: closed -> 0 (structural), zero -> 0, gap/partial -> NaN (masked)."""
    s = daily[metric].astype(float).copy()
    s[daily["status"].isin(["gap", "partial"])] = np.nan
    s[daily["status"].isin(["closed", "zero"])] = 0.0
    return s


def status_summary(daily: pd.DataFrame) -> dict:
    counts = daily["status"].value_counts().to_dict()
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    closed = sorted(daily.loc[daily["status"] == "closed", "dow"].unique().tolist())
    gaps = []
    g = daily.index[daily["status"] == "gap"]
    if len(g):
        start = prev = g[0]
        for d in g[1:]:
            if (d - prev).days > 2:  # allow a closed weekday inside one gap
                gaps.append([start.date().isoformat(), prev.date().isoformat()])
                start = d
            prev = d
        gaps.append([start.date().isoformat(), prev.date().isoformat()])
    return {
        "days_total": int(len(daily)),
        "days_by_status": {k: int(v) for k, v in counts.items()},
        "closed_weekdays": [names[d] for d in closed],
        "gap_periods": gaps,
        "zero_sales_days": [d.date().isoformat() for d in daily.index[daily["status"] == "zero"]],
        "partial_last_day": bool((daily["status"] == "partial").any()),
        "last_complete_day": last_complete_day(daily).date().isoformat(),
    }
