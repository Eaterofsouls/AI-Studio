"""
Color Grading Engine for AI Cinema Studio Engine.
Applies 3D LUT chains, film grain, vignette, and exposure adjustments via FFmpeg.
Refactored from tools/color_grade.py into a fully callable async and sync Python module.
"""

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Dict, List, Optional, Union

from cinema_engine.config import get_settings
from cinema_engine.models import ColorGradeParams


def find_ffmpeg() -> str:
    """Locate full FFmpeg build with lut3d filter support."""
    settings = get_settings()
    if settings.ffmpeg_path and Path(settings.ffmpeg_path).exists():
        return settings.ffmpeg_path

    # Check WinGet standard location
    winget_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"
    search_paths = [str(winget_dir)] + os.environ.get("PATH", "").split(os.pathsep)

    for p in search_paths:
        if not p:
            continue
        candidate = Path(p) / "ffmpeg.exe"
        if candidate.exists():
            try:
                res = subprocess.run([str(candidate), "-filters"], capture_output=True, text=True, timeout=5)
                if "lut3d" in res.stdout:
                    return str(candidate)
            except Exception:
                pass

    which_ffmpeg = shutil.which("ffmpeg")
    if which_ffmpeg:
        return which_ffmpeg

    raise FileNotFoundError("FFmpeg executable with lut3d filter not found on system.")


def get_luts_dir() -> Path:
    """Resolve the directory containing .cube LUT files."""
    settings = get_settings()
    luts_dir = settings.base_dir / "luts"
    if not luts_dir.exists():
        # Fallback relative to current working directory
        luts_dir = Path("./luts")
    return luts_dir.resolve()


def list_available_luts() -> Dict[str, List[str]]:
    """List all available LUTs organized by category."""
    luts_dir = get_luts_dir()
    index_path = luts_dir / "index.json"
    result: Dict[str, List[str]] = {"base": [], "film_stocks": [], "creative": [], "corrections": []}

    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for cat in result.keys():
            if cat in data:
                result[cat] = list(data[cat].keys())
        return result

    # Fallback to directory scan
    for cube in luts_dir.rglob("*.cube"):
        cat = cube.parent.name
        if cat not in result:
            result[cat] = []
        result[cat].append(cube.stem)
    return result


def resolve_lut_path(lut_name: str) -> Path:
    """Resolve a LUT key (e.g. 'kodak_vision3_500t') to its absolute .cube file path."""
    luts_dir = get_luts_dir()

    # Direct filename or path
    if lut_name.endswith(".cube"):
        path = luts_dir / lut_name
        if path.exists():
            return path

    index_path = luts_dir / "index.json"
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            index_data = json.load(f)
        for cat in ["base", "film_stocks", "creative", "corrections"]:
            if cat in index_data and lut_name in index_data[cat]:
                rel_file = index_data[cat][lut_name]["file"]
                target = luts_dir / rel_file
                if target.exists():
                    return target

    # Fuzzy match by stem
    for cube in luts_dir.rglob("*.cube"):
        if lut_name.lower() in cube.stem.lower():
            return cube

    raise FileNotFoundError(f"LUT '{lut_name}' not found in {luts_dir}")


def build_filter_chain(params: ColorGradeParams) -> str:
    """Construct FFmpeg video filter chain string from parameters."""
    filters: List[str] = []

    # 1. Deband filter (smooths AI video compression/quantization artifacts)
    if params.deband:
        filters.append("deband=1thr=0.02:2thr=0.02:3thr=0.02:blur=1")

    # 2. 3D LUT Chain
    for lut_name in params.lut_chain:
        lut_path = resolve_lut_path(lut_name)
        # Format path for FFmpeg filter on Windows (forward slashes and escaped colons)
        lut_str = str(lut_path).replace("\\", "/")
        # Escape colons for ffmpeg filter arguments (e.g. C\:/path)
        lut_str = lut_str.replace(":", "\\:")
        filters.append(f"lut3d=file='{lut_str}'")

    # 3. Exposure / Contrast / Saturation adjustment
    eq_parts: List[str] = []
    if params.exposure != 0.0:
        eq_parts.append(f"brightness={params.exposure}")
    if params.contrast != 0.0:
        eq_parts.append(f"contrast={1.0 + params.contrast}")
    if params.saturation != 0.0:
        eq_parts.append(f"saturation={1.0 + params.saturation}")
    if eq_parts:
        filters.append(f"eq={':'.join(eq_parts)}")

    # 4. Film Grain Simulation
    if params.grain > 0.0:
        intensity = int(params.grain * 30)
        filters.append(f"noise=alls={intensity}:allf=t")

    # 5. Vignette
    if params.vignette > 0.0:
        angle = 0.3 + (params.vignette * 0.5)
        filters.append(f"vignette=angle={angle:.2f}")

    return ",".join(filters) if filters else "null"


async def apply_color_grade(
    input_path: Union[str, Path],
    output_path: Optional[Union[str, Path]] = None,
    params: Optional[ColorGradeParams] = None,
    codec: str = "libx264",
    crf: int = 18,
) -> Path:
    """
    Asynchronously apply 3D LUT grading and film finish to a video using FFmpeg.
    """
    inp = Path(input_path).resolve()
    if not inp.exists():
        raise FileNotFoundError(f"Input video does not exist: {inp}")

    if output_path is None:
        settings = get_settings()
        dest_dir = settings.storage_dir / "graded"
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / f"{inp.stem}_graded.mp4"
    else:
        out = Path(output_path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

    grade_params = params or ColorGradeParams()
    filter_chain = build_filter_chain(grade_params)
    ffmpeg_exe = find_ffmpeg()

    cmd = [
        ffmpeg_exe,
        "-y",
        "-i", str(inp),
        "-vf", filter_chain,
        "-c:v", codec,
        "-crf", str(crf),
        "-c:a", "copy",
        str(out),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace")[-600:]
        raise RuntimeError(f"FFmpeg grading failed with code {proc.returncode}:\n{err_msg}")

    return out


def apply_color_grade_sync(
    input_path: Union[str, Path],
    output_path: Optional[Union[str, Path]] = None,
    params: Optional[ColorGradeParams] = None,
) -> Path:
    """Synchronous wrapper for color grading."""
    return asyncio.run(apply_color_grade(input_path, output_path, params))
