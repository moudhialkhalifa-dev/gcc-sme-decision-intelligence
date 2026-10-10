"""Load -> map -> validate -> clean. Produces the canonical line-item table and a data-quality report.

Cleaning policy (each step is counted in the quality report):
  1. rows with an unparseable/missing timestamp are dropped;
  2. rows whose value cannot be computed (missing/non-numeric quantity, price or amount) are dropped;
  3. exact duplicate rows (identical in every mapped column) are dropped, keeping the first
     (assumption: they are export duplicates, not repeated scans; can be switched off);
  4. rows matching the optional non-product pattern (postage, fees, manual adjustments)
     are excluded from revenue and reported separately;
  5. rows with zero quantity, or zero/negative unit price, are dropped as non-sales records;
  6. remaining rows are 'return' (negative quantity/amount or a cancellation prefix) or 'sale'.
Amounts keep the dataset's own currency. Timestamps are treated as local business time in the
declared time zone and are never shifted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .schema import IngestSettings

CLEAN_COLUMNS = [
    "timestamp", "date", "transaction_id", "customer_id", "product_id", "product_name",
    "quantity", "unit_price", "amount", "row_type", "category", "location", "channel",
]


class IngestError(ValueError):
    pass


@dataclass
class IngestResult:
    clean: pd.DataFrame
    report: dict


def sniff_delimiter(path: str) -> str:
    import csv
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        head = f.readline() + f.readline()
    try:
        return csv.Sniffer().sniff(head, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def read_raw_csv(path: str, nrows: int | None = None) -> pd.DataFrame:
    """Read every column as text so that identifiers keep leading zeros and nothing is coerced silently."""
    sep = sniff_delimiter(path)
    last_err = None
    for enc in ("utf-8-sig", "cp1252"):
        try:
            df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""], nrows=nrows, encoding=enc, sep=sep)
            df.columns = [str(c).strip() for c in df.columns]
            return df
        except UnicodeDecodeError as e:  # try next encoding
            last_err = e
        except pd.errors.ParserError as e:
            raise IngestError(f"The file could not be parsed as CSV: {e}") from e
        except pd.errors.EmptyDataError as e:
            raise IngestError("The file is empty.") from e
    raise IngestError(f"Unsupported text encoding: {last_err}")


def _to_number(s: pd.Series) -> pd.Series:
    cleaned = s.astype("string").str.replace(r"[,\s]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


def _norm_id(s: pd.Series) -> pd.Series:
    out = s.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
    return out.mask(out.isin(["", "nan", "NaN", "None", "null", "NULL"]))


def clean_transactions(raw: pd.DataFrame, settings: IngestSettings) -> IngestResult:
    errors = settings.validate(list(raw.columns))
    if errors:
        raise IngestError(" ".join(errors))
    m = settings.mapping
    n_raw = len(raw)
    if n_raw == 0:
        raise IngestError("The file has no data rows.")
    steps: list[dict] = []
    warnings: list[str] = []

    df = pd.DataFrame(index=raw.index)
    ts_raw = raw[m["timestamp"]]
    df["timestamp"] = pd.to_datetime(ts_raw, errors="coerce", dayfirst=settings.dayfirst,
                                     format=settings.date_format or None)
    if getattr(df["timestamp"].dt, "tz", None) is not None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(None)
        warnings.append("Timestamps carried a UTC offset; the offset was dropped and wall-clock time kept.")
    for col in ("customer_id", "product_id", "transaction_id"):
        df[col] = _norm_id(raw[m[col]]) if col in m else pd.Series(pd.NA, index=raw.index, dtype="string")
    for col in ("product_name", "category", "location", "channel"):
        df[col] = (raw[m[col]].astype("string").str.strip() if col in m
                   else pd.Series(pd.NA, index=raw.index, dtype="string"))
    df["quantity"] = _to_number(raw[m["quantity"]]) if "quantity" in m else np.nan
    df["unit_price"] = _to_number(raw[m["unit_price"]]) if "unit_price" in m else np.nan
    if "amount" in m:
        df["amount"] = _to_number(raw[m["amount"]])
    else:
        df["amount"] = df["quantity"] * df["unit_price"]

    missing = {canon: float(raw[src].isna().mean()) for canon, src in m.items()}

    def drop(mask: pd.Series, key: str, label: str):
        nonlocal df
        n = int(mask.sum())
        steps.append({"key": key, "label": label, "rows": n, "share": n / n_raw})
        df = df[~mask]

    drop(df["timestamp"].isna(), "invalid_date", "Missing or unparseable timestamp (dropped)")
    drop(df["amount"].isna(), "invalid_value", "Missing or non-numeric quantity/price/amount (dropped)")

    if settings.drop_exact_duplicates:
        dup = raw.loc[df.index, list(m.values())].duplicated(keep="first")
        drop(dup, "duplicate", "Exact duplicate rows (dropped, first kept)")
    else:
        steps.append({"key": "duplicate", "label": "Exact duplicate rows (kept by setting)", "rows": 0, "share": 0.0})

    non_product_value = 0.0
    if settings.non_product_pattern and "product_id" in m:
        np_mask = df["product_id"].fillna("").str.match(settings.non_product_pattern)
        non_product_value = float(df.loc[np_mask, "amount"].sum())
        drop(np_mask, "non_product", "Non-product lines such as postage, fees, adjustments (excluded from revenue)")

    bad = df["amount"].eq(0)
    if "quantity" in m:
        bad |= df["quantity"].eq(0)
    if "unit_price" in m:
        bad |= df["unit_price"].le(0)
    drop(bad, "non_sale", "Zero quantity or zero/negative price (dropped as non-sales records)")

    is_return = df["amount"].lt(0)
    if "quantity" in m:
        is_return |= df["quantity"].lt(0)
    if settings.return_prefixes and "transaction_id" in m:
        pref = tuple(settings.return_prefixes)
        is_return |= df["transaction_id"].fillna("").str.startswith(pref)
    # A return always reduces revenue, whatever sign convention the source used.
    df["amount"] = np.where(is_return, -df["amount"].abs(), df["amount"])
    df["row_type"] = np.where(is_return, "return", "sale")

    if len(df) == 0 or not (df["row_type"] == "sale").any():
        raise IngestError("No valid sales rows remain after validation. Check the column mapping and date format.")

    synthetic_txn = "transaction_id" not in m
    if synthetic_txn:
        df["transaction_id"] = ("row-" + pd.Series(df.index, index=df.index).astype(str)).astype("string")
        warnings.append("No transaction ID column was mapped: every row is treated as its own transaction.")
    else:
        n_missing_txn = int(df["transaction_id"].isna().sum())
        if n_missing_txn:
            fill = ("row-" + pd.Series(df.index, index=df.index).astype(str)).astype("string")
            df["transaction_id"] = df["transaction_id"].fillna(fill)
            warnings.append(f"{n_missing_txn:,} rows had no transaction ID and are each treated as a separate transaction.")

    df["date"] = df["timestamp"].dt.normalize()
    df = df.sort_values("timestamp", kind="stable").reset_index(drop=True)[CLEAN_COLUMNS]

    sales = df[df["row_type"] == "sale"]
    returns = df[df["row_type"] == "return"]
    lines_per_txn = float(len(sales) / max(sales["transaction_id"].nunique(), 1))
    has_time = bool((df["timestamp"] != df["date"]).any())
    gross = float(sales["amount"].sum())
    cust_cov_rows = float(sales["customer_id"].notna().mean())
    cust_cov_rev = float(sales.loc[sales["customer_id"].notna(), "amount"].sum() / gross) if gross else 0.0
    if "customer_id" in m and cust_cov_rev < 0.85:
        warnings.append(f"Only {cust_cov_rev:.0%} of sales revenue has a customer ID; customer analytics covers identified customers only.")
    multi_date_txn = int((sales.groupby("transaction_id")["date"].nunique() > 1).sum()) if not synthetic_txn else 0
    if multi_date_txn:
        warnings.append(f"{multi_date_txn:,} transaction IDs appear on more than one date; each is counted once per day it appears.")

    report = {
        "rows_raw": n_raw,
        "rows_clean": int(len(df)),
        "rows_sales": int(len(sales)),
        "rows_returns": int(len(returns)),
        "steps": steps,
        "missing_by_field": missing,
        "date_min": df["timestamp"].min().isoformat(),
        "date_max": df["timestamp"].max().isoformat(),
        "has_time_of_day": has_time,
        "granularity": "line-item" if lines_per_txn > 1.05 else "transaction",
        "lines_per_transaction": lines_per_txn,
        "transactions": int(sales["transaction_id"].nunique()),
        "customers_identified": int(sales["customer_id"].nunique()),
        "customer_id_coverage_rows": cust_cov_rows,
        "customer_id_coverage_revenue": cust_cov_rev,
        "products": int(sales["product_id"].nunique()),
        "gross_sales": gross,
        "returns_value": float(-returns["amount"].sum()),
        "non_product_value_excluded": non_product_value,
        "currency": settings.currency,
        "timezone": settings.timezone,
        "assumptions": [
            f"Amounts are reported in {settings.currency} exactly as supplied; no currency conversion is applied.",
            f"Timestamps are interpreted as local business time ({settings.timezone}); days are calendar days in that zone.",
            "Net revenue = sales lines minus returns/cancellations, dated on the day each line was recorded.",
            "Customer metrics count identifiable customers only (rows with a customer ID).",
        ],
        "warnings": warnings,
        "mapped_fields": sorted(m.keys()),
    }
    return IngestResult(clean=df, report=report)
