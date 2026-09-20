"""
Cinematography RAG query service.
Queries camera, lighting, and effects presets and assembles studio-grade cinematography prompts.
"""

from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field

from cinema_engine.config import get_settings
from cinema_engine.rag.presets import search_local_presets


class CinematographyDirective(BaseModel):
    """Structured technical cinematography direction for a shot."""
    assembled_fragment: str
    full_prompt: str
    camera: Optional[Dict[str, Any]] = None
    lighting: Optional[Dict[str, Any]] = None
    effects: Optional[Dict[str, Any]] = None


class CinematographyRAGService:
    """Service for querying cinematography presets and assembling directives."""

    def __init__(self):
        self.settings = get_settings()

    async def search_collection(
        self,
        collection_name: str,
        query: str,
        limit: int = 1,
    ) -> List[Dict[str, Any]]:
        """Query Qdrant if reachable, otherwise use high-speed local lexical fallback."""
        # Check if Qdrant is reachable and OpenAI key is set
        if self.settings.openai_api_key and self.settings.qdrant_url:
            try:
                # 1. Get embedding via OpenAI API
                async with httpx.AsyncClient(timeout=10.0) as client:
                    emb_resp = await client.post(
                        "https://api.openai.com/v1/embeddings",
                        headers={"Authorization": f"Bearer {self.settings.openai_api_key}"},
                        json={"input": query, "model": "text-embedding-3-small"},
                    )
                    if emb_resp.status_code == 200:
                        vector = emb_resp.json()["data"][0]["embedding"]
                        # 2. Search Qdrant
                        qdrant_headers = {}
                        if self.settings.qdrant_api_key:
                            qdrant_headers["api-key"] = self.settings.qdrant_api_key
                        q_resp = await client.post(
                            f"{self.settings.qdrant_url}/collections/{collection_name}/points/search",
                            headers=qdrant_headers,
                            json={"vector": vector, "limit": limit, "with_payload": True},
                        )
                        if q_resp.status_code == 200:
                            points = q_resp.json().get("result", [])
                            if points:
                                return [p["payload"] for p in points]
            except Exception:
                pass  # Gracefully fall back to local search

        # Map collection name to local preset category
        cat_map = {
            "camera_presets": "camera",
            "lighting_presets": "lighting",
            "effects_presets": "effects",
        }
        cat = cat_map.get(collection_name, collection_name)
        return search_local_presets(cat, query, limit=limit)

    async def assemble_cinematography(
        self,
        shot_description: str,
        camera_query: Optional[str] = None,
        lighting_query: Optional[str] = None,
        effects_query: Optional[str] = None,
    ) -> CinematographyDirective:
        """
        Assemble camera, lighting, and effects presets into a single cohesive cinematography directive.
        """
        cq = camera_query or shot_description
        lq = lighting_query or shot_description
        eq = effects_query or shot_description

        # Query all three preset domains concurrently
        cam_res = await self.search_collection("camera_presets", cq, limit=1)
        light_res = await self.search_collection("lighting_presets", lq, limit=1)
        fx_res = await self.search_collection("effects_presets", eq, limit=1)

        cam_item = cam_res[0] if cam_res else {}
        light_item = light_res[0] if light_res else {}
        fx_item = fx_res[0] if fx_res else {}

        fragments = []
        if cam_item.get("prompt_fragment"):
            fragments.append(cam_item["prompt_fragment"])
        if light_item.get("prompt_fragment"):
            fragments.append(light_item["prompt_fragment"])
        if fx_item.get("prompt_fragment"):
            fragments.append(fx_item["prompt_fragment"])

        assembled_frag = ", ".join(fragments)
        if shot_description.strip():
            full_prompt = f"{shot_description.strip().rstrip('.')}. {assembled_frag}"
        else:
            full_prompt = assembled_frag

        return CinematographyDirective(
            assembled_fragment=assembled_frag,
            full_prompt=full_prompt,
            camera=cam_item,
            lighting=light_item,
            effects=fx_item,
        )


_rag_service: Optional[CinematographyRAGService] = None


def get_rag_service() -> CinematographyRAGService:
    global _rag_service
    if _rag_service is None:
        _rag_service = CinematographyRAGService()
    return _rag_service
