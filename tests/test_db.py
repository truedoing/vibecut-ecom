from __future__ import annotations

import json

from vibecut.config import load_settings
from vibecut.core.db import Database


def make_db(tmp_path):
    settings = load_settings(tmp_path)
    db = Database(settings)
    db.init()
    return settings, db


def test_project_and_task(tmp_path):
    _, db = make_db(tmp_path)
    proj = db.ensure_project("测试项目", "/media/root")
    assert proj["slug"] == "测试项目"

    task = db.ensure_task(proj["id"], "Task1")
    assert task["status"] == "待素材"

    updated = db.update_task(task["id"], status="待初剪", updated_by="小王")
    assert updated["status"] == "待初剪"
    assert updated["updated_by"] == "小王"
    assert updated["version"] == 1

    rows = db.list_tasks(proj["id"])
    assert len(rows) == 1 and rows[0]["name"] == "Task1"


def test_asset_insert_search_and_usage(tmp_path):
    _, db = make_db(tmp_path)
    proj = db.ensure_project("测试项目")
    task = db.ensure_task(proj["id"], "Task1")

    asset = db.insert_asset(
        proj["id"], kind="video", origin="upload", rel_path="assets/files/a.mp4",
        tags=["实拍", "冲泡"], layer="L1", grade="A", note="玻璃杯冲水特写",
        sha256="abc", legacy_id="ast_001",
    )
    assert asset["id"] > 0
    assert asset["project_slug"] == "测试项目"
    assert asset["tags"] == ["实拍", "冲泡"]

    assert db.find_asset_by_sha(proj["id"], "abc")["id"] == asset["id"]
    assert db.find_asset_by_legacy(proj["id"], "ast_001")["id"] == asset["id"]

    assert len(db.search_assets(proj["id"], query="冲泡")) == 1
    assert len(db.search_assets(proj["id"], tags=["实拍"])) == 1
    assert len(db.search_assets(proj["id"], tags=["厨房"])) == 0

    db.record_usage(asset["id"], task["id"], "S01")
    refreshed = db.get_asset(asset["id"])
    assert refreshed["usage_count"] == 1
    assert refreshed["last_used_at"]


def test_attempt_roundtrip(tmp_path):
    _, db = make_db(tmp_path)
    proj = db.ensure_project("测试项目")
    task = db.ensure_task(proj["id"], "Task1")
    row = db.insert_attempt("img_001", proj["id"], task_id=task["id"],
                            kind="image", status="succeeded",
                            params={"prompt": "test"})
    assert row["id"] == "img_001"
    assert row["params"]["prompt"] == "test"
    assert db.list_attempts(proj["id"])[0]["id"] == "img_001"


def test_json_columns_decode(tmp_path):
    _, db = make_db(tmp_path)
    proj = db.ensure_project("测试项目")
    task = db.ensure_task(proj["id"], "Task1")
    db.update_task(task["id"], script={"segments": [{"text": "hi"}]})
    row = db.get_task(proj["id"], "Task1")
    assert json.loads(row["script_json"])["segments"][0]["text"] == "hi"
    assert row["script"]["segments"][0]["text"] == "hi"
