"""
fal.ai provider adapter for AI Cinema Studio Engine.
Supports async video and image generation across Wan 2.6, Seedance, Kling, and FLUX.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
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


class FalProvider(VideoProvider, ImageProvider):
    """Async provider adapter for fal.ai API gateway."""

    NAME = "fal"
    BASE_QUEUE_URL = "https://queue.fal.run"

    # Known models catalog and pricing
    MODELS_CATALOG: Dict[str, Dict[str, Any]] = {
        "wan-2.6": {
            "endpoint": "fal-ai/wan/v2.6/text-to-video",
            "type": MediaType.VIDEO,
            "cost_per_sec": Decimal("0.1000"),
            "default_duration": 5,
        },
        "seedance": {
            "endpoint": "fal-ai/seedance-2/text-to-video",
            "type": MediaType.VIDEO,
            "cost_per_sec": Decimal("0.5000"),
            "default_duration": 5,
        },
        "kling": {
            "endpoint": "fal-ai/kling-video/v2/master/text-to-video",
            "type": MediaType.VIDEO,
            "cost_per_sec": Decimal("0.4000"),
            "default_duration": 5,
        },
        "flux-schnell": {
            "endpoint": "fal-ai/flux/schnell",
            "type": MediaType.IMAGE,
            "cost_per_image": Decimal("0.0100"),
        },
        "flux-pro": {
            "endpoint": "fal-ai/flux-pro/v1.1",
            "type": MediaType.IMAGE,
            "cost_per_image": Decimal("0.0500"),
        },
        "nano-banana": {
            "endpoint": "fal-ai/nano-banana-2",
            "type": MediaType.IMAGE,
            "cost_per_image": Decimal("0.0100"),
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        self.settings = get_settings()
        self.api_key = api_key or self.settings.fal_key or ""

    @property
    def name(self) -> str:
        return self.NAME

    def is_configured(self) -> bool:
        return bool(self.api_key and not self.api_key.startswith("your_"))

    def resolve_model(self, model_key: str) -> Dict[str, Any]:
        """Resolve friendly name or raw endpoint."""
        if model_key in self.MODELS_CATALOG:
            return self.MODELS_CATALOG[model_key]
        # Allow passing full endpoint directly
        return {
            "endpoint": model_key,
            "type": MediaType.VIDEO if "video" in model_key else MediaType.IMAGE,
            "cost_per_sec": Decimal("0.1000"),
            "cost_per_image": Decimal("0.0500"),
        }

    def estimate_cost(self, params: GenerationParams) -> Decimal:
        info = self.resolve_model(params.model)
        if info["type"] == MediaType.VIDEO:
            rate = info.get("cost_per_sec", Decimal("0.1000"))
            return rate * Decimal(str(params.duration_sec))
        return info.get("cost_per_image", Decimal("0.0500"))

    async def generate_video(self, params: GenerationParams) -> JobResponse:
        """Submit and poll fal.ai video generation."""
        job_id = str(uuid4())
        model_info = self.resolve_model(params.model)
        endpoint = model_info["endpoint"]
        estimated_cost = self.estimate_cost(params)

        job = JobResponse(
            job_id=job_id,
            provider=self.name,
            model=params.model,
            media_type=MediaType.VIDEO,
            status=JobStatus.PENDING,
            prompt=params.prompt,
            duration_sec=float(params.duration_sec),
            cost_usd=estimated_cost,
        )

        if not self.is_configured():
            job.status = JobStatus.FAILED
            job.error = "fal.ai API key (FAL_KEY) not configured."
            job.completed_at = datetime.now(timezone.utc)
            return job

        # Construct request payload
        payload: Dict[str, Any] = {
            "prompt": params.prompt,
            "duration": str(params.duration_sec),
            "aspect_ratio": params.aspect_ratio,
        }
        if params.seed is not None:
            payload["seed"] = params.seed
        if params.ref_image_url:
            payload["image_url"] = params.ref_image_url
        if params.negative_prompt:
            payload["negative_prompt"] = params.negative_prompt
        payload.update(params.extra_params)

        headers = {
            "Authorization": f"Key {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=180.0) as client:
            try:
                job.status = JobStatus.PROCESSING
                # Submit to fal queue
                submit_url = f"{self.BASE_QUEUE_URL}/{endpoint}"
                response = await client.post(submit_url, json=payload, headers=headers)

                if response.status_code != 200:
                    job.status = JobStatus.FAILED
                    job.error = f"fal.ai submission error ({response.status_code}): {response.text[:200]}"
                    job.completed_at = datetime.now(timezone.utc)
                    return job

                submit_data = response.json()
                status_url = submit_data.get("status_url")
                response_url = submit_data.get("response_url")

                if not status_url or not response_url:
                    # Synchronous return fallback
                    output_url = self._extract_video_url(submit_data)
                    if output_url:
                        job.output_url = output_url
                        job.status = JobStatus.COMPLETED
                        job.local_path = await self._download_file(client, output_url, f"{job_id}.mp4")
                        job.completed_at = datetime.now(timezone.utc)
                        return job
                    job.status = JobStatus.FAILED
                    job.error = f"Malformed fal response: {submit_data}"
                    job.completed_at = datetime.now(timezone.utc)
                    return job

                # Poll status
                max_retries = 90  # 90 * 2s = 180s timeout
                for _ in range(max_retries):
                    await asyncio.sleep(2.0)
                    status_resp = await client.get(status_url, headers=headers)
                    if status_resp.status_code != 200:
                        continue

                    status_json = status_resp.json()
                    status_str = status_json.get("status")

                    if status_str == "COMPLETED":
                        res_resp = await client.get(response_url, headers=headers)
                        res_json = res_resp.json()
                        output_url = self._extract_video_url(res_json)
                        if output_url:
                            job.output_url = output_url
                            job.status = JobStatus.COMPLETED
                            job.local_path = await self._download_file(client, output_url, f"{job_id}.mp4")
                            job.completed_at = datetime.now(timezone.utc)
                            return job
                        else:
                            job.status = JobStatus.FAILED
                            job.error = f"Missing video url in result: {res_json}"
                            job.completed_at = datetime.now(timezone.utc)
                            return job

                    elif status_str in ("FAILED", "ERROR"):
                        job.status = JobStatus.FAILED
                        job.error = f"Generation failed: {status_json.get('error', 'Unknown error')}"
                        job.completed_at = datetime.now(timezone.utc)
                        return job

                job.status = JobStatus.FAILED
                job.error = "Generation timed out waiting for fal.ai queue."
                job.completed_at = datetime.now(timezone.utc)
                return job

            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = f"Exception during fal generation: {str(e)}"
                job.completed_at = datetime.now(timezone.utc)
                return job

    async def generate_image(self, params: GenerationParams) -> JobResponse:
        """Submit and poll fal.ai image generation."""
        job_id = str(uuid4())
        model_info = self.resolve_model(params.model)
        endpoint = model_info["endpoint"]
        cost = self.estimate_cost(params)

        job = JobResponse(
            job_id=job_id,
            provider=self.name,
            model=params.model,
            media_type=MediaType.IMAGE,
            status=JobStatus.PENDING,
            prompt=params.prompt,
            duration_sec=0.0,
            cost_usd=cost,
        )

        if not self.is_configured():
            job.status = JobStatus.FAILED
            job.error = "fal.ai API key (FAL_KEY) not configured."
            job.completed_at = datetime.now(timezone.utc)
            return job

        payload: Dict[str, Any] = {
            "prompt": params.prompt,
            "image_size": {
                "width": params.width or 1024,
                "height": params.height or 1024,
            },
            "num_images": 1,
        }
        if params.seed is not None:
            payload["seed"] = params.seed

        headers = {
            "Authorization": f"Key {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                job.status = JobStatus.PROCESSING
                submit_url = f"{self.BASE_QUEUE_URL}/{endpoint}"
                response = await client.post(submit_url, json=payload, headers=headers)
                if response.status_code != 200:
                    job.status = JobStatus.FAILED
                    job.error = f"fal image error ({response.status_code}): {response.text[:200]}"
                    job.completed_at = datetime.now(timezone.utc)
                    return job

                data = response.json()
                # Check direct or queued
                output_url = self._extract_image_url(data)
                if output_url:
                    job.output_url = output_url
                    job.status = JobStatus.COMPLETED
                    job.local_path = await self._download_file(client, output_url, f"{job_id}.png")
                    job.completed_at = datetime.now(timezone.utc)
                    return job

                status_url = data.get("status_url")
                response_url = data.get("response_url")
                if not status_url or not response_url:
                    job.status = JobStatus.FAILED
                    job.error = "No status or image url returned"
                    job.completed_at = datetime.now(timezone.utc)
                    return job

                for _ in range(60):
                    await asyncio.sleep(1.5)
                    st = await client.get(status_url, headers=headers)
                    if st.status_code != 200:
                        continue
                    st_json = st.json()
                    if st_json.get("status") == "COMPLETED":
                        res = await client.get(response_url, headers=headers)
                        out_url = self._extract_image_url(res.json())
                        if out_url:
                            job.output_url = out_url
                            job.status = JobStatus.COMPLETED
                            job.local_path = await self._download_file(client, out_url, f"{job_id}.png")
                            job.completed_at = datetime.now(timezone.utc)
                            return job
                    elif st_json.get("status") in ("FAILED", "ERROR"):
                        job.status = JobStatus.FAILED
                        job.error = f"Image gen failed: {st_json.get('error')}"
                        job.completed_at = datetime.now(timezone.utc)
                        return job

                job.status = JobStatus.FAILED
                job.error = "Timed out waiting for image"
                job.completed_at = datetime.now(timezone.utc)
                return job

            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = str(e)
                job.completed_at = datetime.now(timezone.utc)
                return job

    def _extract_video_url(self, data: dict) -> Optional[str]:
        if "video" in data and isinstance(data["video"], dict):
            return data["video"].get("url")
        if "output" in data and isinstance(data["output"], dict):
            return data["output"].get("video", {}).get("url")
        return None

    def _extract_image_url(self, data: dict) -> Optional[str]:
        if "images" in data and isinstance(data["images"], list) and data["images"]:
            return data["images"][0].get("url")
        if "output" in data and "images" in data["output"] and data["output"]["images"]:
            return data["output"]["images"][0].get("url")
        return None

    async def _download_file(self, client: httpx.AsyncClient, url: str, filename: str) -> str:
        dest_dir = self.settings.storage_dir / "raw"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / filename
        resp = await client.get(url, timeout=120.0)
        resp.raise_for_status()
        dest_path.write_bytes(resp.content)
        return str(dest_path)
