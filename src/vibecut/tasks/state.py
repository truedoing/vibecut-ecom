"""任务台账状态机。

状态是团队协作的公共语言，不要随意扩展；新增状态需要同时改看板与 CLI。
"""
from __future__ import annotations

from ..core.db import Database

STATUSES = ["待素材", "待初剪", "待审", "待发", "已发"]

# 允许的回退：返工是真实存在的，不禁止，只记录
TRANSITIONS = {
    "待素材": {"待初剪"},
    "待初剪": {"待审", "待素材"},
    "待审": {"待发", "待初剪"},
    "待发": {"已发", "待审"},
    "已发": {"待审"},
}


def validate(status: str) -> str:
    if status not in STATUSES:
        raise ValueError(f"未知状态: {status}（可选: {'/'.join(STATUSES)}）")
    return status


def set_status(db: Database, project: str, task_name: str, status: str,
               by: str = "") -> dict:
    validate(status)
    proj = db.ensure_project(project)
    task = db.get_task(proj["id"], task_name)
    if not task:
        raise FileNotFoundError(f"任务不存在: {project}/{task_name}")
    updated = db.update_task(task["id"], status=status, updated_by=by)
    return {**updated, "changed": task["status"] != status}


def list_tasks(db: Database, project: str | None = None,
               status: str | None = None) -> list[dict]:
    proj = db.ensure_project(project) if project else None
    if status:
        validate(status)
    rows = db.list_tasks(proj["id"] if proj else None, status)
    return [
        {k: r.get(k) for k in ("id", "project", "name", "status", "brief",
                               "version", "updated_by", "updated_at", "created_at")}
        for r in rows
    ]


def show(db: Database, project: str, task_name: str) -> dict:
    proj = db.ensure_project(project)
    task = db.get_task(proj["id"], task_name)
    if not task:
        raise FileNotFoundError(f"任务不存在: {project}/{task_name}")
    return task
