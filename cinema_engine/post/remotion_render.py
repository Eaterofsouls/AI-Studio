"""
Remotion video compositing integration for AI Cinema Studio Engine (Layer 4).
Renders branded motion graphics, lower thirds, end cards, subtitles, and clips.
Falls back to FFmpeg concat/mix pipeline if Node.js/Remotion is not installed.
"""

import asyncio
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from cinema_engine.config import get_settings
from cinema_engine.post.grading import find_ffmpeg


class RemotionProps(BaseModel):
    """Properties passed to Remotion compositions."""
    composition_id: str = "Generic"  # Generic, Generic-Vertical, PopTech, PopTech-Vertical
    clips: List[Dict[str, Any]] = Field(default_factory=list)  # [{"videoUrl": "...", "duration": 5}]
    audio_url: Optional[str] = None
    title: str = "AI Cinema Production"
    description: Optional[str] = None
    branding: Dict[str, str] = Field(default_factory=lambda: {"primaryColor": "#2792dc", "secondaryColor": "#0a0a0a"})


def get_remotion_dir() -> Path:
    settings = get_settings()
    rem_dir = settings.base_dir / "remotion"
    if not rem_dir.exists():
        rem_dir = Path("./remotion")
    return rem_dir.resolve()


def is_node_available() -> bool:
    """Check if Node.js and npx are available on system."""
    return bool(shutil.which("npx") and shutil.which("node"))


async def render_remotion_composition(
    props: RemotionProps,
    output_path: Optional[Union[str, Path]] = None,
) -> Path:
    """
    Render a Remotion composition to MP4 using npx remotion render.
    Falls back to FFmpeg compositing if Node/Remotion environment is not present.
    """
    settings = get_settings()
    dest_dir = settings.storage_dir / "composited"
    dest_dir.mkdir(parents=True, exist_ok=True)

    if output_path is None:
        out = dest_dir / f"composed_{props.composition_id}_{int(asyncio.get_event_loop().time())}.mp4"
    else:
        out = Path(output_path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

    remotion_dir = get_remotion_dir()

    # If Node.js and remotion directory exist, attempt Remotion CLI render
    if is_node_available() and (remotion_dir / "package.json").exists():
        props_json = props.model_dump_json()
        cmd = [
            "npx", "remotion", "render",
            "src/index.ts",
            props.composition_id,
            str(out),
            f"--props={props_json}",
            "--overwrite",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(remotion_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
                return out
        except Exception:
            pass  # Fall through to FFmpeg compositing fallback

    # Fallback: FFmpeg video assembly
    return await _ffmpeg_fallback_render(props, out)


async def _ffmpeg_fallback_render(props: RemotionProps, out_path: Path) -> Path:
    """
    Resilient FFmpeg-based fallback that stitches clips and merges the master audio track.
    """
    ffmpeg_exe = find_ffmpeg()
    clip_paths = []

    for c in props.clips:
        p = c.get("videoUrl") or c.get("local_path")
        if p and Path(p).exists():
            clip_paths.append(str(Path(p).resolve()))

    if not clip_paths:
        # Generate synthetic branded sequence
        cmd = [
            ffmpeg_exe, "-y",
            "-f", "lavfi", "-i", "color=c=black:s=1920x1080:d=5",
            "-vf", f"drawtext=text='{props.title}':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=(h-text_h)/2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out_path),
        ]
    elif len(clip_paths) == 1 and not props.audio_url:
        shutil.copyfile(clip_paths[0], out_path)
        return out_path
    else:
        # Build concat list file
        concat_file = out_path.parent / f"concat_{out_path.stem}.txt"
        with open(concat_file, "w", encoding="utf-8") as f:
            for cp in clip_paths:
                f.write(f"file '{cp.replace('\\', '/')}'\n")

        cmd = [
            ffmpeg_exe, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
        ]

        if props.audio_url and Path(props.audio_url).exists():
            cmd.extend(["-i", str(Path(props.audio_url).resolve()), "-c:a", "aac", "-shortest"])
        else:
            cmd.extend(["-c:a", "copy"])

        cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path)])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    raise RuntimeError("FFmpeg compositing fallback failed to produce an output file.")
