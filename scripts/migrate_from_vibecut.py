#!/usr/bin/env python3
"""把旧 VIBECAP 项目的资产/任务/抽卡记录迁移到新库。

用法：
  python scripts/migrate_from_vibecut.py \
      --src /Users/zgl/VIBECAP/羽衣甘蓝 \
      --dst ~/VibeCutEcomData \
      [--dry-run]

幂等：资产按 legacy_id、任务按 (project, name)、抽卡按 attempt_id 去重。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vibecut.config import load_settings  # noqa: E402
from vibecut.core.db import Database  # noqa: E402
from vibecut.core.paths import ensure_dir  # noqa: E402


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def copy_file(src: Path, dst: Path, dry: bool) -> bool:
    if not src.exists():
        return False
    if not dry:
        ensure_dir(dst.parent)
        if not dst.exists():
            shutil.copy2(src, dst)
    return True


def migrate_assets(db: Database, settings, proj: dict, src: Path,
                   dry: bool, report: dict) -> dict[str, int]:
    manifest = read_json(src / "assets" / "manifest.json", {}) or {}
    items = manifest.get("assets") or []
    slug = proj["slug"]
    old_to_new: dict[str, int] = {}

    for item in items:
        legacy = item.get("asset_id") or ""
        rel_old = item.get("file") or ""
        if not rel_old:
            report["skipped"].append({"legacy_id": legacy, "reason": "no file"})
            continue
        src_file = src / "assets" / rel_old
        if not src_file.exists():
            report["missing"].append(str(src_file))
            continue

        existing = db.find_asset_by_legacy(proj["id"], legacy)
        if existing:
            old_to_new[legacy] = existing["id"]
            report["assets_skipped"] += 1
            continue

        rel_new = f"assets/files/{src_file.name}"
        thumb_old = item.get("thumb") or ""
        thumb_new = ""
        if thumb_old and (src / "assets" / thumb_old).exists():
            thumb_new = f"assets/thumbs/{Path(thumb_old).name}"
            copy_file(src / "assets" / thumb_old,
                      settings.project_dir(slug) / thumb_new, dry)
        copy_file(src_file, settings.project_dir(slug) / rel_new, dry)

        if dry:
            report["assets"] += 1
            continue

        usage = item.get("usage") or {}
        asset = db.insert_asset(
            proj["id"],
            legacy_id=legacy,
            kind=item.get("kind") or "video",
            origin=item.get("origin") or "upload",
            rel_path=rel_new,
            thumb_rel_path=thumb_new,
            duration_sec=item.get("duration_sec"),
            width=item.get("width"),
            height=item.get("height"),
            source=item.get("source") or {"type": item.get("origin") or "upload"},
            tags=item.get("tags") or [],
            tier=item.get("tier") or "",
            layer=item.get("layer") or "",
            grade=item.get("grade") or "",
            recognizability=item.get("recognizability") or "",
            face_rights=item.get("face_rights") or "",
            status=item.get("status") or "available",
            usage_count=usage.get("count") or 0,
            last_used_at=usage.get("last_used_at"),
            note=item.get("note") or "",
            sha256=sha256_of(src_file),
            created_at=item.get("created_at"),
        )
        old_to_new[legacy] = asset["id"]
        report["assets"] += 1

    return old_to_new


def derive_status(task_dir: Path) -> str:
    if (task_dir / "versions").is_dir() or any(task_dir.glob("*final*")):
        return "待审"
    if (task_dir / "storyboard.json").exists():
        return "待初剪"
    return "待素材"


def migrate_tasks(db: Database, proj: dict, src: Path, dry: bool,
                  report: dict, old_to_new: dict[str, int]) -> None:
    tasks_dir = src / "tasks"
    if not tasks_dir.is_dir():
        return
    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir() or not task_dir.name.startswith("Task"):
            continue
        name = task_dir.name
        segments = read_json(task_dir / "segments.json")
        reference_set = read_json(task_dir / "reference_set.json")
        status = derive_status(task_dir)

        if dry:
            report["tasks"] += 1
            continue

        task = db.ensure_task(proj["id"], name, status=status)
        db.update_task(task["id"], status=status,
                       script=segments or [], reference_set=reference_set or {},
                       updated_by="migrate")
        report["tasks"] += 1

        attempts = read_json(task_dir / "generation_attempts.json", {}) or {}
        for attempt in attempts.get("attempts") or []:
            attempt_id = attempt.get("attempt_id")
            if not attempt_id:
                continue
            old_asset = attempt.get("asset_id") or ""
            db.insert_attempt(
                attempt_id=attempt_id,
                project_id=proj["id"],
                task_id=task["id"],
                kind=attempt.get("kind") or "",
                status=attempt.get("status") or "",
                error=attempt.get("error") or "",
                asset_id=old_to_new.get(old_asset),
                selected=1 if attempt.get("selected") else 0,
                provider=attempt.get("provider") or "",
                model=attempt.get("model") or "",
                prompt=attempt.get("prompt") or "",
                params=attempt,
                batch_id=attempt.get("batch_id") or "",
                shot_id=attempt.get("shot_id") or "",
                ref_role=attempt.get("ref_role") or "",
                created_at=attempt.get("created_at"),
                finished_at=attempt.get("finished_at"),
            )
            report["attempts"] += 1


def migrate_refs_manifest(settings, proj: dict, src: Path, dry: bool) -> None:
    source = src / "sources" / "refs_manifest.json"
    if not source.exists():
        return
    dst = settings.project_dir(proj["slug"]) / "sources" / "refs_manifest.json"
    copy_file(source, dst, dry)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="旧项目数据目录")
    ap.add_argument("--dst", required=True, help="新 data_root")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.src).expanduser().resolve()
    settings = load_settings(args.dst)
    db = Database(settings)
    db.init()

    config_path = src.parent / "projects" / f"{src.name}.json"
    config = read_json(config_path, {}) or {}
    proj = db.ensure_project(src.name, config.get("reference_videos_dir", ""))
    settings.ensure_dirs(proj["slug"])

    report = {"source": str(src), "project": proj["name"], "dry_run": args.dry_run,
              "assets": 0, "assets_skipped": 0, "tasks": 0, "attempts": 0,
              "missing": [], "skipped": []}

    old_to_new = migrate_assets(db, settings, proj, src, args.dry_run, report)
    migrate_tasks(db, proj, src, args.dry_run, report, old_to_new)
    migrate_refs_manifest(settings, proj, src, args.dry_run)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
