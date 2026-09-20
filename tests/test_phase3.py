"""
Unit and integration tests for Phase 3:
Temporal Workflows, Remotion Compositing, Platform Encoding, and Human Phase Gates.
"""

from decimal import Decimal
import pytest
import httpx

from cinema_engine.api.main import app
from cinema_engine.db import init_db
from cinema_engine.post.encode import PLATFORM_SPECS, encode_platform_variant
from cinema_engine.post.remotion_render import RemotionProps, render_remotion_composition
from cinema_engine.workflows.activities import (
    color_grade_activity,
    parse_brief_activity,
    query_rag_presets_activity,
)
from cinema_engine.workflows.production import ProductionWorkflow


def test_platform_specs_validation():
    """Verify platform encoding specs exist for YouTube, Shorts/Reels, and Square."""
    assert "youtube_16_9" in PLATFORM_SPECS
    assert "shorts_reels_9_16" in PLATFORM_SPECS
    assert "square_1_1" in PLATFORM_SPECS


@pytest.mark.asyncio
async def test_remotion_fallback_render():
    """Verify Remotion video rendering pipeline produces valid video file."""
    props = RemotionProps(
        composition_id="Generic",
        title="PopTech AI Commercial",
        description="Automated Production",
    )
    rendered = await render_remotion_composition(props)
    assert rendered.exists()
    assert rendered.stat().st_size > 0


@pytest.mark.asyncio
async def test_platform_variant_encoding():
    """Verify platform encoding produces compliant YouTube and Shorts variants."""
    props = RemotionProps(title="Platform Encode Test")
    master = await render_remotion_composition(props)

    yt_variant = await encode_platform_variant(master, "youtube_16_9")
    assert yt_variant.exists()
    assert yt_variant.stat().st_size > 0

    shorts_variant = await encode_platform_variant(master, "shorts_reels_9_16")
    assert shorts_variant.exists()
    assert shorts_variant.stat().st_size > 0


@pytest.mark.asyncio
async def test_production_activities():
    """Verify individual Temporal activities run smoothly."""
    brief = {
        "project_name": "SOP Test Production",
        "client": "Acme Brand",
        "shots": [
            {"scene": 1, "shot": 1, "description": "Hero entering cyberpunk city", "duration_sec": 5},
        ],
    }
    parsed = await parse_brief_activity(brief)
    assert parsed["project_name"] == "SOP Test Production"
    assert len(parsed["shots"]) == 1

    shot_with_rag = await query_rag_presets_activity(parsed["shots"][0])
    assert "assembled_prompt" in shot_with_rag
    assert "cinematography" in shot_with_rag


def test_production_workflow_gates_and_state():
    """Verify ProductionWorkflow state progression and signal handling."""
    wf = ProductionWorkflow()
    status = wf.get_production_status()
    assert status["phase"] == "init"
    assert status["gate_1_approved"] is False

    # Signal Gate 1
    wf.approve_gate(1)
    status = wf.get_production_status()
    assert status["gate_1_approved"] is True

    # Signal Gate 2 & 3
    wf.approve_gate(2)
    wf.approve_gate(3)
    status = wf.get_production_status()
    assert status["gate_2_approved"] is True
    assert status["gate_3_approved"] is True


@pytest.mark.asyncio
async def test_fastapi_phase3_project_and_gate_endpoints():
    """Verify FastAPI endpoints for project creation and Human Phase Gate approval."""
    await init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create Project
        resp = await client.post(
            "/api/v1/projects",
            json={"name": "Cyberpunk Ad", "client": "TechCorp", "budget_ceiling_usd": 150.0},
        )
        assert resp.status_code == 200
        proj_data = resp.json()
        proj_id = proj_data["id"]
        assert proj_data["status"] == "pre_production"

        # Approve Gate 1
        resp = await client.post(f"/api/v1/projects/{proj_id}/approve-gate/1")
        assert resp.status_code == 200
        assert resp.json()["project_status"] == "production"

        # Approve Gate 2
        resp = await client.post(f"/api/v1/projects/{proj_id}/approve-gate/2")
        assert resp.status_code == 200
        assert resp.json()["project_status"] == "post_production"

        # Approve Gate 3
        resp = await client.post(f"/api/v1/projects/{proj_id}/approve-gate/3")
        assert resp.status_code == 200
        assert resp.json()["project_status"] == "published"
