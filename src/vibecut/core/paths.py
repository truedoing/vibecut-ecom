"""跨平台路径与原子写。Windows 是首要交付目标，这里先处理它的坑。"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, fallback: str = "file") -> str:
    """保留中文，清掉各平台非法字符，避开 Windows 保留名。"""
    name = unicodedata.normalize("NFC", (name or "").strip())
    name = _ILLEGAL.sub("_", name).rstrip(" .")
    if not name:
        name = fallback
    stem, dot, suffix = name.partition(".")
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    return f"{stem}{dot}{suffix}"


def long_path(path: Path | str) -> str:
    """Windows 下超长路径加 \\\\?\\ 前缀；其它平台原样返回。"""
    p = os.path.abspath(os.fspath(path))
    if sys.platform == "win32" and len(p) >= 240 and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + p
    return p


def ensure_dir(path: Path | str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def atomic_write_bytes(path: Path | str, data: bytes) -> None:
    """同目录临时文件 + fsync + os.replace。Windows 上读句柄占用时重试。"""
    target = Path(path)
    ensure_dir(target.parent)
    tmp = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    with open(long_path(tmp), "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(6):
        try:
            os.replace(long_path(tmp), long_path(target))
            return
        except PermissionError:
            if attempt == 5:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.15 * (2 ** attempt))


def atomic_write_json(path: Path | str, obj) -> None:
    atomic_write_bytes(path, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))


def atomic_write_text(path: Path | str, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))
