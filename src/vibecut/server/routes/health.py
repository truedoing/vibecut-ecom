"""健康检查与能力自检。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ...core import media

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health(request: Request):
    db = request.app.state.db
    settings = request.app.state.settings
    ffmpeg = ""
    error = ""
    try:
        ffmpeg = media.ffmpeg_bin()
    except FileNotFoundError as e:
        error = str(e)
    projects = db.list_projects()
    return {
        "ok": not error,
        "version": "0.1.0",
        "data_root": str(settings.data_root),
        "db_path": str(settings.db_path),
        "ffmpeg": ffmpeg,
        "error": error,
        "projects": len(projects),
        "assets": sum(p.get("asset_count", 0) for p in projects),
        "tasks": sum(p.get("task_count", 0) for p in projects),
    }


@router.get("/status")
def status(request: Request):
    return health(request)
