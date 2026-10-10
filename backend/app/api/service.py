"""Dataset storage, background jobs and cached access to cleaned tables."""
from __future__ import annotations

import datetime as dt
import logging
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import pandas as pd

from ..core.ingest import IngestError
from ..core.schema import IngestSettings
from ..pipeline import run_pipeline
from .config import settings
from .db import AnalysisResult, DailyMetric, Dataset, Job, SessionLocal

log = logging.getLogger("sme-di")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")  # one heavy job at a time
_lock = threading.Lock()


def dataset_dir(dataset_id: str) -> str:
    return os.path.join(settings.data_dir, "datasets", dataset_id)


def raw_path(dataset_id: str) -> str:
    return os.path.join(dataset_dir(dataset_id), "raw.csv")


@lru_cache(maxsize=2)
def _load_clean(dataset_id: str, mtime: float) -> pd.DataFrame:
    return pd.read_parquet(os.path.join(dataset_dir(dataset_id), "clean.parquet"))


def load_clean(dataset_id: str) -> pd.DataFrame:
    path = os.path.join(dataset_dir(dataset_id), "clean.parquet")
    return _load_clean(dataset_id, os.path.getmtime(path))


def load_segments(dataset_id: str) -> pd.Series | None:
    path = os.path.join(dataset_dir(dataset_id), "customers.parquet")
    if not os.path.exists(path):
        return None
    return pd.read_parquet(path, columns=["customer_id", "segment"]).set_index("customer_id")["segment"]


def load_daily(dataset_id: str) -> pd.DataFrame:
    with SessionLocal() as s:
        rows = s.query(DailyMetric).filter_by(dataset_id=dataset_id).order_by(DailyMetric.date).all()
    df = pd.DataFrame([{"date": pd.Timestamp(r.date), "status": r.status, "net_revenue": r.net_revenue,
                        "gross_revenue": r.gross_revenue, "returns_value": r.returns_value,
                        "transactions": r.transactions, "customers": r.customers, "units": r.units} for r in rows])
    df = df.set_index("date")
    df["dow"] = df.index.dayofweek
    return df


def _run_job(job_id: str, dataset_id: str) -> None:
    def progress(stage: str, pct: int):
        with SessionLocal() as s:
            job = s.get(Job, job_id)
            job.status, job.stage, job.progress = "running", stage[:200], int(pct)
            s.commit()

    try:
        with SessionLocal() as s:
            ds = s.get(Dataset, dataset_id)
            ing = IngestSettings.from_dict(ds.settings)
        out = run_pipeline(raw_path(dataset_id), dataset_dir(dataset_id), ing, progress)
        daily = out.pop("daily")
        with SessionLocal() as s:
            s.query(DailyMetric).filter_by(dataset_id=dataset_id).delete()
            s.query(AnalysisResult).filter_by(dataset_id=dataset_id).delete()
            s.bulk_save_objects([
                DailyMetric(dataset_id=dataset_id, date=d.date(), status=r.status, net_revenue=float(r.net_revenue),
                            gross_revenue=float(r.gross_revenue), returns_value=float(r.returns_value),
                            transactions=float(r.transactions), customers=float(r.customers),
                            units=None if pd.isna(r.units) else float(r.units))
                for d, r in daily.iterrows()])
            for kind in ("forecast", "customers", "anomalies", "recommendations"):
                s.add(AnalysisResult(dataset_id=dataset_id, kind=kind, payload=out[kind]))
            ds = s.get(Dataset, dataset_id)
            ds.quality, ds.capabilities, ds.status = out["quality"], out["capabilities"], "ready"
            job = s.get(Job, job_id)
            job.status, job.stage, job.progress, job.finished_at = "succeeded", "Done", 100, dt.datetime.now(dt.timezone.utc)
            s.commit()
        _load_clean.cache_clear()
    except Exception as e:  # noqa: BLE001 - the job must always record its outcome
        user_msg = str(e) if isinstance(e, IngestError) else "Processing failed unexpectedly. The error has been logged."
        log.exception("job %s failed", job_id)
        with SessionLocal() as s:
            job = s.get(Job, job_id)
            job.status, job.error, job.finished_at = "failed", user_msg, dt.datetime.now(dt.timezone.utc)
            ds = s.get(Dataset, dataset_id)
            if ds:
                ds.status = "failed"
            s.commit()


def start_job(dataset_id: str) -> str:
    with SessionLocal() as s:
        job = Job(dataset_id=dataset_id)
        s.add(job)
        ds = s.get(Dataset, dataset_id)
        ds.status = "processing"
        s.commit()
        job_id = job.id
    if settings.jobs_sync:
        _run_job(job_id, dataset_id)
    else:
        _executor.submit(_run_job, job_id, dataset_id)
    return job_id


def delete_dataset(dataset_id: str) -> None:
    with SessionLocal() as s:
        for model in (DailyMetric, AnalysisResult, Job):
            s.query(model).filter_by(dataset_id=dataset_id).delete()
        ds = s.get(Dataset, dataset_id)
        if ds:
            s.delete(ds)
        s.commit()
    shutil.rmtree(dataset_dir(dataset_id), ignore_errors=True)
    _load_clean.cache_clear()
