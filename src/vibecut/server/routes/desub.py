"""去字幕 API：check（只判风险）与 run（修复）。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ...config import Settings, slugify
from ...core.db import Database
from ...desub import pipeline
from ..files import range_response

router = APIRouter(prefix="/api/desub", tags=["desub"])

ALLOWED_FILES = {"original.mp4", "result.mp4", "mask_overlay.mp4",
                 "contact_sheet.jpg", "analysis.json", "report.json"}


def _ctx(request: Request) -> tuple[Database, Settings]:
    return request.app.state.db, request.app.state.settings


def _new_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"desub_{stamp}_{uuid.uuid4().hex[:6]}"


def _resolve_source(db: Database, settings: Settings, project: str | None,
                    body: dict) -> Path:
    explicit = body.get("source") or body.get("file")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise HTTPException(400, f"文件不存在: {path}")
        return path

    ep = body.get("ep")
    if ep is None:
        raise HTTPException(400, "需要 source/file，或 project + ep + start + end")
    if not project:
        raise HTTPException(400, "按 ep 取源视频时必须指定 project")

    proj = db.ensure_project(project)
    for manifest in (settings.project_dir(proj["slug"]) / "sources" / "refs_manifest.json",
                     Path(proj.get("media_root") or "") / "refs_manifest.json"):
        if manifest.is_file():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            src = data.get(f"ep{ep}")
            if src and Path(src).exists():
                return Path(src)
    media_root = Path(proj.get("media_root") or "")
    if media_root.is_dir():
        videos = sorted(media_root.glob("*.mp4"))
        if 1 <= int(ep) <= len(videos):
            return videos[int(ep) - 1]
    raise HTTPException(404, f"找不到 EP{ep} 对应的视频")


def _timing(body: dict) -> tuple[float, float]:
    try:
        start = float(body.get("start", 0))
        end = float(body.get("end", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "start/end 必须是秒数")
    if end <= start:
        raise HTTPException(400, "end 必须大于 start")
    return start, end


def _out_dir(settings: Settings, project: str | None, report_id: str) -> Path:
    if project:
        return settings.project_desub_dir(slugify(project)) / report_id
    return settings.data_root / "desub" / report_id


def _persist(db: Database, project: str | None, report_id: str,
             source: Path, result_path: str, report: dict) -> None:
    proj = db.ensure_project(project) if project else None
    result_rel = ""
    if proj and result_path:
        try:
            result_rel = db.settings.relative_to_project(proj["slug"], Path(result_path))
        except ValueError:
            result_rel = result_path
    db.add_desub_report(report_id, proj["id"] if proj else None, str(source),
                        result_rel, report.get("risk", ""),
                        report.get("conflicts", []), report)


@router.post("/check")
async def check(request: Request):
    db, settings = _ctx(request)
    body = await request.json()
    project = body.get("project") or None
    src = _resolve_source(db, settings, project, body)
    start, end = _timing(body)

    report_id = _new_id()
    out_dir = _out_dir(settings, project, report_id)
    report = await run_in_threadpool(
        pipeline.run_file, src, start, end, out_dir,
        sample_every=int(body.get("sample_every", 6)),
        mask_pad=int(body.get("mask_pad", 4)),
        backend=body.get("backend", "auto"),
        det_only=bool(body.get("det_only", False)),
        analyze_only=True, verbose=False,
        label=body.get("label", ""),
    )
    _persist(db, project, report_id, src, "", report)
    return {"ok": True, "report_id": report_id,
            "risk": report.get("risk"),
            "conflicts": report.get("conflicts", []),
            "removed_texts": report.get("removed_texts", []),
            "packaging_texts": report.get("packaging_texts", []),
            "report": report,
            "files": {"analysis": f"/api/desub/reports/{report_id}/file/analysis.json"}}


@router.post("/run")
async def run(request: Request):
    db, settings = _ctx(request)
    body = await request.json()
    project = body.get("project")
    if not project:
        raise HTTPException(400, "run 需要 project，结果要落到项目目录")
    src = _resolve_source(db, settings, project, body)
    start, end = _timing(body)

    report_id = _new_id()
    out_dir = _out_dir(settings, project, report_id)
    report = await run_in_threadpool(
        pipeline.run_file, src, start, end, out_dir,
        sample_every=int(body.get("sample_every", 6)),
        mask_pad=int(body.get("mask_pad", 4)),
        backend=body.get("backend", "auto"),
        det_only=bool(body.get("det_only", False)),
        analyze_only=False, verbose=False,
        label=body.get("label", ""),
    )
    _persist(db, project, report_id, src, report.get("files", {}).get("result", ""), report)
    return {"ok": True, "report_id": report_id, "risk": report.get("risk"),
            "qa": report.get("qa", {}),
            "files": {
                "result": f"/api/desub/reports/{report_id}/file/result.mp4",
                "overlay": f"/api/desub/reports/{report_id}/file/mask_overlay.mp4",
                "contact_sheet": f"/api/desub/reports/{report_id}/file/contact_sheet.jpg",
                "original": f"/api/desub/reports/{report_id}/file/original.mp4",
            },
            "report": report}


@router.get("/reports")
def list_reports(request: Request, project: str | None = None, limit: int = 100):
    db, _ = _ctx(request)
    proj = db.ensure_project(project) if project else None
    rows = db.list_desub_reports(proj["id"] if proj else None, limit)
    return {"ok": True, "count": len(rows), "reports": rows}


@router.get("/reports/{report_id}")
def get_report(report_id: str, request: Request):
    db, _ = _ctx(request)
    row = db.get_desub_report(report_id)
    if not row:
        raise HTTPException(404, "报告不存在")
    return {"ok": True, "report": row}


@router.get("/reports/{report_id}/file/{name}")
def report_file(report_id: str, name: str, request: Request):
    db, _ = _ctx(request)
    if name not in ALLOWED_FILES:
        raise HTTPException(400, "不允许的文件")
    row = db.get_desub_report(report_id)
    if not row:
        raise HTTPException(404, "报告不存在")

    report = row.get("report") or {}
    files = report.get("files") or {}
    out_dir = Path(report.get("out_dir") or "")
    mapping = {
        "original.mp4": files.get("original"),
        "result.mp4": files.get("result"),
        "mask_overlay.mp4": files.get("mask_overlay"),
        "contact_sheet.jpg": files.get("contact_sheet"),
        "analysis.json": out_dir / "analysis.json",
        "report.json": out_dir / "report.json",
    }
    path = Path(mapping.get(name) or "")
    if (not path or not path.exists()) and out_dir:
        # 数据根搬迁后绝对路径会失效，按 out_dir + 文件名兜底
        path = out_dir / name
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    return range_response(path, request)
