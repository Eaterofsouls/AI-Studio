"""
Domain models and schema definitions for AI Cinema Studio Engine.
Follows Pydantic v2 conventions and strict typing.
"""

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4
from pydantic import BaseModel, Field


class MediaType(str, Enum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"
    LIPSYNC = "lipsync"


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class AspectRatio(str, Enum):
    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"
    SQUARE = "1:1"
    STANDARD = "4:3"
    CINEMATIC = "21:9"


class ColorGradeParams(BaseModel):
    """Parameters for FFmpeg LUT color grading."""
    lut_chain: List[str] = Field(default_factory=lambda: ["neutral_normalize", "kodak_vision3_500t"])
    grain: float = Field(default=0.12, ge=0.0, le=1.0)
    vignette: float = Field(default=0.25, ge=0.0, le=1.0)
    exposure: float = Field(default=0.0, ge=-1.0, le=1.0)
    contrast: float = Field(default=0.0, ge=-1.0, le=1.0)
    saturation: float = Field(default=0.0, ge=-1.0, le=1.0)
    deband: bool = Field(default=True)


class GenerationParams(BaseModel):
    """Parameters for media generation via provider adapters."""
    prompt: str
    negative_prompt: Optional[str] = None
    duration_sec: int = Field(default=5, ge=1, le=60)
    aspect_ratio: str = Field(default="16:9")
    width: Optional[int] = None
    height: Optional[int] = None
    ref_image_url: Optional[str] = None
    seed: Optional[int] = None
    model: str = "wan-2.6"
    extra_params: Dict[str, Any] = Field(default_factory=dict)


class DirectGenerateRequest(BaseModel):
    """User request for immediate video/image generation."""
    prompt: str
    model: str = Field(default="wan-2.6", description="Target model key or endpoint")
    provider: Optional[str] = Field(default=None, description="Optional provider override (e.g. fal, replicate)")
    duration_sec: int = Field(default=5, ge=1, le=30)
    aspect_ratio: str = Field(default="16:9")
    ref_image_url: Optional[str] = None
    seed: Optional[int] = None
    auto_grade: bool = Field(default=True, description="Automatically run FFmpeg color grading on completion")
    grading: Optional[ColorGradeParams] = None


class CinematicGenerateRequest(BaseModel):
    """Request for RAG-assembled virtual cinematography generation."""
    shot_description: str = Field(description="Narrative action or subject description")
    camera_query: Optional[str] = Field(default=None, description="Camera direction override (e.g. 'dolly in close up')")
    lighting_query: Optional[str] = Field(default=None, description="Lighting direction override (e.g. 'moody golden hour')")
    effects_query: Optional[str] = Field(default=None, description="Style / film stock override (e.g. '35mm grain')")
    model: str = Field(default="wan-2.6", description="Generation model key")
    provider: Optional[str] = Field(default=None, description="Provider key (fal or replicate)")
    duration_sec: int = Field(default=5, ge=1, le=30)
    aspect_ratio: str = Field(default="16:9")
    ref_image_url: Optional[str] = None
    seed: Optional[int] = None
    auto_grade: bool = Field(default=True)
    grading: Optional[ColorGradeParams] = None


class SpeechGenerateRequest(BaseModel):
    """Request for voiceover audio generation via ElevenLabs."""
    text: str = Field(description="Script or dialogue text")
    voice_id: Optional[str] = Field(default=None, description="ElevenLabs voice ID")
    model_id: str = Field(default="eleven_turbo_v2_5")


class LipSyncRequest(BaseModel):
    """Request for lip synchronization via Sync Labs."""
    video_url_or_path: str = Field(description="URL or local path to driving video")
    audio_url_or_path: str = Field(description="URL or local path to driving audio")
    model: str = Field(default="lipsync-2")


class JobResponse(BaseModel):
    """Represents the status and metadata of a generation job."""
    job_id: str = Field(default_factory=lambda: str(uuid4()))
    provider: str
    model: str
    media_type: MediaType = MediaType.VIDEO
    status: JobStatus = JobStatus.PENDING
    prompt: str
    duration_sec: float = 0.0
    cost_usd: Decimal = Field(default=Decimal("0.0000"))
    output_url: Optional[str] = None
    local_path: Optional[str] = None
    graded_path: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None


class CostRecord(BaseModel):
    """Cost tracking entry."""
    id: Optional[int] = None
    job_id: str
    project_id: Optional[str] = None
    service: str
    amount_usd: Decimal
    details: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CostSummary(BaseModel):
    """Aggregated cost summary."""
    total_cost_usd: Decimal
    total_transactions: int
    by_service: Dict[str, Decimal]
    by_model: Dict[str, Decimal]


class ProjectCreateRequest(BaseModel):
    name: str
    client: str
    format: str = "cinematic_commercial"
    budget_ceiling_usd: Optional[Decimal] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    client: str
    format: str
    status: str
    budget_ceiling_usd: Optional[Decimal] = None
    actual_cost_usd: Decimal = Decimal("0.00")
    created_at: datetime
    updated_at: datetime
