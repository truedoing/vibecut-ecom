"""素材相关 API。"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ...assets import gap as gap_mod
from ...assets import store
from ...core.db import Database
from ...core.paths import ensure_dir
from ...config import Settings
from ..files import range_response

router = APIRouter(prefix="/api/assets", tags=["assets"])


def _ctx(request: Request) -> tuple[Database, Settings]:
    return request.app.state.db, request.app.state.settings


def _split_tags(tags) -> list[str]:
    if isinstance(tags, list):
        return tags
    return [t.strip() for t in (tags or "").replace("，", ",").split(",") if t.strip()]


@router.get("")
def list_assets(request: Request, project: str | None = None, query: str = "",
                tags: str = "", layer: str | None = None, kind: str | None = None,
                scope: str = "project", limit: int = 200):
    db, settings = _ctx(request)
    if scope == "all" and not project:
        project = None
    elif not project:
        raise HTTPException(400, "必须指定 project（或使用 scope=all）")
    rows = store.search(db, settings, project=project, query=query,
                        tags=_split_tags(tags), layer=layer, kind=kind,
                        scope=scope, limit=limit)
    return {"ok": True, "count": len(rows), "assets": rows}


@router.post("")
async def upload_asset(request: Request,
                       project: str = Form(...),
                       file: UploadFile = File(...),
                       tags: str = Form(""),
                       layer: str = Form(""),
                       grade: str = Form(""),
                       note: str = Form(""),
                       origin: str = Form("upload")):
    db, settings = _ctx(request)
    suffix = Path(file.filename or "upload").suffix
    fd, tmp_name = tempfile.mkstemp(suffix=suffix)
    try:
        with open(fd, "wb") as out:
            while chunk := await file.read(1 << 20):
                out.write(chunk)
        asset = store.ingest(db, settings, project, tmp_name,
                             tags=_split_tags(tags), layer=layer, grade=grade,
                             note=note, origin=origin, filename=file.filename)
        return {"ok": True, "asset": store.decorate(settings, asset)}
    finally:
        Path(tmp_name).unlink(missing_ok=True)


@router.post("/import")
async def import_asset(request: Request):
    """Agent 常用：素材已经在本机磁盘，直接登记，不必上传。"""
    body = await request.json()
    db, settings = _ctx(request)
    project = body.get("project")
    path = body.get("path") or body.get("file")
    if not project or not path:
        raise HTTPException(400, "需要 project 与 path")
    asset = store.ingest(
        db, settings, project, path,
        tags=_split_tags(body.get("tags")), layer=body.get("layer", ""),
        grade=body.get("grade", ""), tier=body.get("tier", ""),
        note=body.get("note", ""), origin=body.get("origin", "upload"),
        source=body.get("source"), task=body.get("task"),
        copy=body.get("copy", True),
    )
    return {"ok": True, "asset": store.decorate(settings, asset),
            "duplicate": asset.get("duplicate", False)}


@router.get("/{asset_id}")
def get_asset(asset_id: int, request: Request):
    db, settings = _ctx(request)
    asset = db.get_asset(asset_id)
    if not asset:
        raise HTTPException(404, "素材不存在")
    return {"ok": True, "asset": store.decorate(settings, asset)}


@router.patch("/{asset_id}")
async def patch_asset(asset_id: int, request: Request):
    db, settings = _ctx(request)
    body = await request.json()
    fields = {}
    for key in ("tier", "layer", "grade", "status", "note", "recognizability",
                "face_rights"):
        if key in body:
            fields[key] = body[key]
    if "tags" in body:
        fields["tags_json"] = _split_tags(body["tags"])
    if "source" in body:
        fields["source_json"] = body["source"]
    asset = db.update_asset(asset_id, **fields)
    if not asset:
        raise HTTPException(404, "素材不存在")
    return {"ok": True, "asset": store.decorate(settings, asset)}


@router.delete("/{asset_id}")
def delete_asset(asset_id: int, request: Request):
    db, settings = _ctx(request)
    asset = db.get_asset(asset_id)
    if not asset:
        raise HTTPException(404, "素材不存在")
    src = store.file_path(settings, asset)
    if src.exists():
        trash = ensure_dir(settings.project_assets_dir(asset["project_slug"]) / ".trash")
        shutil.move(str(src), str(trash / src.name))
    db.delete_asset(asset_id)
    return {"ok": True, "deleted": asset_id}


@router.get("/{asset_id}/file")
def asset_file(asset_id: int, request: Request):
    db, settings = _ctx(request)
    asset = db.get_asset(asset_id)
    if not asset:
        raise HTTPException(404, "素材不存在")
    return range_response(store.file_path(settings, asset), request)


@router.get("/{asset_id}/thumb")
def asset_thumb(asset_id: int, request: Request):
    db, settings = _ctx(request)
    asset = db.get_asset(asset_id)
    if not asset:
        raise HTTPException(404, "素材不存在")
    path = store.thumb_path(settings, asset)
    if not path or not path.exists():
        raise HTTPException(404, "缩略图不存在")
    return range_response(path, request)


@router.post("/gap")
async def gap(request: Request):
    db, _ = _ctx(request)
    body = await request.json()
    project = body.get("project")
    task = body.get("task")
    if not project or not task:
        raise HTTPException(400, "需要 project 与 task")
    result = gap_mod.analyze(db, project, task, body.get("requirements"))
    return {"ok": True, **result}
