"""
Cinematography presets definitions, loaders, and indexing.
Consolidates camera, lighting, and effects presets into a unified repository.
"""

from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from cinema_engine.config import get_settings


class CameraPreset(BaseModel):
    id: str
    camera_body: str
    camera_body_traits: Optional[str] = None
    lens_type: str
    lens_traits: Optional[str] = None
    focal_length_mm: int
    aperture: str
    movement: str
    prompt_fragment: str
    use_cases: List[str] = Field(default_factory=list)


class LightingPreset(BaseModel):
    id: str
    setup_type: str
    description: Optional[str] = None
    prompt_fragment: str
    color_temperature_k: Optional[int] = None
    mood_tags: List[str] = Field(default_factory=list)
    use_cases: List[str] = Field(default_factory=list)


class EffectsPreset(BaseModel):
    id: str
    name: str
    effect_type: str
    description: Optional[str] = None
    prompt_fragment: str
    use_cases: List[str] = Field(default_factory=list)


def get_tools_dir() -> Path:
    settings = get_settings()
    tools_dir = settings.base_dir / "tools"
    if not tools_dir.exists():
        tools_dir = Path("./tools")
    return tools_dir.resolve()


@lru_cache(maxsize=1)
def load_all_presets() -> Dict[str, List[Any]]:
    """Load all presets from JSON storage with caching."""
    tools_dir = get_tools_dir()
    data: Dict[str, List[Any]] = {
        "camera": [],
        "lighting": [],
        "effects": [],
    }

    cam_path = tools_dir / "camera_presets.json"
    if cam_path.exists():
        try:
            with open(cam_path, "r", encoding="utf-8") as f:
                items = json.load(f)
                data["camera"] = [CameraPreset(**item) for item in items]
        except Exception:
            pass

    light_path = tools_dir / "lighting_presets.json"
    if light_path.exists():
        try:
            with open(light_path, "r", encoding="utf-8") as f:
                items = json.load(f)
                data["lighting"] = [LightingPreset(**item) for item in items]
        except Exception:
            pass

    fx_path = tools_dir / "effects_presets.json"
    if fx_path.exists():
        try:
            with open(fx_path, "r", encoding="utf-8") as f:
                items = json.load(f)
                data["effects"] = [EffectsPreset(**item) for item in items]
        except Exception:
            pass

    return data


def _tokenize(text: str) -> set[str]:
    """Tokenize and normalize text for fast lexical scoring."""
    tokens = re.findall(r"\w+", text.lower())
    stop_words = {"a", "an", "the", "in", "on", "at", "with", "and", "or", "for", "to", "of"}
    return {t for t in tokens if t not in stop_words and len(t) > 2}


def score_preset(query_tokens: set[str], preset_text: str, tags: List[str]) -> float:
    """Calculate match score between query tokens and preset metadata."""
    if not query_tokens:
        return 0.0
    text_tokens = _tokenize(preset_text)
    tag_tokens = set()
    for tag in tags:
        tag_tokens.update(_tokenize(tag))

    overlap_text = len(query_tokens.intersection(text_tokens))
    overlap_tags = len(query_tokens.intersection(tag_tokens))

    # Tags have higher weight
    return (overlap_text * 1.0) + (overlap_tags * 2.5)


def search_local_presets(
    category: str,
    query: str,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """
    Search presets locally with sub-millisecond ranking.
    Acts as resilient fallback when vector DB is unreachable or during offline operations.
    """
    all_data = load_all_presets()
    presets = all_data.get(category, [])
    if not presets:
        return []

    q_tokens = _tokenize(query)
    scored_items = []

    for p in presets:
        if category == "camera":
            assert isinstance(p, CameraPreset)
            searchable = f"{p.camera_body} {p.lens_type} {p.aperture} {p.movement} {p.prompt_fragment}"
            tags = p.use_cases
            score = score_preset(q_tokens, searchable, tags)
            scored_items.append((score, p.model_dump()))

        elif category == "lighting":
            assert isinstance(p, LightingPreset)
            searchable = f"{p.setup_type} {p.description or ''} {p.prompt_fragment}"
            tags = p.mood_tags + p.use_cases
            score = score_preset(q_tokens, searchable, tags)
            scored_items.append((score, p.model_dump()))

        elif category == "effects":
            assert isinstance(p, EffectsPreset)
            searchable = f"{p.name} {p.effect_type} {p.description or ''} {p.prompt_fragment}"
            tags = p.use_cases
            score = score_preset(q_tokens, searchable, tags)
            scored_items.append((score, p.model_dump()))

    scored_items.sort(key=lambda x: x[0], reverse=True)
    # If top scores are 0, return default curated choices
    results = [item for score, item in scored_items if score > 0][:limit]
    if not results and scored_items:
        results = [scored_items[0][1]]

    return results
