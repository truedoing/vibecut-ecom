"""静态文件与 Range 响应。"""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


def media_type_for(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def range_response(path: Path, request: Request) -> Response:
    """浏览器时间轴/预览需要 Range。只读最终文件，写入方用 .part + replace。"""
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "文件不存在")
    file_size = path.stat().st_size
    media_type = media_type_for(path)

    header = request.headers.get("range", "")
    match = _RANGE.match(header)
    if not match:
        return StreamingResponse(
            _iter_file(path, 0, file_size - 1),
            media_type=media_type,
            headers={"Content-Length": str(file_size),
                     "Accept-Ranges": "bytes"},
        )

    start_s, end_s = match.groups()
    if start_s:
        start = int(start_s)
        end = int(end_s) if end_s else file_size - 1
    else:
        length = int(end_s or 0)
        start = max(0, file_size - length)
        end = file_size - 1
    start = max(0, start)
    end = min(end, file_size - 1)
    if start > end:
        raise HTTPException(416, "Range 超出文件大小")

    return StreamingResponse(
        _iter_file(path, start, end),
        status_code=206,
        media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(end - start + 1),
            "Accept-Ranges": "bytes",
        },
    )


def _iter_file(path: Path, start: int, end: int, chunk: int = 1 << 20):
    with open(path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            data = f.read(min(chunk, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data
