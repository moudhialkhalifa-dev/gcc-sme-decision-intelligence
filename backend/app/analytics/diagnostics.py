"""Calculation-based diagnostics of a revenue change between two periods.

Factor decomposition uses Shapley values of the multiplicative identity, which splits every
interaction term equally between the factors involved and therefore reconciles EXACTLY:

  two-factor:    net revenue = transactions x average transaction value
  three-factor:  identified revenue = active customers x transactions per customer x value per transaction
                 total change = three factor effects + change in revenue with no customer ID

Dimension contributions (product, location, ...) are each an alternative split of the SAME total
change. They must not be added across dimensions. Contributions describe where the change shows
up arithmetically; they are not causes.
"""
from __future__ import annotations

from itertools import permutations
from math import factorial

import numpy as np
import pandas as pd

from ..core.kpis import Period, compute_kpis, slice_period


def shapley_product(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    """Exact Shapley attribution of prod(after) - prod(before) to each factor."""
    names = list(before)
    contrib = dict.fromkeys(names, 0.0)

    def value(changed: set) -> float:
        v = 1.0
        for n in names:
            v *= after[n] if n in changed else before[n]
        return v

    for order in permutations(names):
        changed: set = set()
        for n in order:
            base = value(changed)
            changed.add(n)
            contrib[n] += value(changed) - base
    k = factorial(len(names))
    return {n: c / k for n, c in contrib.items()}


def _factors(rows: pd.DataFrame) -> dict:
    k = compute_kpis(rows)
    sales = rows[rows["row_type"] == "sale"]
    ident_ids = sales.loc[sales["customer_id"].notna(), "transaction_id"].unique()
    # A transaction is 'identified' if its sale lines carry a customer ID. Returns are attributed
    # by their own customer ID.
    ident_rows = rows[rows["customer_id"].notna()]
    ident_rev = float(ident_rows["amount"].sum())
    customers = k["active_customers"]
    ident_tx = int(len(ident_ids))
    return {
        "net_revenue": k["net_revenue"], "transactions": k["transactions"],
        "atv": k["avg_transaction_value"] or 0.0,
        "identified_revenue": ident_rev, "unidentified_revenue": k["net_revenue"] - ident_rev,
        "customers": customers,
        "tx_per_customer": (ident_tx / customers) if customers else 0.0,
        "value_per_tx": (ident_rev / ident_tx) if ident_tx else 0.0,
    }


def factor_decomposition(clean: pd.DataFrame, a: Period, b: Period, has_customers: bool) -> dict:
    """a = baseline period, b = comparison period. Effects are scaled to equal-length periods only
    if the periods already have equal length; otherwise totals are compared as-is and flagged."""
    fa, fb = _factors(slice_period(clean, a)), _factors(slice_period(clean, b))
    total = fb["net_revenue"] - fa["net_revenue"]
    two = shapley_product({"transactions": fa["transactions"], "atv": fa["atv"]},
                          {"transactions": fb["transactions"], "atv": fb["atv"]})
    out = {
        "baseline": fa, "comparison": fb, "total_change": total,
        "total_change_pct": (total / abs(fa["net_revenue"])) if fa["net_revenue"] else None,
        "two_factor": {
            "identity": "net revenue = transactions x average transaction value",
            "effects": [
                {"factor": "Number of transactions", "before": fa["transactions"], "after": fb["transactions"],
                 "effect": two["transactions"]},
                {"factor": "Average transaction value", "before": fa["atv"], "after": fb["atv"], "effect": two["atv"]},
            ],
            "reconciliation_error": (two["transactions"] + two["atv"]) - total,
        },
        "three_factor": None,
    }
    if has_customers and fa["customers"] and fb["customers"]:
        three = shapley_product(
            {"customers": fa["customers"], "tx_per_customer": fa["tx_per_customer"], "value_per_tx": fa["value_per_tx"]},
            {"customers": fb["customers"], "tx_per_customer": fb["tx_per_customer"], "value_per_tx": fb["value_per_tx"]})
        unident = fb["unidentified_revenue"] - fa["unidentified_revenue"]
        out["three_factor"] = {
            "identity": "identified revenue = active customers x transactions per customer x value per transaction; "
                        "plus revenue without a customer ID",
            "effects": [
                {"factor": "Active customers", "before": fa["customers"], "after": fb["customers"], "effect": three["customers"]},
                {"factor": "Transactions per customer", "before": fa["tx_per_customer"], "after": fb["tx_per_customer"],
                 "effect": three["tx_per_customer"]},
                {"factor": "Value per transaction", "before": fa["value_per_tx"], "after": fb["value_per_tx"],
                 "effect": three["value_per_tx"]},
                {"factor": "Revenue without customer ID", "before": fa["unidentified_revenue"],
                 "after": fb["unidentified_revenue"], "effect": unident},
            ],
            "reconciliation_error": sum(three.values()) + unident - total,
        }
    return out


def dimension_contributions(clean: pd.DataFrame, a: Period, b: Period, dimension: str,
                            top: int = 8, labels: pd.Series | None = None) -> dict:
    """Change in net revenue by group. Sum over ALL groups equals the total change (reconciled)."""
    ra, rb = slice_period(clean, a), slice_period(clean, b)

    def key(rows):
        if labels is not None:  # e.g. customer -> segment
            return rows["customer_id"].map(labels).fillna("No customer ID / unsegmented")
        return rows[dimension].fillna("(not recorded)")

    va = ra.groupby(key(ra))["amount"].sum()
    vb = rb.groupby(key(rb))["amount"].sum()
    df = pd.concat([va.rename("before"), vb.rename("after")], axis=1).fillna(0.0)
    df["change"] = df["after"] - df["before"]
    total = float(df["change"].sum())
    df["status"] = np.where(df["before"] == 0, "new", np.where(df["after"] == 0, "absent in comparison", "continuing"))
    names = None
    if dimension == "product_id" and "product_name" in clean.columns:
        both = pd.concat([ra, rb])
        names = both.dropna(subset=["product_name"]).groupby("product_id")["product_name"].agg(lambda s: s.iloc[-1])

    def rec(k, r):
        label = f"{names[k]} ({k})" if names is not None and k in names.index else str(k)
        return {"key": str(k), "label": label, "before": float(r["before"]), "after": float(r["after"]),
                "change": float(r["change"]), "share_of_total_change": float(r["change"] / total) if total else None,
                "status": r["status"]}

    neg = df[df["change"] < 0].sort_values("change").head(top)
    pos = df[df["change"] > 0].sort_values("change", ascending=False).head(top)
    shown = set(neg.index) | set(pos.index)
    rest = df[~df.index.isin(shown)]
    return {
        "dimension": dimension, "groups": int(len(df)), "total_change": total,
        "sum_negative": float(df.loc[df["change"] < 0, "change"].sum()),
        "sum_positive": float(df.loc[df["change"] > 0, "change"].sum()),
        "top_negative": [rec(k, r) for k, r in neg.iterrows()],
        "top_positive": [rec(k, r) for k, r in pos.iterrows()],
        "other": {"groups": int(len(rest)), "change": float(rest["change"].sum())},
        "reconciliation_error": float(neg["change"].sum() + pos["change"].sum() + rest["change"].sum() - total),
    }


def weekday_contributions(clean: pd.DataFrame, a: Period, b: Period) -> dict:
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    ra, rb = slice_period(clean, a), slice_period(clean, b)
    va = ra.groupby(ra["date"].dt.dayofweek)["amount"].sum()
    vb = rb.groupby(rb["date"].dt.dayofweek)["amount"].sum()
    rows = []
    for d in range(7):
        x, y = float(va.get(d, 0.0)), float(vb.get(d, 0.0))
        rows.append({"key": names[d], "label": names[d], "before": x, "after": y, "change": y - x})
    return {"dimension": "weekday", "rows": rows, "total_change": sum(r["change"] for r in rows)}


def diagnose(clean: pd.DataFrame, a: Period, b: Period, available_dims: list[str], has_customers: bool,
             segment_labels: pd.Series | None = None) -> dict:
    out = factor_decomposition(clean, a, b, has_customers)
    out["baseline_period"], out["comparison_period"] = a.to_dict(), b.to_dict()
    out["equal_length"] = a.days == b.days
    dims = {}
    for d in available_dims:
        dims[d] = dimension_contributions(clean, a, b, d)
    if segment_labels is not None and has_customers:
        dims["customer_segment"] = dimension_contributions(clean, a, b, "customer_id", labels=segment_labels)
        dims["customer_segment"]["dimension"] = "customer_segment"
    dims["weekday"] = weekday_contributions(clean, a, b)
    out["dimensions"] = dims
    out["caveats"] = [
        "Each dimension is a separate way of splitting the same total change; do not add them together.",
        "Contributions show where the change appears in the numbers, not why it happened.",
    ]
    if not out["equal_length"]:
        out["caveats"].append("The two periods have different lengths, so totals are not like-for-like.")
    return out
