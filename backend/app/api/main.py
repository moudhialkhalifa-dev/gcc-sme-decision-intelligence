"""HTTP API. Single-company demonstration: datasets are separated by ID and, when API_KEY is set,
every /api route except /api/health requires the X-API-Key header. Customer identifiers are never
returned by any endpoint; only aggregates are.
"""
from __future__ import annotations

import json
import logging
import os
import shutil

import pandas as pd
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..analytics.diagnostics import diagnose
from ..core import capabilities as caps_mod
from ..core.ingest import IngestError, read_raw_csv
from ..core.kpis import Period, breakdown, compare_periods, resample_series, slice_period, trailing_period
from ..core.schema import CANONICAL_FIELDS, IngestSettings, suggest_mapping
from ..core.timeseries import last_complete_day
from ..pipeline import jsonable
from . import service
from .config import settings
from .db import AnalysisResult, Dataset, Job, SessionLocal, init_db

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("sme-di")

app = FastAPI(title="GCC SME Decision Intelligence API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup() -> None:
    os.makedirs(settings.data_dir, exist_ok=True)
    init_db()
    log.info("started; data_dir=%s auth=%s", settings.data_dir, "on" if settings.api_key else "off")


@app.exception_handler(Exception)
async def _unhandled(request, exc):  # never leak internals to the client
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal error. It has been logged."})


def auth(x_api_key: str | None = Header(default=None)) -> None:
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(401, "Missing or invalid API key.")


def get_dataset(dataset_id: str) -> Dataset:
    with SessionLocal() as s:
        ds = s.get(Dataset, dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found.")
    return ds


def ready_dataset(dataset_id: str) -> Dataset:
    ds = get_dataset(dataset_id)
    if ds.status != "ready":
        raise HTTPException(409, f"Dataset is not ready (status: {ds.status}).")
    return ds


def result(dataset_id: str, kind: str):
    with SessionLocal() as s:
        row = s.query(AnalysisResult).filter_by(dataset_id=dataset_id, kind=kind).first()
    return row.payload if row else None


def require_cap(ds: Dataset, key: str) -> None:
    for c in ds.capabilities or []:
        if c["key"] == key and not c["enabled"]:
            raise HTTPException(422, {"unsupported": key, "reason": c["reason"]})


def ds_public(ds: Dataset, job: Job | None = None) -> dict:
    return {"id": ds.id, "name": ds.name, "filename": ds.filename, "status": ds.status, "currency": ds.currency,
            "timezone": ds.timezone, "is_demo": bool(ds.is_demo), "created_at": ds.created_at.isoformat(),
            "columns": ds.columns, "settings": ds.settings, "quality": ds.quality, "capabilities": ds.capabilities,
            "job": None if job is None else job_public(job)}


def job_public(job: Job) -> dict:
    return {"id": job.id, "dataset_id": job.dataset_id, "status": job.status, "stage": job.stage,
            "progress": job.progress, "error": job.error}


def latest_job(dataset_id: str) -> Job | None:
    with SessionLocal() as s:
        return s.query(Job).filter_by(dataset_id=dataset_id).order_by(Job.created_at.desc()).first()


api = Depends(auth)


@app.get("/api/health")
def health():
    from sqlalchemy import text
    try:
        with SessionLocal() as s:
            s.execute(text("select 1"))
        return {"status": "ok", "database": "ok"}
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "degraded", "database": "unreachable"})


@app.get("/api/meta", dependencies=[api])
def meta():
    return {"canonical_fields": CANONICAL_FIELDS, "max_upload_mb": settings.max_upload_mb,
            "demo_available": os.path.exists(settings.demo_csv), "tenancy": "single-company demonstration",
            "row_unit": "One row = one line item of a transaction (a transaction-level file is one line per transaction)."}


@app.get("/api/datasets", dependencies=[api])
def list_datasets():
    with SessionLocal() as s:
        rows = s.query(Dataset).order_by(Dataset.created_at.desc()).all()
    return [{"id": d.id, "name": d.name, "status": d.status, "currency": d.currency, "is_demo": bool(d.is_demo),
             "created_at": d.created_at.isoformat()} for d in rows]


def _register(path_writer, filename: str, name: str, is_demo: bool = False) -> Dataset:
    with SessionLocal() as s:
        ds = Dataset(name=name, filename=filename, is_demo=int(is_demo))
        s.add(ds)
        s.commit()
        dataset_id = ds.id
    os.makedirs(service.dataset_dir(dataset_id), exist_ok=True)
    try:
        path_writer(service.raw_path(dataset_id))
        preview = read_raw_csv(service.raw_path(dataset_id), nrows=200)
        if preview.shape[1] < 2:
            raise IngestError("The file needs at least two columns (a date and a value).")
    except (IngestError, HTTPException) as e:
        service.delete_dataset(dataset_id)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(400, str(e)) from e
    with SessionLocal() as s:
        ds = s.get(Dataset, dataset_id)
        ds.columns = list(preview.columns)
        s.commit()
    return ds, preview


@app.post("/api/datasets", dependencies=[api])
def upload_dataset(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith((".csv", ".txt")):
        raise HTTPException(400, "Please upload a .csv file.")
    limit = settings.max_upload_mb * 1024 * 1024

    def writer(dst: str):
        size = 0
        with open(dst, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"File is larger than the {settings.max_upload_mb} MB limit.")
                out.write(chunk)
        if size == 0:
            raise HTTPException(400, "The file is empty.")

    safe_name = os.path.basename(file.filename)[:200]
    ds, preview = _register(writer, safe_name, os.path.splitext(safe_name)[0])
    return {"dataset": ds_public(ds), "suggested_mapping": suggest_mapping(list(preview.columns)),
            "preview": preview.head(8).fillna("").to_dict(orient="records")}


@app.post("/api/demo", dependencies=[api])
def load_demo():
    """Register the bundled public case-study file with its documented mapping and start processing."""
    if not os.path.exists(settings.demo_csv):
        raise HTTPException(404, "The case-study file is not present on this server.")
    with SessionLocal() as s:
        existing = s.query(Dataset).filter(Dataset.is_demo == 1, Dataset.status.in_(["ready", "processing"])).first()
    if existing:
        return {"dataset": ds_public(existing, latest_job(existing.id))}
    cfg = json.load(open(settings.demo_config))
    ds, _ = _register(lambda dst: shutil.copyfile(settings.demo_csv, dst), os.path.basename(settings.demo_csv), cfg["name"], True)
    return process_dataset(ds.id, ProcessRequest(**{k: v for k, v in cfg.items() if k != "name"}))


class ProcessRequest(BaseModel):
    mapping: dict[str, str]
    currency: str = Field(min_length=3, max_length=3)
    timezone: str = "UTC"
    dayfirst: bool = False
    date_format: str | None = None
    return_prefixes: list[str] = []
    non_product_pattern: str | None = Field(default=None, max_length=500)
    drop_exact_duplicates: bool = True


@app.post("/api/datasets/{dataset_id}/process", dependencies=[api])
def process_dataset(dataset_id: str, body: ProcessRequest):
    ds = get_dataset(dataset_id)
    if ds.status == "processing":
        raise HTTPException(409, "This dataset is already being processed.")
    ing = IngestSettings(**body.model_dump())
    ing.currency = ing.currency.upper()
    errors = ing.validate(ds.columns or [])
    if errors:
        raise HTTPException(422, {"errors": errors})
    with SessionLocal() as s:
        row = s.get(Dataset, dataset_id)
        row.settings, row.currency, row.timezone = ing.to_dict(), ing.currency, ing.timezone
        s.commit()
    service.start_job(dataset_id)
    return {"dataset": ds_public(get_dataset(dataset_id), latest_job(dataset_id))}


@app.get("/api/datasets/{dataset_id}", dependencies=[api])
def dataset_detail(dataset_id: str):
    return ds_public(get_dataset(dataset_id), latest_job(dataset_id))


@app.get("/api/datasets/{dataset_id}/preview", dependencies=[api])
def dataset_preview(dataset_id: str):
    ds = get_dataset(dataset_id)
    preview = read_raw_csv(service.raw_path(dataset_id), nrows=200)
    return {"suggested_mapping": suggest_mapping(list(preview.columns)), "columns": ds.columns,
            "preview": preview.head(8).fillna("").to_dict(orient="records")}


@app.delete("/api/datasets/{dataset_id}", dependencies=[api])
def remove_dataset(dataset_id: str):
    ds = get_dataset(dataset_id)
    if ds.status == "processing":
        raise HTTPException(409, "Wait for processing to finish before deleting.")
    service.delete_dataset(dataset_id)
    return {"deleted": dataset_id}


def _parse_period(start: str | None, end: str | None, days: int, as_of: pd.Timestamp, data_start: pd.Timestamp) -> Period:
    if start and end:
        try:
            p = Period(pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize())
        except ValueError as e:
            raise HTTPException(422, "Dates must be YYYY-MM-DD.") from e
        if p.end < p.start:
            raise HTTPException(422, "The end date is before the start date.")
        if p.start < data_start or p.end > as_of:
            raise HTTPException(422, f"Choose dates between {data_start.date()} and {as_of.date()} (the last complete day).")
        return p
    return trailing_period(as_of, days)


@app.get("/api/datasets/{dataset_id}/kpis", dependencies=[api])
def kpis(dataset_id: str, days: int = Query(28, ge=1, le=730), start: str | None = None, end: str | None = None):
    ds = ready_dataset(dataset_id)
    clean, daily = service.load_clean(dataset_id), service.load_daily(dataset_id)
    as_of = last_complete_day(daily)
    cur = _parse_period(start, end, days, as_of, daily.index.min())
    out = compare_periods(clean, daily, cur)
    rows = slice_period(clean, cur)
    out["breakdowns"] = {d: breakdown(rows, d) for d in caps_mod.available_dimensions(ds.capabilities)}
    out["as_of"] = as_of.date().isoformat()
    out["currency"] = ds.currency
    out["definitions"] = {
        "net_revenue": "Sales minus returns and cancellations.",
        "transactions": "Distinct sale transactions (not line items).",
        "avg_transaction_value": "Net revenue divided by transactions.",
        "active_customers": "Distinct customers with an ID who bought in the period; anonymous sales are not counted.",
        "growth": "Change versus the immediately preceding period of the same length.",
    }
    return jsonable(out)


@app.get("/api/datasets/{dataset_id}/timeseries", dependencies=[api])
def timeseries(dataset_id: str, freq: str = Query("D", pattern="^(D|W|M)$")):
    ds = ready_dataset(dataset_id)
    daily = service.load_daily(dataset_id)
    d = resample_series(daily, freq)
    cols = [c for c in ["net_revenue", "gross_revenue", "returns_value", "transactions", "units", "customers",
                        "status", "days_with_sales", "days_gap"] if c in d.columns]
    if freq != "D":
        cols = [c for c in cols if c != "customers"]
    rows = [{"date": i.date().isoformat(), **{c: r[c] for c in cols}} for i, r in d.iterrows()]
    return jsonable({"freq": freq, "currency": ds.currency, "rows": rows,
                     "note": "Weekly/monthly transactions are the sum of daily distinct counts. Customers per period are "
                             "not additive across days and are only shown daily."})


@app.get("/api/datasets/{dataset_id}/overview", dependencies=[api])
def overview(dataset_id: str):
    ds = ready_dataset(dataset_id)
    clean, daily = service.load_clean(dataset_id), service.load_daily(dataset_id)
    as_of = last_complete_day(daily)
    cmp_ = compare_periods(clean, daily, trailing_period(as_of, 28))
    fc = result(dataset_id, "forecast") or {}
    fsum = None
    for h in ("30", "7", "90"):
        e = (fc.get("horizons") or {}).get(h)
        if e and e.get("enabled"):
            los = [p["lo"] for p in e["forecast"] if p["lo"] is not None]
            his = [p["hi"] for p in e["forecast"] if p["hi"] is not None]
            fsum = {"horizon": int(h), "total": e["forecast_total"], "model": e["selected_label"],
                    "from": e["forecast"][0]["date"], "to": e["forecast"][-1]["date"],
                    "test_wmape": next(c["test"]["wmape"] for c in e["comparison"] if c["model"] == e["selected_model"]),
                    "has_interval": len(los) == len(e["forecast"]) and len(his) == len(e["forecast"])}
            break
    an = result(dataset_id, "anomalies")
    recs = result(dataset_id, "recommendations")
    weekly = resample_series(daily, "W")
    recent = []
    if an:
        cutoff = (as_of - pd.Timedelta(days=60)).date().isoformat()
        recent = [{k: v for k, v in a.items() if k != "context"} for a in an["alerts"] if a["date"] >= cutoff][:5]
    return jsonable({
        "as_of": as_of.date().isoformat(), "currency": ds.currency, "comparison": cmp_,
        "weekly": [{"date": i.date().isoformat(), "net_revenue": r.net_revenue, "days_gap": r.days_gap} for i, r in weekly.iterrows()],
        "forecast": fsum, "forecast_available": fsum is not None,
        "anomalies": recent, "anomalies_available": an is not None,
        "recommendations": (recs or {}).get("items", [])[:3],
    })


@app.get("/api/datasets/{dataset_id}/forecast", dependencies=[api])
def forecast(dataset_id: str):
    ds = ready_dataset(dataset_id)
    fc = result(dataset_id, "forecast")
    daily = service.load_daily(dataset_id)
    d = daily[daily["status"] != "partial"].tail(180)
    history = [{"date": i.date().isoformat(), "net_revenue": r.net_revenue, "status": r.status} for i, r in d.iterrows()]
    return jsonable({"currency": ds.currency, "history": history, **(fc or {})})


@app.get("/api/datasets/{dataset_id}/customers", dependencies=[api])
def customers(dataset_id: str):
    ds = ready_dataset(dataset_id)
    require_cap(ds, "customer_rfm")
    return {"currency": ds.currency, **(result(dataset_id, "customers") or {})}


@app.get("/api/datasets/{dataset_id}/anomalies", dependencies=[api])
def anomalies(dataset_id: str):
    ds = ready_dataset(dataset_id)
    require_cap(ds, "anomalies")
    return {"currency": ds.currency, **(result(dataset_id, "anomalies") or {})}


@app.get("/api/datasets/{dataset_id}/diagnostics", dependencies=[api])
def diagnostics(dataset_id: str, a_start: str | None = None, a_end: str | None = None,
                b_start: str | None = None, b_end: str | None = None, days: int = Query(28, ge=1, le=365)):
    """a = baseline period, b = comparison period. Default: the last `days` complete days vs the days before."""
    ds = ready_dataset(dataset_id)
    require_cap(ds, "diagnostics")
    clean, daily = service.load_clean(dataset_id), service.load_daily(dataset_id)
    as_of, start = last_complete_day(daily), daily.index.min()
    b = _parse_period(b_start, b_end, days, as_of, start)
    a = _parse_period(a_start, a_end, days, as_of, start) if (a_start and a_end) else b.previous()
    if a.start < start:
        raise HTTPException(422, "The baseline period starts before the first day of data. Choose a shorter period.")
    has_c = caps_mod.enabled(ds.capabilities, "customer_rfm")
    out = diagnose(clean, a, b, caps_mod.available_dimensions(ds.capabilities), has_c,
                   service.load_segments(dataset_id) if has_c else None)
    out["currency"] = ds.currency
    out["data_range"] = {"start": start.date().isoformat(), "end": as_of.date().isoformat()}
    return jsonable(out)


@app.get("/api/datasets/{dataset_id}/recommendations", dependencies=[api])
def recommendations(dataset_id: str):
    ds = ready_dataset(dataset_id)
    return {"currency": ds.currency, **(result(dataset_id, "recommendations") or {"items": []})}


# Serve the built frontend from the same origin when STATIC_DIR is set (used by the Docker image).
if settings.static_dir and os.path.isdir(settings.static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(settings.static_dir, "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Not found.")
        return FileResponse(os.path.join(settings.static_dir, "index.html"))
