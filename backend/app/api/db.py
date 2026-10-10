"""Database layer (PostgreSQL in deployment; SQLite works for tests).

Tables: datasets (metadata, mapping, quality report, capabilities), jobs (background status),
daily_metrics (one row per dataset-day), analysis_results (saved JSON output per module).
Line items stay in a Parquet file per dataset on disk; customer-level rows never leave the server.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings

SCHEMA_VERSION = 1


def _uid() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="uploaded")  # uploaded|processing|ready|failed
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_demo: Mapped[int] = mapped_column(Integer, default=0)
    columns: Mapped[list | None] = mapped_column(JSON, nullable=True)
    settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quality: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    capabilities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_uid)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|succeeded|failed
    stage: Mapped[str] = mapped_column(String(200), default="Queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DailyMetric(Base):
    __tablename__ = "daily_metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    date: Mapped[dt.date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10))
    net_revenue: Mapped[float] = mapped_column(Float)
    gross_revenue: Mapped[float] = mapped_column(Float)
    returns_value: Mapped[float] = mapped_column(Float)
    transactions: Mapped[float] = mapped_column(Float)
    customers: Mapped[float] = mapped_column(Float)
    units: Mapped[float | None] = mapped_column(Float, nullable=True)
    __table_args__ = (UniqueConstraint("dataset_id", "date"),)


class AnalysisResult(Base):
    __tablename__ = "analysis_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # forecast|customers|anomalies|recommendations
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    __table_args__ = (UniqueConstraint("dataset_id", "kind"),)


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)


_connect = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=_connect)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    """Create tables if missing and record the schema version (run at start-up and by `python -m app.api.db`)."""
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        if s.get(SchemaVersion, SCHEMA_VERSION) is None:
            s.add(SchemaVersion(version=SCHEMA_VERSION))
            s.commit()
        # jobs that were running when the process stopped cannot be resumed
        for job in s.query(Job).filter(Job.status.in_(["queued", "running"])).all():
            job.status, job.error = "failed", "The server restarted while this job was running. Please start it again."
            ds = s.get(Dataset, job.dataset_id)
            if ds and ds.status == "processing":
                ds.status = "failed"
        s.commit()


if __name__ == "__main__":
    init_db()
    print("database ready:", engine.url.render_as_string(hide_password=True))
