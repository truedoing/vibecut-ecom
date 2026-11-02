"""素材缺口分析。

优先使用 WorkBuddy 显式给出的 requirements（最准）：
  {"items":[{"id":"r1","desc":"产品包装特写","tags":["包装","特写"],
             "kind":"video","action":"实拍"}]}

没有 requirements 时，退化为按脚本段落文本做关键词匹配，并标注 low confidence。
v1 不调用 LLM：缺口判断应该由上游 Agent 负责，这里只做「有没有」的核对。
"""
from __future__ import annotations

import re

from ..core.db import Database

_SPLIT = re.compile(r"[\s，。！？、,.!?：:;；\"'“”‘’（）()\[\]【】—\-~·/]+")


def _terms(text: str) -> list[str]:
    out = []
    for part in _SPLIT.split(text or ""):
        part = part.strip()
        if len(part) >= 2:
            out.append(part)
    return out


def _asset_blob(asset: dict) -> str:
    tags = asset.get("tags") or []
    return " ".join([asset.get("note", ""), asset.get("rel_path", ""), *tags])


def _matches(req: dict, asset: dict) -> float:
    """返回 0–1 的匹配分。0 表示不匹配。"""
    wanted = [t for t in (req.get("tags") or []) if t]
    have = set(asset.get("tags") or [])
    blob = _asset_blob(asset).lower()

    if wanted:
        hit = sum(1 for t in wanted if t in have or t.lower() in blob)
        return hit / len(wanted)

    for term in _terms(req.get("desc", "")):
        if term.lower() in blob:
            return 1.0
    return 0.0


def _script_requirements(task: dict) -> list[dict]:
    """只取脚本里明确描述画面的字段；旁白文本不是素材需求。"""
    script = task.get("script") or {}
    segments = script.get("segments") if isinstance(script, dict) else script
    items = []
    for i, seg in enumerate(segments or []):
        if not isinstance(seg, dict):
            continue
        desc = (seg.get("visual") or seg.get("visual_keywords")
                or seg.get("keywords") or seg.get("material_desc") or "")
        tags = seg.get("material_tags") or []
        if not desc and not tags:
            continue
        items.append({
            "id": f"s{i + 1}",
            "desc": desc,
            "tags": tags,
            "kind": seg.get("material_kind") or "",
            "action": seg.get("material_action") or "搜索",
            "confidence": "low",
        })
    return items


def _requirements_from_input(requirements) -> list[dict]:
    if not requirements:
        return []
    if isinstance(requirements, dict):
        requirements = requirements.get("items") or requirements.get("requirements") or []
    out = []
    for i, item in enumerate(requirements or []):
        if isinstance(item, str):
            item = {"desc": item}
        out.append({
            "id": item.get("id") or f"r{i + 1}",
            "desc": item.get("desc") or item.get("description") or "",
            "tags": item.get("tags") or [],
            "kind": item.get("kind") or "",
            "action": item.get("action") or item.get("suggested") or "搜索",
            "confidence": item.get("confidence") or "high",
        })
    return out


def analyze(db: Database, project: str, task_name: str,
            requirements=None, threshold: float = 0.5) -> dict:
    proj = db.ensure_project(project)
    task = db.get_task(proj["id"], task_name)
    if not task:
        raise FileNotFoundError(f"任务不存在: {project}/{task_name}")

    reqs = _requirements_from_input(requirements) or _script_requirements(task)
    source = "explicit" if requirements else ("script" if reqs else "none")
    assets = db.search_assets(project_id=proj["id"], status="available", limit=5000)

    matched, missing = [], []
    for req in reqs:
        scored = []
        for asset in assets:
            score = _matches(req, asset)
            if score >= threshold:
                scored.append((score, asset))
        scored.sort(key=lambda x: -x[0])
        if scored:
            matched.append({
                **req,
                "asset_id": scored[0][1]["id"],
                "asset_note": scored[0][1].get("note", ""),
                "score": round(scored[0][0], 2),
                "candidates": [a["id"] for _, a in scored[:5]],
            })
        else:
            missing.append(req)

    result = {
        "project": proj["name"],
        "task": task["name"],
        "requirements_source": source,
        "total": len(reqs),
        "matched_count": len(matched),
        "missing_count": len(missing),
        "matched": matched,
        "missing": missing,
    }
    if not reqs:
        result["hint"] = ("脚本里没有画面字段，无法推断素材需求。"
                          "请用 --requirements 传入 WorkBuddy 生成的素材需求 JSON。")
    return result
