"""
Unit and integration tests for Phase 2:
RAG Presets Consolidation and Multi-Provider Adapters (Replicate, ElevenLabs, Sync Labs).
"""

from decimal import Decimal
import pytest
import httpx

from cinema_engine.api.main import app
from cinema_engine.db import init_db
from cinema_engine.models import GenerationParams
from cinema_engine.providers.elevenlabs_provider import ElevenLabsProvider
from cinema_engine.providers.fal_provider import FalProvider
from cinema_engine.providers.registry import get_provider_registry
from cinema_engine.providers.replicate_provider import ReplicateProvider
from cinema_engine.providers.synclabs_provider import SyncLabsProvider
from cinema_engine.rag.presets import load_all_presets, search_local_presets
from cinema_engine.rag.query import CinematographyDirective, get_rag_service


def test_rag_presets_loading():
    """Verify camera, lighting, and effects presets load correctly from JSON."""
    data = load_all_presets()
    assert len(data["camera"]) > 0
    assert len(data["lighting"]) > 0
    assert len(data["effects"]) > 0

    first_cam = data["camera"][0]
    assert hasattr(first_cam, "camera_body")
    assert hasattr(first_cam, "prompt_fragment")


def test_rag_local_search():
    """Verify local lexical search ranks relevant presets."""
    # Search for dramatic close-up
    results = search_local_presets("camera", "dramatic close up shallow depth of field", limit=3)
    assert len(results) > 0
    assert "prompt_fragment" in results[0]

    # Search for golden hour lighting
    light_results = search_local_presets("lighting", "golden hour warm sunset", limit=3)
    assert len(light_results) > 0
    assert "prompt_fragment" in light_results[0]

    # Search for vintage film stock
    fx_results = search_local_presets("effects", "vintage film stock grain kodak", limit=3)
    assert len(fx_results) > 0
    assert "prompt_fragment" in fx_results[0]


@pytest.mark.asyncio
async def test_rag_directive_assembly():
    """Verify CinematographyRAGService assembles full prompt directive."""
    rag = get_rag_service()
    shot_desc = "A cybernetic traveler standing in the neon rain"
    directive: CinematographyDirective = await rag.assemble_cinematography(
        shot_description=shot_desc,
        camera_query="anamorphic lens slow tracking shot",
        lighting_query="cyberpunk neon backlight",
        effects_query="kodak 500t film grain",
    )

    assert directive.full_prompt.startswith(shot_desc)
    assert len(directive.assembled_fragment) > 0
    assert directive.camera is not None
    assert directive.lighting is not None
    assert directive.effects is not None


def test_replicate_provider_cost_estimation():
    """Verify Replicate provider model resolution and cost estimation."""
    prov = ReplicateProvider(api_token="test_token")
    assert prov.name == "replicate"
    assert prov.is_configured() is True

    # 10s of Kling on Replicate at $0.30/s = $3.00
    p = GenerationParams(prompt="Test", duration_sec=10, model="kling")
    assert prov.estimate_cost(p) == Decimal("3.0000")


def test_elevenlabs_provider_cost_estimation():
    """Verify ElevenLabs character-based cost calculation."""
    prov = ElevenLabsProvider(api_key="test_key")
    assert prov.name == "elevenlabs"
    assert prov.is_configured() is True

    # 100 characters on Turbo @ $0.00005/char = $0.0050
    cost = prov.estimate_cost("A" * 100, model_id="eleven_turbo_v2_5")
    assert cost == Decimal("0.0050")


def test_synclabs_provider_cost_estimation():
    """Verify Sync Labs provider configuration and estimate."""
    prov = SyncLabsProvider(api_key="test_key")
    assert prov.name == "synclabs"
    assert prov.is_configured() is True
    assert prov.estimate_cost() > Decimal("0.0000")


def test_provider_registry_multi_provider_routing():
    """Verify multi-provider registration and priority fallback."""
    reg = get_provider_registry()
    listing = reg.list_providers()
    assert "fal" in listing["video"]
    assert "replicate" in listing["video"]
    assert "elevenlabs" in listing["audio"]
    assert "synclabs" in listing["lipsync"]

    # Explicit routing
    rep = reg.get_video_provider("replicate")
    assert isinstance(rep, ReplicateProvider)

    fal = reg.get_video_provider("fal")
    assert isinstance(fal, FalProvider)

    # Audio & Lip-sync
    audio = reg.get_audio_provider()
    assert isinstance(audio, ElevenLabsProvider)

    lipsync = reg.get_lipsync_provider()
    assert isinstance(lipsync, SyncLabsProvider)


@pytest.mark.asyncio
async def test_fastapi_phase2_endpoints():
    """Verify FastAPI Phase 2 endpoints."""
    await init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Preset search endpoint
        resp = await client.get("/api/v1/presets/search?category=camera&q=dolly")
        assert resp.status_code == 200
        presets = resp.json()
        assert isinstance(presets, list)
        assert len(presets) > 0

        # 2. Providers status
        resp = await client.get("/api/v1/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "audio" in data
        assert "lipsync" in data

        # 3. Cinematic generate endpoint (mock/unconfigured provider graceful failure)
        cinematic_payload = {
            "shot_description": "Detective entering a shadowy office",
            "camera_query": "handheld close up",
            "lighting_query": "film noir shadows",
            "model": "wan-2.6",
            "auto_grade": False,
        }
        resp = await client.post("/api/v1/generate/cinematic", json=cinematic_payload)
        assert resp.status_code == 200
        job_data = resp.json()
        assert "job_id" in job_data
        assert "film noir" in job_data["prompt"].lower() or "shadow" in job_data["prompt"].lower()
