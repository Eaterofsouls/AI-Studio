"""
ElevenLabs provider adapter for AI Cinema Studio Engine.
Generates voiceovers, narration, and sound effects via direct ElevenLabs API.
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional
from uuid import uuid4
import httpx

from cinema_engine.config import get_settings
from cinema_engine.models import JobResponse, JobStatus, MediaType
from cinema_engine.providers.base import AudioProvider


class ElevenLabsProvider(AudioProvider):
    """Async provider adapter for ElevenLabs TTS and audio generation."""

    NAME = "elevenlabs"
    BASE_URL = "https://api.elevenlabs.io/v1"
    DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel (default standard voice)

    def __init__(self, api_key: Optional[str] = None):
        self.settings = get_settings()
        self.api_key = api_key or self.settings.elevenlabs_api_key or ""

    @property
    def name(self) -> str:
        return self.NAME

    def is_configured(self) -> bool:
        return bool(self.api_key and not self.api_key.startswith("your_"))

    def estimate_cost(self, text: str, model_id: str = "eleven_turbo_v2_5") -> Decimal:
        """Calculate estimated cost based on character count."""
        char_count = len(text)
        # Turbo/Flash rate is ~$0.05 per 1,000 characters
        rate_per_char = Decimal("0.00005")
        if "multilingual" in model_id.lower():
            rate_per_char = Decimal("0.00010")
        return Decimal(str(char_count)) * rate_per_char

    async def generate_speech(
        self,
        text: str,
        voice_id: Optional[str] = None,
        model_id: str = "eleven_turbo_v2_5",
        **kwargs,
    ) -> JobResponse:
        """Synthesize voiceover audio from text."""
        job_id = str(uuid4())
        vid = voice_id or self.DEFAULT_VOICE_ID
        cost = self.estimate_cost(text, model_id)

        job = JobResponse(
            job_id=job_id,
            provider=self.name,
            model=model_id,
            media_type=MediaType.AUDIO,
            status=JobStatus.PENDING,
            prompt=text,
            cost_usd=cost,
        )

        if not self.is_configured():
            job.status = JobStatus.FAILED
            job.error = "ElevenLabs API key (ELEVENLABS_API_KEY) not configured."
            job.completed_at = datetime.now(timezone.utc)
            return job

        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": kwargs.get("stability", 0.5),
                "similarity_boost": kwargs.get("similarity_boost", 0.75),
            },
        }

        url = f"{self.BASE_URL}/text-to-speech/{vid}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                job.status = JobStatus.PROCESSING
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code != 200:
                    job.status = JobStatus.FAILED
                    job.error = f"ElevenLabs synthesis failed ({resp.status_code}): {resp.text[:200]}"
                    job.completed_at = datetime.now(timezone.utc)
                    return job

                dest_dir = self.settings.storage_dir / "audio"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / f"{job_id}.mp3"
                dest_path.write_bytes(resp.content)

                job.status = JobStatus.COMPLETED
                job.local_path = str(dest_path)
                job.completed_at = datetime.now(timezone.utc)
                return job

            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = f"Exception during ElevenLabs synthesis: {str(e)}"
                job.completed_at = datetime.now(timezone.utc)
                return job
