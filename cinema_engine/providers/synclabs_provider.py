"""
Sync Labs provider adapter for AI Cinema Studio Engine.
Provides production-grade video and audio lip synchronization via Sync Labs REST API.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import uuid4
import httpx

from cinema_engine.config import get_settings
from cinema_engine.models import JobResponse, JobStatus, MediaType
from cinema_engine.providers.base import LipSyncProvider


class SyncLabsProvider(LipSyncProvider):
    """Async provider adapter for Sync Labs (sync.so) lip-sync API."""

    NAME = "synclabs"
    BASE_URL = "https://api.sync.so/v2"

    def __init__(self, api_key: Optional[str] = None):
        self.settings = get_settings()
        self.api_key = api_key or self.settings.synclabs_api_key or ""

    @property
    def name(self) -> str:
        return self.NAME

    def is_configured(self) -> bool:
        return bool(self.api_key and not self.api_key.startswith("your_"))

    def estimate_cost(self, duration_sec: float = 5.0) -> Decimal:
        """Estimate cost: ~$0.30 per 15s sequence."""
        return Decimal("0.3500")

    async def apply_lipsync(
        self,
        video_url_or_path: str,
        audio_url_or_path: str,
        model: str = "lipsync-2",
        **kwargs,
    ) -> JobResponse:
        """Synchronize mouth and facial motion to the driving audio."""
        job_id = str(uuid4())
        cost = self.estimate_cost()

        job = JobResponse(
            job_id=job_id,
            provider=self.name,
            model=model,
            media_type=MediaType.LIPSYNC,
            status=JobStatus.PENDING,
            prompt=f"LipSync: {video_url_or_path} with {audio_url_or_path}",
            cost_usd=cost,
        )

        if not self.is_configured():
            job.status = JobStatus.FAILED
            job.error = "Sync Labs API key (SYNCLABS_API_KEY) not configured."
            job.completed_at = datetime.now(timezone.utc)
            return job

        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "videoUrl": video_url_or_path,
            "audioUrl": audio_url_or_path,
            "model": model,
        }

        async with httpx.AsyncClient(timeout=180.0) as client:
            try:
                job.status = JobStatus.PROCESSING
                resp = await client.post(f"{self.BASE_URL}/generate", json=payload, headers=headers)
                if resp.status_code not in (200, 201):
                    job.status = JobStatus.FAILED
                    job.error = f"Sync Labs submission failed ({resp.status_code}): {resp.text[:200]}"
                    job.completed_at = datetime.now(timezone.utc)
                    return job

                gen_data = resp.json()
                sync_id = gen_data.get("id")

                for _ in range(60):
                    await asyncio.sleep(3.0)
                    st_resp = await client.get(f"{self.BASE_URL}/generate/{sync_id}", headers=headers)
                    if st_resp.status_code != 200:
                        continue
                    st_data = st_resp.json()
                    st_status = st_data.get("status")

                    if st_status == "COMPLETED":
                        out_url = st_data.get("outputUrl")
                        if out_url:
                            job.output_url = out_url
                            job.status = JobStatus.COMPLETED
                            dest = self.settings.storage_dir / "raw" / f"{job_id}_synced.mp4"
                            dl = await client.get(out_url)
                            dest.write_bytes(dl.content)
                            job.local_path = str(dest)
                            job.completed_at = datetime.now(timezone.utc)
                            return job

                    elif st_status in ("FAILED", "ERROR"):
                        job.status = JobStatus.FAILED
                        job.error = f"Sync Labs failed: {st_data.get('error')}"
                        job.completed_at = datetime.now(timezone.utc)
                        return job

                job.status = JobStatus.FAILED
                job.error = "Sync Labs timed out."
                job.completed_at = datetime.now(timezone.utc)
                return job

            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = f"Exception during Sync Labs sync: {str(e)}"
                job.completed_at = datetime.now(timezone.utc)
                return job
