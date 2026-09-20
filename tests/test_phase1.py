"""
Unit and integration tests for Phase 1 MVP Foundation.
Tests configuration, domain models, provider registry, color grading resolution,
database operations, and FastAPI endpoints.
"""

from decimal import Decimal
import pytest
import httpx
from sqlalchemy import select

from cinema_engine.api.main import app
from cinema_engine.config import Settings, get_settings
from cinema_engine.db import (
    CostModel,
    JobModel,
    get_db,
    get_session_factory,
    init_db,
    record_cost_entry,
    record_job,
)
from cinema_engine.models import (
    ColorGradeParams,
    GenerationParams,
    JobResponse,
    JobStatus,
    MediaType,
)
from cinema_engine.post.grading import (
    build_filter_chain,
    find_ffmpeg,
    list_available_luts,
    resolve_lut_path,
)
from cinema_engine.providers.fal_provider import FalProvider
from cinema_engine.providers.registry import get_provider_registry


@pytest.mark.asyncio
async def test_settings_initialization():
    """Verify settings loads defaults and creates required storage directories."""
    settings = get_settings()
    assert settings.app_env in ("development", "production", "test")
    assert settings.storage_dir.exists()
    assert (settings.storage_dir / "raw").exists()
    assert (settings.storage_dir / "graded").exists()


def test_generation_models_validation():
    """Verify Pydantic models enforce schema contracts."""
    params = GenerationParams(
        prompt="Cinematic drone shot of misty mountain peak at dawn",
        duration_sec=10,
        aspect_ratio="16:9",
        model="wan-2.6",
    )
    assert params.duration_sec == 10
    assert params.aspect_ratio == "16:9"

    job = JobResponse(
        job_id="test-job-123",
        provider="fal",
        model="wan-2.6",
        media_type=MediaType.VIDEO,
        status=JobStatus.PENDING,
        prompt=params.prompt,
        duration_sec=10.0,
        cost_usd=Decimal("1.0000"),
    )
    assert job.cost_usd == Decimal("1.0000")
    assert job.status == JobStatus.PENDING


def test_provider_registry_and_cost_estimation():
    """Verify provider registry routes to fal and calculates costs accurately."""
    registry = get_provider_registry()
    video_provider = registry.get_video_provider("fal")
    assert isinstance(video_provider, FalProvider)

    # 5 seconds of Wan 2.6 at $0.10/sec = $0.50
    params = GenerationParams(prompt="Test prompt", duration_sec=5, model="wan-2.6")
    cost = video_provider.estimate_cost(params)
    assert cost == Decimal("0.5000")

    # 10 seconds of Seedance at $0.50/sec = $5.00
    params_seedance = GenerationParams(prompt="Test prompt", duration_sec=10, model="seedance")
    cost_seedance = video_provider.estimate_cost(params_seedance)
    assert cost_seedance == Decimal("5.0000")


def test_color_grade_lut_resolution():
    """Verify LUT index reading and path resolution for cinema film stocks."""
    luts = list_available_luts()
    assert "film_stocks" in luts
    assert len(luts["film_stocks"]) > 0
    assert "kodak_vision3_500t" in luts["film_stocks"]

    # Verify path resolves to an existing .cube file
    cube_path = resolve_lut_path("kodak_vision3_500t")
    assert cube_path.exists()
    assert cube_path.suffix == ".cube"

    # Verify filter chain generation
    grade_params = ColorGradeParams(
        lut_chain=["neutral_normalize", "kodak_vision3_500t"],
        grain=0.15,
        vignette=0.3,
        deband=True,
    )
    filter_chain = build_filter_chain(grade_params)
    assert "deband=" in filter_chain
    assert "lut3d=file=" in filter_chain
    assert "noise=" in filter_chain
    assert "vignette=" in filter_chain


def test_ffmpeg_discovery():
    """Verify FFmpeg executable is discovered on system."""
    ffmpeg_exe = find_ffmpeg()
    assert ffmpeg_exe is not None
    assert "ffmpeg" in ffmpeg_exe.lower()


@pytest.mark.asyncio
async def test_database_initialization_and_operations():
    """Verify database tables creation and job recording."""
    await init_db()
    session_factory = get_session_factory()

    async with session_factory() as session:
        test_job = JobResponse(
            job_id="job-verify-456",
            provider="fal",
            model="wan-2.6",
            media_type=MediaType.VIDEO,
            status=JobStatus.COMPLETED,
            prompt="Close up portrait on 35mm film",
            duration_sec=5.0,
            cost_usd=Decimal("0.5000"),
            output_url="https://example.com/test.mp4",
        )
        await record_job(session, test_job)
        await record_cost_entry(
            session,
            job_id="job-verify-456",
            service="fal_wan2.6",
            amount_usd=Decimal("0.5000"),
            details={"test": True},
        )

        # Query back
        stmt = select(JobModel).where(JobModel.id == "job-verify-456")
        res = await session.execute(stmt)
        record = res.scalar_one_or_none()
        assert record is not None
        assert record.status == JobStatus.COMPLETED.value
        assert Decimal(str(record.cost_usd)) == Decimal("0.5000")


@pytest.mark.asyncio
async def test_fastapi_endpoints():
    """Verify FastAPI endpoints return expected responses."""
    await init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Health check
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["ffmpeg"]["available"] is True

        # Providers list
        resp = await client.get("/api/v1/providers")
        assert resp.status_code == 200
        providers = resp.json()
        assert "video" in providers
        assert "fal" in providers["video"]

        # LUTs list
        resp = await client.get("/api/v1/luts")
        assert resp.status_code == 200
        luts = resp.json()
        assert "film_stocks" in luts

        # Jobs list
        resp = await client.get("/api/v1/jobs")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

        # Costs summary
        resp = await client.get("/api/v1/costs")
        assert resp.status_code == 200
        costs = resp.json()
        assert "total_cost_usd" in costs
