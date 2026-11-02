"""项目与任务台账 API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ...config import Settings, slugify
from ...core.db import Database
from ...tasks import state

router = APIRouter(prefix="/api", tags=["tasks"])


def _ctx(request: Request) -> tuple[Database, Settings]:
    return request.app.state.db, request.app.state.settings


@router.get("/projects")
def list_projects(request: Request):
    db, _ = _ctx(request)
    return {"ok": True, "projects": db.list_projects()}


@router.post("/projects")
async def create_project(request: Request):
    db, settings = _ctx(request)
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "需要 name")
    proj = db.ensure_project(name, body.get("media_root", ""))
    settings.ensure_dirs(slugify(name))
    return {"ok": True, "project": proj}


@router.get("/board")
def board(request: Request, project: str):
    db, _ = _ctx(request)
    proj = db.get_project(project)
    if not proj:
        raise HTTPException(404, "项目不存在")
    tasks = db.list_tasks(proj["id"])
    columns = {s: [] for s in state.STATUSES}
    for task in tasks:
        columns.setdefault(task["status"], []).append({
            k: task.get(k) for k in ("id", "name", "status", "brief",
                                     "version", "updated_by", "updated_at")
        })
    return {"ok": True, "project": proj["name"], "columns": columns,
            "statuses": state.STATUSES}


@router.get("/tasks")
def list_tasks(request: Request, project: str | None = None,
               status: str | None = None):
    db, _ = _ctx(request)
    try:
        rows = state.list_tasks(db, project, status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "count": len(rows), "tasks": rows}


@router.post("/tasks")
async def create_task(request: Request):
    db, _ = _ctx(request)
    body = await request.json()
    project = body.get("project")
    name = body.get("name")
    if not project or not name:
        raise HTTPException(400, "需要 project 与 name")
    proj = db.ensure_project(project)
    task = db.ensure_task(proj["id"], name, body.get("brief", ""),
                          body.get("status", "待素材"))
    return {"ok": True, "task": task}


@router.get("/tasks/{project}/{task}")
def get_task(project: str, task: str, request: Request):
    db, _ = _ctx(request)
    try:
        return {"ok": True, "task": state.show(db, project, task)}
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@router.patch("/tasks/{project}/{task}")
async def patch_task(project: str, task: str, request: Request):
    db, _ = _ctx(request)
    body = await request.json()
    proj = db.ensure_project(project)
    row = db.get_task(proj["id"], task)
    if not row:
        raise HTTPException(404, "任务不存在")

    if "status" in body:
        try:
            updated = state.set_status(db, project, task, body["status"],
                                       body.get("by", ""))
        except ValueError as e:
            raise HTTPException(400, str(e))
    else:
        updated = db.update_task(
            row["id"], brief=body.get("brief"),
            script=body.get("script"), reference_set=body.get("reference_set"),
            updated_by=body.get("by", ""),
        )
    return {"ok": True, "task": updated}
