"""
Database configuration and SQLAlchemy 2.0 async ORM models.
Supports PostgreSQL (production) and SQLite via aiosqlite (local development).
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncGenerator, Optional
from sqlalchemy import (
    Column,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    JSON,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

from cinema_engine.config import get_settings
from cinema_engine.models import JobResponse, JobStatus, MediaType

Base = declarative_base()


class ProjectModel(Base):
    __tablename__ = "projects"

    id = Column(String(50), primary_key=True)
    name = Column(String(200), nullable=False)
    client = Column(String(100), nullable=False)
    format = Column(String(50), default="cinematic_commercial")
    status = Column(String(50), default="queued")
    budget_ceiling_usd = Column(Numeric(10, 2), nullable=True)
    actual_cost_usd = Column(Numeric(10, 2), default=0.00)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class JobModel(Base):
    __tablename__ = "jobs"

    id = Column(String(50), primary_key=True)
    project_id = Column(String(50), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=False)
    media_type = Column(String(20), default=MediaType.VIDEO.value)
    status = Column(String(20), default=JobStatus.PENDING.value)
    prompt = Column(Text, nullable=False)
    duration_sec = Column(Numeric(6, 2), default=0.00)
    cost_usd = Column(Numeric(10, 4), default=0.0000)
    output_url = Column(String(1000), nullable=True)
    local_path = Column(String(500), nullable=True)
    graded_path = Column(String(500), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)


class CostModel(Base):
    __tablename__ = "costs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(50), nullable=True)
    project_id = Column(String(50), nullable=True)
    service = Column(String(50), nullable=False)
    amount_usd = Column(Numeric(10, 4), nullable=False)
    details = Column(JSON, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class GovernanceLogModel(Base):
    __tablename__ = "governance_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(50), nullable=True)
    action = Column(String(100), nullable=False)
    details = Column(JSON, nullable=True)
    severity = Column(String(20), default="info")
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


_engine: Optional[AsyncEngine] = None
_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        db_url = settings.database_url
        # Handle SQLite vs Postgres connection args
        connect_args = {}
        if db_url.startswith("sqlite"):
            connect_args = {"check_same_thread": False}
        _engine = create_async_engine(db_url, echo=settings.debug, connect_args=connect_args)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        engine = get_engine()
        _sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return _sessionmaker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for database sessions."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session


async def init_db() -> None:
    """Initialize database tables."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def record_job(session: AsyncSession, job: JobResponse) -> None:
    """Insert or update a job in the database."""
    db_job = await session.get(JobModel, job.job_id)
    if db_job is None:
        db_job = JobModel(
            id=job.job_id,
            provider=job.provider,
            model=job.model,
            media_type=job.media_type.value,
            status=job.status.value,
            prompt=job.prompt,
            duration_sec=Decimal(str(job.duration_sec)),
            cost_usd=job.cost_usd,
            output_url=job.output_url,
            local_path=job.local_path,
            graded_path=job.graded_path,
            error=job.error,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )
        session.add(db_job)
    else:
        db_job.status = job.status.value
        db_job.cost_usd = job.cost_usd
        db_job.output_url = job.output_url
        db_job.local_path = job.local_path
        db_job.graded_path = job.graded_path
        db_job.error = job.error
        db_job.completed_at = job.completed_at

    await session.commit()


async def record_cost_entry(
    session: AsyncSession,
    job_id: str,
    service: str,
    amount_usd: Decimal,
    project_id: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """Record an itemized cost entry."""
    cost = CostModel(
        job_id=job_id,
        project_id=project_id,
        service=service,
        amount_usd=amount_usd,
        details=details or {},
    )
    session.add(cost)
    await session.commit()
