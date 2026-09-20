"""
Platform encoding and packaging pipeline for AI Cinema Studio Engine (Layer 5).
Generates compliant deliverable variants for YouTube, Shorts/Reels/TikTok, and LinkedIn.
"""

import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Union
from cinema_engine.config import get_settings
from cinema_engine.post.grading import find_ffmpeg


PLATFORM_SPECS: Dict[str, Dict[str, str]] = {
    "youtube_16_9": {
        "aspect": "16:9",
        "vf": "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "crf": "18",
        "bitrate": "8M",
        "ext": "_16x9_yt.mp4",
    },
    "shorts_reels_9_16": {
        "aspect": "9:16",
        "vf": "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "crf": "20",
        "bitrate": "6M",
        "ext": "_9x16_vertical.mp4",
    },
    "square_1_1": {
        "aspect": "1:1",
        "vf": "scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080",
        "crf": "20",
        "bitrate": "5M",
        "ext": "_1x1_square.mp4",
    },
}


async def encode_platform_variant(
    input_video: Union[str, Path],
    platform_key: str,
    output_path: Optional[Union[str, Path]] = None,
) -> Path:
    """Encode a video master into a platform-specific aspect ratio and bitrate variant."""
    inp = Path(input_video).resolve()
    if not inp.exists():
        raise FileNotFoundError(f"Input video does not exist: {inp}")

    spec = PLATFORM_SPECS.get(platform_key)
    if not spec:
        raise ValueError(f"Unknown platform spec '{platform_key}'. Supported: {list(PLATFORM_SPECS.keys())}")

    settings = get_settings()
    dest_dir = settings.storage_dir / "finals"
    dest_dir.mkdir(parents=True, exist_ok=True)

    out = Path(output_path).resolve() if output_path else dest_dir / f"{inp.stem}{spec['ext']}"
    ffmpeg_exe = find_ffmpeg()

    cmd = [
        ffmpeg_exe, "-y",
        "-i", str(inp),
        "-vf", spec["vf"],
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", spec["crf"],
        "-b:v", spec["bitrate"],
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[-500:]
        raise RuntimeError(f"FFmpeg platform encode for '{platform_key}' failed:\n{err}")

    return out


async def encode_all_platforms(
    input_video: Union[str, Path],
    platforms: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Batch encode video for all target platforms."""
    target_platforms = platforms or list(PLATFORM_SPECS.keys())
    results: Dict[str, str] = {}

    for plat in target_platforms:
        out_file = await encode_platform_variant(input_video, plat)
        results[plat] = str(out_file)

    return results
