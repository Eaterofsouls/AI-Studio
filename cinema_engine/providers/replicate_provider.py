"""
Replicate provider adapter for AI Cinema Studio Engine.
Provides alternative gateway for video and image generation models.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import uuid4
import httpx

from cinema_engine.config import get_settings
from cinema_engine.models import (
    GenerationParams,
    JobResponse,
    JobStatus,
    MediaType,
)
from cinema_engine.providers.base import ImageProvider, VideoProvider


class ReplicateProvider(VideoProvider, ImageProvider):
    """Async provider adapter for Replicate API."""

    NAME = "replicate"
    BASE_URL = "https://api.replicate.com/v1"

    MODELS_CATALOG: Dict[str, Dict[str, Any]] = {
        "kling": {
            "model": "kwaivgi/kling-v1.6-standard",
            "type": MediaType.VIDEO,
            "cost_per_sec": Decimal("0.3000"),
        },
        "minimax": {
            "model": "minimax/video-01",
            "type": MediaType.VIDEO,
            "cost_per_sec": Decimal("0.2500"),
        },
        "wan-2.1": {
            "model": "wavespeedai/wan-2.1-t2v-480p",
            "type": MediaType.VIDEO,
            "cost_per_sec": Decimal("0.1000"),
        },
        "flux-schnell": {
            "model": "black-forest-labs/flux-schnell",
            "type": MediaType.IMAGE,
            "cost_per_image": Decimal("0.0030"),
        },
    }

    def __init__(self, api_token: Optional[str] = None):
        self.settings = get_settings()
        self.api_token = api_token or self.settings.replicate_api_token or ""

    @property
    def name(self) -> str:
        return self.NAME

    def is_configured(self) -> bool:
        return bool(self.api_token and not self.api_token.startswith("your_"))

    def resolve_model(self, model_key: str) -> Dict[str, Any]:
        if model_key in self.MODELS_CATALOG:
            return self.MODELS_CATALOG[model_key]
        return {
            "model": model_key,
            "type": MediaType.VIDEO if "video" in model_key else MediaType.IMAGE,
            "cost_per_sec": Decimal("0.2000"),
            "cost_per_image": Decimal("0.0100"),
        }

    def estimate_cost(self, params: GenerationParams) -> Decimal:
        info = self.resolve_model(params.model)
        if info["type"] == MediaType.VIDEO:
            rate = info.get("cost_per_sec", Decimal("0.2000"))
            return rate * Decimal(str(params.duration_sec))
        return info.get("cost_per_image", Decimal("0.0100"))

    async def generate_video(self, params: GenerationParams) -> JobResponse:
        """Submit and poll Replicate video prediction."""
        job_id = str(uuid4())
        model_info = self.resolve_model(params.model)
        cost = self.estimate_cost(params)

        job = JobResponse(
            job_id=job_id,
            provider=self.name,
            model=params.model,
            media_type=MediaType.VIDEO,
            status=JobStatus.PENDING,
            prompt=params.prompt,
            duration_sec=float(params.duration_sec),
            cost_usd=cost,
        )

        if not self.is_configured():
            job.status = JobStatus.FAILED
            job.error = "Replicate API token (REPLICATE_API_TOKEN) not configured."
            job.completed_at = datetime.now(timezone.utc)
            return job

        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

        input_payload: Dict[str, Any] = {
            "prompt": params.prompt,
            "duration": params.duration_sec,
            "aspect_ratio": params.aspect_ratio,
        }
        if params.negative_prompt:
            input_payload["negative_prompt"] = params.negative_prompt
        input_payload.update(params.extra_params)

        async with httpx.AsyncClient(timeout=180.0) as client:
            try:
                job.status = JobStatus.PROCESSING
                # Models format: https://api.replicate.com/v1/models/{owner}/{model}/predictions
                model_path = model_info["model"]
                submit_url = f"{self.BASE_URL}/models/{model_path}/predictions"
                resp = await client.post(submit_url, json={"input": input_payload}, headers=headers)

                if resp.status_code not in (200, 201):
                    job.status = JobStatus.FAILED
                    job.error = f"Replicate submission failed ({resp.status_code}): {resp.text[:200]}"
                    job.completed_at = datetime.now(timezone.utc)
                    return job

                pred_data = resp.json()
                pred_id = pred_data.get("id")
                poll_url = pred_data.get("urls", {}).get("get") or f"{self.BASE_URL}/predictions/{pred_id}"

                # Poll prediction
                for _ in range(90):
                    await asyncio.sleep(2.5)
                    st_resp = await client.get(poll_url, headers=headers)
                    if st_resp.status_code != 200:
                        continue
                    st_data = st_resp.json()
                    st_status = st_data.get("status")

                    if st_status == "succeeded":
                        output = st_data.get("output")
                        out_url = output if isinstance(output, str) else (output[0] if isinstance(output, list) else None)
                        if out_url:
                            job.output_url = out_url
                            job.status = JobStatus.COMPLETED
                            dest = self.settings.storage_dir / "raw" / f"{job_id}.mp4"
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dl = await client.get(out_url, timeout=120.0)
                            dest.write_bytes(dl.content)
                            job.local_path = str(dest)
                            job.completed_at = datetime.now(timezone.utc)
                            return job

                    elif st_status in ("failed", "canceled"):
                        job.status = JobStatus.FAILED
                        job.error = f"Replicate prediction failed: {st_data.get('error')}"
                        job.completed_at = datetime.now(timezone.utc)
                        return job

                job.status = JobStatus.FAILED
                job.error = "Timed out waiting for Replicate prediction."
                job.completed_at = datetime.now(timezone.utc)
                return job

            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = f"Exception during Replicate generation: {str(e)}"
                job.completed_at = datetime.now(timezone.utc)
                return job

    async def generate_image(self, params: GenerationParams) -> JobResponse:
        """Submit and poll Replicate image prediction."""
        job_id = str(uuid4())
        model_info = self.resolve_model(params.model)
        cost = self.estimate_cost(params)

        job = JobResponse(
            job_id=job_id,
            provider=self.name,
            model=params.model,
            media_type=MediaType.IMAGE,
            status=JobStatus.PENDING,
            prompt=params.prompt,
            cost_usd=cost,
        )

        if not self.is_configured():
            job.status = JobStatus.FAILED
            job.error = "Replicate API token not configured."
            job.completed_at = datetime.now(timezone.utc)
            return job

        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        input_payload = {
            "prompt": params.prompt,
            "width": params.width or 1024,
            "height": params.height or 1024,
        }

        async with httpx.AsyncClient(timeout=90.0) as client:
            try:
                job.status = JobStatus.PROCESSING
                model_path = model_info["model"]
                submit_url = f"{self.BASE_URL}/models/{model_path}/predictions"
                resp = await client.post(submit_url, json={"input": input_payload}, headers=headers)
                if resp.status_code not in (200, 201):
                    job.status = JobStatus.FAILED
                    job.error = f"Replicate image failed: {resp.text[:200]}"
                    job.completed_at = datetime.now(timezone.utc)
                    return job

                data = resp.json()
                poll_url = data.get("urls", {}).get("get") or f"{self.BASE_URL}/predictions/{data.get('id')}"

                for _ in range(40):
                    await asyncio.sleep(1.5)
                    st = await client.get(poll_url, headers=headers)
                    if st.status_code != 200:
                        continue
                    st_data = st.json()
                    if st_data.get("status") == "succeeded":
                        out = st_data.get("output")
                        out_url = out if isinstance(out, str) else (out[0] if isinstance(out, list) else None)
                        if out_url:
                            job.output_url = out_url
                            job.status = JobStatus.COMPLETED
                            dest = self.settings.storage_dir / "raw" / f"{job_id}.png"
                            dl = await client.get(out_url)
                            dest.write_bytes(dl.content)
                            job.local_path = str(dest)
                            job.completed_at = datetime.now(timezone.utc)
                            return job
                    elif st_data.get("status") in ("failed", "canceled"):
                        job.status = JobStatus.FAILED
                        job.error = str(st_data.get("error"))
                        job.completed_at = datetime.now(timezone.utc)
                        return job

                job.status = JobStatus.FAILED
                job.error = "Replicate image timeout"
                job.completed_at = datetime.now(timezone.utc)
                return job
            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = str(e)
                job.completed_at = datetime.now(timezone.utc)
                return job
