"""素材入库、检索与使用记录。

入库规则：
- 按 sha256 去重，同一项目内相同文件不会重复入库。
- 文件复制进 <project>/assets/files/，库中只存相对路径。
- 视频/图片自动抽缩略图；探测失败不阻塞入库。
"""
from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from ..config import Settings, slugify
from ..core import media
from ..core.db import Database
from ..core.paths import ensure_dir, safe_filename


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _unique_path(directory: Path, name: str) -> Path:
    stem = Path(name).stem
    suffix = Path(name).suffix
    candidate = directory / name
    i = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{i}{suffix}"
        i += 1
    return candidate


def _normalize_tags(tags) -> list[str]:
    if not tags:
        return []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.replace("，", ",").split(",")]
    return [t.strip() for t in tags if t and t.strip()]


def ingest(db: Database, settings: Settings, project: str, file: str | Path, *,
           tags=None, layer: str = "", grade: str = "", tier: str = "",
           note: str = "", origin: str = "upload", source: dict | None = None,
           task: str | None = None, copy: bool = True,
           legacy_id: str = "", filename: str | None = None) -> dict:
    proj = db.ensure_project(project)
    settings.ensure_dirs(proj["slug"])
    src = Path(file).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"文件不存在: {src}")

    digest = sha256_of(src)
    existing = db.find_asset_by_sha(proj["id"], digest)
    if existing:
        return {**existing, "duplicate": True}

    kind = media.kind_of(src)
    if kind == "file":
        raise ValueError(f"不支持的素材类型: {src.suffix}")

    assets_dir = settings.project_assets_dir(proj["slug"])
    files_dir = ensure_dir(assets_dir / "files")
    thumbs_dir = ensure_dir(assets_dir / "thumbs")

    if copy:
        dest = _unique_path(files_dir, safe_filename(filename or src.name))
        shutil.copy2(src, dest)
    else:
        dest = src
        if files_dir.resolve() not in dest.resolve().parents and dest.parent != files_dir.resolve():
            raise ValueError("copy=False 时文件必须在项目 assets/files 内")

    info = media.probe_media(dest)
    thumb = thumbs_dir / f"{safe_filename(dest.stem)}.jpg"
    if not media.make_thumb(dest, thumb, at_sec=min(0.5, (info.get("duration_sec") or 1) / 2)):
        thumb = None

    rel_path = settings.relative_to_project(proj["slug"], dest)
    thumb_rel = settings.relative_to_project(proj["slug"], thumb) if thumb else ""

    asset = db.insert_asset(
        proj["id"], kind=kind, origin=origin, rel_path=rel_path,
        thumb_rel_path=thumb_rel, duration_sec=info.get("duration_sec"),
        width=info.get("width"), height=info.get("height"),
        source=source or {"type": origin}, tags=_normalize_tags(tags),
        tier=tier, layer=layer, grade=grade, note=note, sha256=digest,
        legacy_id=legacy_id,
    )
    if task:
        t = db.get_task(proj["id"], task)
        if t:
            db.record_usage(asset["id"], t["id"], "")
    return {**asset, "duplicate": False}


def search(db: Database, settings: Settings, *, project: str | None = None,
           query: str = "", tags=None, layer: str | None = None,
           kind: str | None = None, scope: str = "project",
           limit: int = 200) -> list[dict]:
    if scope == "all":
        project_id = None
    else:
        proj = db.ensure_project(project) if project else None
        if not proj:
            raise ValueError("scope=project 时必须指定项目")
        project_id = proj["id"]
    rows = db.search_assets(project_id=project_id, query=query,
                            tags=_normalize_tags(tags), layer=layer,
                            kind=kind, limit=limit)
    return [decorate(settings, row) for row in rows]


def list_assets(db: Database, settings: Settings, project: str,
                scope: str = "project", limit: int = 500) -> list[dict]:
    return search(db, settings, project=project, scope=scope, limit=limit)


def decorate(settings: Settings, asset: dict) -> dict:
    """补上可直接访问的 URL，并去掉重复的原始 JSON 列。"""
    if not asset:
        return asset
    out = {k: v for k, v in asset.items()
           if k not in ("tags_json", "source_json")}
    out.update({
        "file_url": f"/api/assets/{asset['id']}/file",
        "thumb_url": f"/api/assets/{asset['id']}/thumb" if asset.get("thumb_rel_path") else "",
        "abs_path": str(settings.project_path(_project_slug(settings, asset), asset["rel_path"])),
    })
    return out


def _project_slug(settings: Settings, asset: dict) -> str:
    # 项目 slug 由库里 projects 表给出；调用方通常已带上 project，兜底用目录探测
    return asset.get("project_slug") or slugify(asset.get("project", ""))


def file_path(settings: Settings, asset: dict) -> Path:
    return settings.project_path(_project_slug(settings, asset), asset["rel_path"])


def thumb_path(settings: Settings, asset: dict) -> Path | None:
    if not asset.get("thumb_rel_path"):
        return None
    return settings.project_path(_project_slug(settings, asset), asset["thumb_rel_path"])
