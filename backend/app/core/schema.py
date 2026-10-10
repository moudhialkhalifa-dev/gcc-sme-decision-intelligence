"""Canonical schema and column-mapping helpers.

Unit of a canonical row: ONE LINE ITEM of a transaction (one product on one
invoice/receipt). A transaction-level file (one row per receipt) is the special
case where every transaction has exactly one line. All KPIs aggregate lines to
transactions first, so both granularities produce the same definitions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional

CANONICAL_FIELDS = {
    "timestamp": "Date/time the transaction happened (required).",
    "transaction_id": "Invoice/receipt/order identifier shared by all lines of one transaction.",
    "customer_id": "Stable identifier of the buying customer (optional).",
    "product_id": "Product or service identifier (optional).",
    "product_name": "Human-readable product/service name (optional).",
    "quantity": "Units on this line; negative values are treated as returns.",
    "unit_price": "Price per unit in the dataset currency.",
    "amount": "Line value in the dataset currency (alternative to quantity x unit_price).",
    "category": "Product/service category (optional).",
    "location": "Branch, store, market or country (optional).",
    "channel": "Sales channel (optional).",
}

SYNONYMS = {
    "timestamp": ["invoicedate", "date", "datetime", "timestamp", "orderdate", "transactiondate", "saledate", "createdat", "time"],
    "transaction_id": ["invoice", "invoiceno", "invoiceid", "orderid", "orderno", "transactionid", "receipt", "receiptno", "billno", "ticket"],
    "customer_id": ["customerid", "customer", "custid", "clientid", "client", "memberid", "member", "customerno"],
    "product_id": ["stockcode", "sku", "productid", "itemid", "itemcode", "productcode", "serviceid"],
    "product_name": ["description", "productname", "itemname", "product", "item", "servicename"],
    "quantity": ["quantity", "qty", "units", "unitssold"],
    "unit_price": ["price", "unitprice", "itemprice", "rate"],
    "amount": ["amount", "total", "linetotal", "sales", "revenue", "totalprice", "value", "nettotal"],
    "category": ["category", "productcategory", "department", "itemcategory"],
    "location": ["location", "store", "branch", "country", "city", "region", "outlet", "storeid"],
    "channel": ["channel", "saleschannel", "source", "ordertype"],
}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def suggest_mapping(columns: list[str]) -> dict[str, str]:
    """Suggest canonical -> source column using name synonyms. The user confirms or edits."""
    normed = {c: _norm(c) for c in columns}
    used: set[str] = set()
    out: dict[str, str] = {}
    for pass_exact in (True, False):
        for canon, syns in SYNONYMS.items():
            if canon in out:
                continue
            for col, n in normed.items():
                if col in used:
                    continue
                hit = n in syns if pass_exact else any(len(s) >= 5 and s in n for s in syns)
                if hit:
                    out[canon] = col
                    used.add(col)
                    break
    return out


@dataclass
class IngestSettings:
    mapping: dict[str, str]
    currency: str
    timezone: str = "UTC"
    dayfirst: bool = False
    date_format: Optional[str] = None
    return_prefixes: list[str] = field(default_factory=list)
    non_product_pattern: Optional[str] = None
    drop_exact_duplicates: bool = True
    name: Optional[str] = None

    def validate(self, columns: list[str]) -> list[str]:
        errors = []
        m = self.mapping
        for canon, src in m.items():
            if canon not in CANONICAL_FIELDS:
                errors.append(f"Unknown canonical field '{canon}'.")
            elif src not in columns:
                errors.append(f"Mapped column '{src}' (for {canon}) is not in the file.")
        if len(set(m.values())) != len(m.values()):
            errors.append("Each source column can be mapped to only one field.")
        if "timestamp" not in m:
            errors.append("A timestamp column is required.")
        if "amount" not in m and not ("quantity" in m and "unit_price" in m):
            errors.append("Map either an amount column, or both quantity and unit price.")
        if not re.fullmatch(r"[A-Z]{3}", self.currency or ""):
            errors.append("Currency must be a 3-letter ISO code (e.g. BHD, SAR, AED, GBP).")
        if self.non_product_pattern:
            try:
                re.compile(self.non_product_pattern)
            except re.error as e:
                errors.append(f"Invalid non-product pattern: {e}")
        return errors

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "IngestSettings":
        keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in keys})
