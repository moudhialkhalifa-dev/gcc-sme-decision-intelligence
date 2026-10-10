"""Customer intelligence: RFM features, rule-based RFM segments, K-Means comparison, stability.

All features are computed from rows strictly BEFORE a documented cutoff date.
  recency_days = days between the customer's latest sale and the cutoff
  frequency    = number of distinct sale transactions
  monetary     = sales minus returns for that customer (historical value, floored at 0)
Segments are descriptive groupings. 'At-risk' and 'Dormant' are inactivity heuristics, not
validated churn probabilities. 'monetary' is historical spend, not predicted lifetime value.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

NEW_TENURE_DAYS = 60
SEGMENT_ORDER = ["High-value", "Loyal", "New", "Occasional", "At-risk", "Dormant"]
SEGMENT_RULES = {
    "New": f"First purchase within {NEW_TENURE_DAYS} days of the cutoff.",
    "High-value": "Recency, frequency and spend scores all 4 or 5 (top 40% on each).",
    "Loyal": "Recency score 3+ and frequency score 3+, but not High-value.",
    "At-risk": "Recency score 1-2 (bottom 40%) with frequency score 3+ or spend score 4+: was valuable, now inactive.",
    "Dormant": "Recency score 1 (least recent 20%) with low frequency and spend.",
    "Occasional": "Everyone else: infrequent buyers who are neither new nor long inactive.",
}


def rfm_table(clean: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    cutoff = pd.Timestamp(cutoff).normalize()
    rows = clean[(clean["date"] < cutoff) & clean["customer_id"].notna()]
    sales = rows[rows["row_type"] == "sale"]
    g = sales.groupby("customer_id")
    rfm = pd.DataFrame({
        "last_purchase": g["date"].max(), "first_purchase": g["date"].min(),
        "frequency": g["transaction_id"].nunique(),
    })
    rfm["monetary"] = rows.groupby("customer_id")["amount"].sum().reindex(rfm.index).clip(lower=0)
    rfm["recency_days"] = (cutoff - rfm["last_purchase"]).dt.days
    rfm["tenure_days"] = (cutoff - rfm["first_purchase"]).dt.days
    return rfm


def _score(s: pd.Series, higher_is_better: bool = True) -> pd.Series:
    """1-5 score from percentile rank; tied values always share a score."""
    pct = s.rank(pct=True, method="average", ascending=higher_is_better)
    return np.ceil(pct * 5).clip(1, 5).astype(int)


def add_scores(rfm: pd.DataFrame) -> pd.DataFrame:
    out = rfm.copy()
    out["r_score"] = _score(out["recency_days"], higher_is_better=False)
    out["f_score"] = _score(out["frequency"])
    out["m_score"] = _score(out["monetary"])
    return out


def rule_segments(r, f, m, tenure) -> np.ndarray:
    r, f, m, tenure = map(np.asarray, (r, f, m, tenure))
    conds = [
        tenure <= NEW_TENURE_DAYS,
        (r >= 4) & (f >= 4) & (m >= 4),
        (r >= 3) & (f >= 3),
        (r <= 2) & ((f >= 3) | (m >= 4)),
        r <= 1,
    ]
    return np.select(conds, ["New", "High-value", "Loyal", "At-risk", "Dormant"], default="Occasional")


def segment_customers(clean: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    rfm = add_scores(rfm_table(clean, cutoff))
    rfm["segment"] = rule_segments(rfm["r_score"], rfm["f_score"], rfm["m_score"], rfm["tenure_days"])
    return rfm


def _kmeans_features(rfm: pd.DataFrame) -> np.ndarray:
    """log1p tames the heavy right skew of frequency and spend; then standardise."""
    from sklearn.preprocessing import StandardScaler
    x = np.log1p(rfm[["recency_days", "frequency", "monetary"]].astype(float).values)
    return StandardScaler().fit_transform(x)


def kmeans_compare(rfm: pd.DataFrame, k_range=range(2, 8), n_boot: int = 10, seed: int = 0,
                   min_stability: float = 0.85) -> dict:
    """K-Means on scaled log RFM. k is the highest-silhouette k >= 3 whose bootstrap stability
    (mean adjusted Rand index between the base clustering and refits on resamples) passes the bar."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import adjusted_rand_score, silhouette_score
    x = _kmeans_features(rfm)
    rng = np.random.default_rng(seed)
    n = len(x)
    sample = rng.choice(n, min(n, 5000), replace=False)
    diag, fits = [], {}
    for k in k_range:
        if k >= n:
            break
        km = KMeans(n_clusters=k, n_init=5, random_state=seed).fit(x)
        aris = []
        for b in range(n_boot):
            idx = rng.choice(n, n, replace=True)
            kb = KMeans(n_clusters=k, n_init=2, random_state=seed + b + 1).fit(x[idx])
            aris.append(adjusted_rand_score(km.labels_, kb.predict(x)))
        diag.append({"k": k, "silhouette": float(silhouette_score(x[sample], km.labels_[sample])),
                     "inertia": float(km.inertia_), "stability_ari": float(np.mean(aris)),
                     "stability_ari_min": float(np.min(aris))})
        fits[k] = km
    eligible = [d for d in diag if d["k"] >= 3 and d["stability_ari"] >= min_stability]
    pool = eligible or diag
    chosen = max(pool, key=lambda d: d["silhouette"])["k"]
    labels = fits[chosen].labels_
    out = rfm.copy()
    out["cluster"] = labels
    # name clusters from what is observed in them: where the cluster's median customer sits in
    # the whole customer base on recency and on spend (thirds), plus frequency if names collide
    med = out.groupby("cluster")[["recency_days", "frequency", "monetary"]].median()
    pct = lambda col, v: float((out[col] <= v).mean())
    cluster_names, seen = {}, {}
    for c, r in med.iterrows():
        pr, pm = pct("recency_days", r["recency_days"]), pct("monetary", r["monetary"])
        rec = "Recent" if pr <= 1 / 3 else "Cooling" if pr <= 2 / 3 else "Inactive"
        val = "high spend" if pm >= 2 / 3 else "mid spend" if pm >= 1 / 3 else "low spend"
        cluster_names[int(c)] = f"{rec}, {val}"
        seen.setdefault(cluster_names[int(c)], []).append(int(c))
    for nm, cs in seen.items():
        if len(cs) > 1:
            for c in cs:
                cluster_names[c] = f"{nm}, ~{med.loc[c, 'frequency']:.0f} purchases"
    out["cluster_name"] = out["cluster"].map(cluster_names)
    ari = float(adjusted_rand_score(out["segment"], out["cluster"])) if "segment" in out else None
    xt = pd.crosstab(out["segment"], out["cluster_name"]) if "segment" in out else None
    return {
        "labels": out[["cluster", "cluster_name"]],
        "k": int(chosen), "diagnostics": diag,
        "selection_rule": f"Highest silhouette among k >= 3 with bootstrap stability ARI >= {min_stability}; "
                          f"falls back to highest silhouette if none pass.",
        "passed_stability": bool(eligible),
        "agreement_with_rfm_ari": ari,
        "crosstab": None if xt is None else {"rows": list(xt.index), "cols": list(xt.columns), "values": xt.values.tolist()},
        "clusters": summarize(out, "cluster_name"),
        "preprocessing": "log1p of recency, frequency and spend, then z-score standardisation.",
    }


def summarize(seg: pd.DataFrame, col: str = "segment") -> list[dict]:
    total_m, total_n = float(seg["monetary"].sum()), len(seg)
    rows = []
    for name, g in seg.groupby(col):
        rows.append({
            "segment": str(name), "customers": int(len(g)), "customer_share": len(g) / total_n,
            "revenue": float(g["monetary"].sum()),
            "revenue_share": float(g["monetary"].sum() / total_m) if total_m else None,
            "median_recency_days": float(g["recency_days"].median()),
            "median_frequency": float(g["frequency"].median()),
            "median_monetary": float(g["monetary"].median()),
            "mean_monetary": float(g["monetary"].mean()),
        })
    order = {s: i for i, s in enumerate(SEGMENT_ORDER)}
    rows.sort(key=lambda r: (order.get(r["segment"], 99), -r["revenue"]))
    return rows


def describe(row: dict, currency: str) -> str:
    return (f"Typically last bought {row['median_recency_days']:.0f} days before the cutoff, "
            f"{row['median_frequency']:.0f} purchase(s), {currency} {row['median_monetary']:,.0f} spent (medians).")


def out_of_time_check(clean: pd.DataFrame, cutoff: pd.Timestamp, window_days: int = 90) -> dict | None:
    """Segment customers as of (cutoff - window), then look at what they did in the following window.
    This tests whether the heuristic labels separate behaviour; it is not a churn model."""
    cutoff = pd.Timestamp(cutoff).normalize()
    t0 = cutoff - pd.Timedelta(days=window_days)
    if (t0 - clean["date"].min()).days < 180:
        return None
    seg0 = segment_customers(clean, t0)
    after = clean[(clean["date"] >= t0) & (clean["date"] < cutoff) & clean["customer_id"].notna()]
    buyers = set(after.loc[after["row_type"] == "sale", "customer_id"].unique())
    spend = after.groupby("customer_id")["amount"].sum()
    seg0["bought_again"] = seg0.index.isin(buyers)
    seg0["next_spend"] = spend.reindex(seg0.index).fillna(0.0).values
    seg1 = segment_customers(clean, cutoff)
    seg0["segment_now"] = seg1["segment"].reindex(seg0.index).values
    by = []
    for name, g in seg0.groupby("segment"):
        by.append({"segment": name, "customers": int(len(g)), "repurchase_rate": float(g["bought_again"].mean()),
                   "mean_next_spend": float(g["next_spend"].mean())})
    order = {s: i for i, s in enumerate(SEGMENT_ORDER)}
    by.sort(key=lambda r: order.get(r["segment"], 99))
    mig = pd.crosstab(seg0["segment"], seg0["segment_now"])
    mig = mig.reindex(index=[s for s in SEGMENT_ORDER if s in mig.index], columns=[s for s in SEGMENT_ORDER if s in mig.columns])
    valuable = seg0[seg0["segment"].isin(["High-value", "Loyal"])]
    slipped = valuable[valuable["segment_now"].isin(["At-risk", "Dormant"])]
    return {
        "as_of": t0.date().isoformat(), "window_days": window_days, "until": cutoff.date().isoformat(),
        "by_segment": by,
        "migration": {"rows": list(mig.index), "cols": list(mig.columns), "values": mig.fillna(0).astype(int).values.tolist()},
        "valuable_then": int(len(valuable)), "valuable_slipped": int(len(slipped)),
        "valuable_slipped_prior_value": float(slipped["monetary"].sum()),
        "valuable_prior_value": float(valuable["monetary"].sum()),
        "note": "Share of each segment (as labelled at the earlier date) that bought again in the following window.",
    }


def run_customer_intelligence(clean: pd.DataFrame, cutoff: pd.Timestamp, currency: str, do_kmeans: bool = True) -> dict:
    seg = segment_customers(clean, cutoff)
    summary = summarize(seg)
    for r in summary:
        r["rule"] = SEGMENT_RULES.get(r["segment"], "")
        r["description"] = describe(r, currency)
    top10 = float(seg["monetary"].nlargest(10).sum() / seg["monetary"].sum()) if seg["monetary"].sum() else None
    result = {
        "cutoff": pd.Timestamp(cutoff).date().isoformat(),
        "customers": int(len(seg)),
        "definitions": {
            "recency_days": "Days from the customer's latest purchase to the cutoff date.",
            "frequency": "Number of distinct sale transactions before the cutoff.",
            "monetary": "Sales minus returns before the cutoff (historical value, not a prediction).",
            "scores": "1-5 from percentile rank within this dataset; ties share a score.",
        },
        "segments": summary,
        "top10_customer_revenue_share": top10,
        "distributions": {c: [float(v) for v in seg[c].quantile([0.1, 0.25, 0.5, 0.75, 0.9]).values]
                          for c in ["recency_days", "frequency", "monetary"]},
        "caveats": [
            "Only customers with an ID are included.",
            "At-risk and Dormant are rule-based inactivity labels, not churn probabilities.",
            "Spend shown is historical; predicted lifetime value is not modelled.",
        ],
    }
    if do_kmeans and len(seg) >= 200:
        km = kmeans_compare(seg)
        seg = seg.join(km.pop("labels"))
        for r in km["clusters"]:
            r["description"] = describe(r, currency)
        result["kmeans"] = km
    else:
        result["kmeans"] = None
    result["out_of_time"] = out_of_time_check(clean, cutoff)
    result["table"] = seg  # DataFrame; caller persists it and removes it before serialising
    return result
