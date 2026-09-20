"""
Abstract base interfaces for AI media providers.
Ensures uniform contract across fal.ai, Replicate, Vertex AI, ElevenLabs, Sync Labs, etc.
"""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional
from cinema_engine.models import GenerationParams, JobResponse


class BaseProvider(ABC):
    """Base interface for all external API providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for the provider (e.g. 'fal', 'replicate')."""
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Check whether required API credentials/configs are present."""
        pass


class VideoProvider(BaseProvider):
    """Interface for video generation engines."""

    @abstractmethod
    async def generate_video(self, params: GenerationParams) -> JobResponse:
        """
        Submit and wait for completion of a video generation task.
        Downloads the generated video to local storage if successful.
        """
        pass

    @abstractmethod
    def estimate_cost(self, params: GenerationParams) -> Decimal:
        """Estimate the generation cost in USD."""
        pass


class ImageProvider(BaseProvider):
    """Interface for image generation engines."""

    @abstractmethod
    async def generate_image(self, params: GenerationParams) -> JobResponse:
        """Submit and wait for an image generation task."""
        pass

    @abstractmethod
    def estimate_cost(self, params: GenerationParams) -> Decimal:
        """Estimate the image generation cost in USD."""
        pass


class AudioProvider(BaseProvider):
    """Interface for voice and music synthesis."""

    @abstractmethod
    async def generate_speech(self, text: str, voice_id: Optional[str] = None, **kwargs) -> JobResponse:
        """Synthesize speech audio from text."""
        pass


class LipSyncProvider(BaseProvider):
    """Interface for video + audio lip synchronization."""

    @abstractmethod
    async def apply_lipsync(
        self,
        video_url_or_path: str,
        audio_url_or_path: str,
        **kwargs,
    ) -> JobResponse:
        """Synchronize video mouth movements to an audio track."""
        pass
