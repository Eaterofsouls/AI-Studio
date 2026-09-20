"""
Temporal activities for AI Cinema Studio Engine.
Each activity encapsulates an asynchronous, retriable step of the 26-step Production SOP.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional
try:
    from temporalio import activity
except ImportError:
    # Lightweight fallback decorator when temporalio is loading/testing
    class ActivityShim:
        @staticmethod
        def defn(func):
            return func
    activity = ActivityShim()

from cinema_engine.models import (
    ColorGradeParams,
    GenerationParams,
    JobStatus,
)
from cinema_engine.post.encode import encode_all_platforms
from cinema_engine.post.grading import apply_color_grade
from cinema_engine.post.remotion_render import RemotionProps, render_remotion_composition
from cinema_engine.providers.registry import get_provider_registry
from cinema_engine.rag.query import get_rag_service


@activity.defn
async def parse_brief_activity(brief: Dict[str, Any]) -> Dict[str, Any]:
    """Parse and validate client brief, structure initial shotlist."""
    shots = brief.get("shots", [])
    if not shots:
        # Generate default 3-shot commercial structure if none provided
        shots = [
            {"scene": 1, "shot": 1, "description": "Wide establishing cinematic shot", "duration_sec": 5},
            {"scene": 1, "shot": 2, "description": "Close up character portrait with dramatic lighting", "duration_sec": 5},
            {"scene": 1, "shot": 3, "description": "Hero product reveal dynamic motion", "duration_sec": 5},
        ]
    return {
        "project_id": brief.get("project_id", "proj_default"),
        "client": brief.get("client", "Standard Client"),
        "project_name": brief.get("project_name", "Commercial"),
        "format": brief.get("format", "cinematic_commercial"),
        "shots": shots,
        "script": brief.get("script", ""),
    }


@activity.defn
async def query_rag_presets_activity(shot: Dict[str, Any]) -> Dict[str, Any]:
    """Query virtual cinematography presets (Camera, Lighting, Effects) for a shot."""
    rag = get_rag_service()
    directive = await rag.assemble_cinematography(
        shot_description=shot.get("description", ""),
        camera_query=shot.get("camera_query"),
        lighting_query=shot.get("lighting_query"),
        effects_query=shot.get("effects_query"),
    )
    result = dict(shot)
    result["assembled_prompt"] = directive.full_prompt
    result["cinematography"] = directive.model_dump()
    return result


@activity.defn
async def generate_video_shot_activity(shot: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch video generation to configured video provider."""
    registry = get_provider_registry()
    provider_name = shot.get("provider")
    model_name = shot.get("model", "wan-2.6")
    provider = registry.get_video_provider(provider_name)

    params = GenerationParams(
        prompt=shot.get("assembled_prompt") or shot.get("description", ""),
        duration_sec=shot.get("duration_sec", 5),
        aspect_ratio=shot.get("aspect_ratio", "16:9"),
        model=model_name,
    )
    job = await provider.generate_video(params)
    return job.model_dump()


@activity.defn
async def generate_audio_activity(script_text: str, voice_id: Optional[str] = None) -> Dict[str, Any]:
    """Synthesize voiceover narration using ElevenLabs."""
    if not script_text.strip():
        return {"status": "skipped", "local_path": None}
    registry = get_provider_registry()
    audio_provider = registry.get_audio_provider("elevenlabs")
    job = await audio_provider.generate_speech(text=script_text, voice_id=voice_id)
    return job.model_dump()


@activity.defn
async def apply_lipsync_activity(video_path: str, audio_path: str) -> Dict[str, Any]:
    """Synchronize video facial motion to audio narration via Sync Labs."""
    registry = get_provider_registry()
    lipsync = registry.get_lipsync_provider("synclabs")
    job = await lipsync.apply_lipsync(video_url_or_path=video_path, audio_url_or_path=audio_path)
    return job.model_dump()


@activity.defn
async def color_grade_activity(video_path: str, grade_params: Optional[Dict[str, Any]] = None) -> str:
    """Apply 3D LUT film stock color grading and grain via FFmpeg."""
    params = ColorGradeParams(**(grade_params or {}))
    graded_file = await apply_color_grade(video_path, params=params)
    return str(graded_file)


@activity.defn
async def remotion_render_activity(props_data: Dict[str, Any]) -> str:
    """Composite clips, audio, branding, and motion graphics."""
    props = RemotionProps(**props_data)
    rendered = await render_remotion_composition(props)
    return str(rendered)


@activity.defn
async def encode_platforms_activity(video_path: str) -> Dict[str, str]:
    """Generate delivery variants for YouTube, Shorts/Reels/TikTok, and LinkedIn."""
    return await encode_all_platforms(video_path)
