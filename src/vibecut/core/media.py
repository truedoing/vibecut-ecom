"""ffmpeg / ffprobe 定位与媒体元数据。

所有路径都通过 long_path() 交给子进程，避免 Windows 中文/长路径问题。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..config import REPO_DIR, load_settings
from .paths import ensure_dir, long_path

_FFMPEG_CACHE: str | None = None
_FFPROBE_CACHE: str | None = None

_EXE = ".exe" if os.name == "nt" else ""

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac"}


def _find_tool(name: str) -> str:
    settings = load_settings()
    candidates: list[Path] = []

    configured = settings.ffmpeg or os.environ.get("VIBECUT_FFMPEG", "")
    if configured:
        p = Path(configured).expanduser()
        candidates.append(p / f"{name}{_EXE}" if p.is_dir() else p)

    candidates += [
        REPO_DIR / "tools" / "ffmpeg" / "bin" / f"{name}{_EXE}",
        Path("/opt/anaconda3/bin") / f"{name}{_EXE}",
        Path("/usr/local/bin") / f"{name}{_EXE}",
        Path("/usr/bin") / f"{name}{_EXE}",
    ]

    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return str(c)

    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(
        f"找不到 {name}。请安装 ffmpeg，或设置 VIBECUT_FFMPEG 指向 ffmpeg 可执行文件。"
    )


def ffmpeg_bin() -> str:
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE is None:
        _FFMPEG_CACHE = _find_tool("ffmpeg")
    return _FFMPEG_CACHE


def ffprobe_bin() -> str:
    global _FFPROBE_CACHE
    if _FFPROBE_CACHE is None:
        _FFPROBE_CACHE = _find_tool("ffprobe")
    return _FFPROBE_CACHE


def kind_of(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    if ext in AUDIO_EXT:
        return "audio"
    return "file"


def probe_media(path: str | Path) -> dict:
    """返回 duration_sec / width / height / codec / fps；失败返回空字段。"""
    src = Path(path)
    out = {"duration_sec": None, "width": None, "height": None,
           "codec": "", "fps": None}
    if not src.exists():
        return out
    if kind_of(src) == "image":
        try:
            from PIL import Image
            with Image.open(src) as im:
                out["width"], out["height"] = im.size
        except Exception:
            pass
        return out

    cmd = [
        ffprobe_bin(), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", long_path(src),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            return out
        data = json.loads(res.stdout or "{}")
    except Exception:
        return out

    duration = (data.get("format") or {}).get("duration")
    if duration:
        try:
            out["duration_sec"] = round(float(duration), 3)
        except ValueError:
            pass

    for st in data.get("streams") or []:
        if st.get("codec_type") == "video":
            out["width"] = st.get("width")
            out["height"] = st.get("height")
            out["codec"] = st.get("codec_name", "")
            rate = st.get("avg_frame_rate") or ""
            if "/" in rate:
                num, den = rate.split("/", 1)
                try:
                    out["fps"] = round(float(num) / float(den), 3) if float(den) else None
                except ValueError:
                    pass
            break
    return out


def run_ffmpeg(args: list[str], timeout: float = 3600,
               check: bool = True) -> subprocess.CompletedProcess:
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", *map(str, args)]
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, check=check)


def make_thumb(path: str | Path, out: str | Path,
               at_sec: float = 0.5, width: int = 480) -> Path | None:
    """抽一帧做缩略图。图片直接缩放；视频用 ffmpeg。"""
    src, dst = Path(path), Path(out)
    ensure_dir(dst.parent)
    tmp = dst.with_name(f".{dst.stem}.tmp{dst.suffix}")
    try:
        if kind_of(src) == "image":
            from PIL import Image
            with Image.open(src) as im:
                im.thumbnail((width, width * 4))
                im.convert("RGB").save(tmp, quality=85)
        else:
            res = run_ffmpeg([
                "-ss", f"{max(0.0, at_sec):.3f}", "-i", long_path(src),
                "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3",
                long_path(tmp),
            ], timeout=60, check=False)
            if res.returncode != 0 or not tmp.exists():
                tmp.unlink(missing_ok=True)
                return None
        os.replace(long_path(tmp), long_path(dst))
        return dst
    except Exception:
        tmp.unlink(missing_ok=True)
        return None
