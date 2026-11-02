from __future__ import annotations

from PIL import Image
from fastapi.testclient import TestClient

from vibecut.config import load_settings
from vibecut.server.app import create_app


def make_client(tmp_path):
    settings = load_settings(tmp_path)
    return TestClient(create_app(settings)), settings


def _png(path, size=(320, 240), color=(30, 120, 60)):
    Image.new("RGB", size, color).save(path)
    return path


def test_health(tmp_path):
    client, _ = make_client(tmp_path)
    data = client.get("/api/health").json()
    assert data["ok"] is True
    assert data["projects"] == 0


def test_project_task_and_board(tmp_path):
    client, _ = make_client(tmp_path)
    assert client.post("/api/projects", json={"name": "测试项目"}).json()["ok"]

    task = client.post("/api/tasks", json={
        "project": "测试项目", "name": "Task1", "brief": "测试"
    }).json()["task"]
    assert task["status"] == "待素材"

    patched = client.patch("/api/tasks/测试项目/Task1",
                           json={"status": "待初剪", "by": "小王"}).json()["task"]
    assert patched["status"] == "待初剪"
    assert patched["updated_by"] == "小王"

    board = client.get("/api/board", params={"project": "测试项目"}).json()
    assert len(board["columns"]["待初剪"]) == 1

    bad = client.patch("/api/tasks/测试项目/Task1", json={"status": "不存在"})
    assert bad.status_code == 400


def test_asset_import_search_gap_and_files(tmp_path):
    client, settings = make_client(tmp_path)
    client.post("/api/projects", json={"name": "测试项目"})
    client.post("/api/tasks", json={"project": "测试项目", "name": "Task1"})

    img = _png(tmp_path / "包装特写.png")
    res = client.post("/api/assets/import", json={
        "project": "测试项目", "path": str(img),
        "tags": "测试,包装", "layer": "L1", "grade": "A",
        "note": "包装正面",
    }).json()
    assert res["ok"] and res["asset"]["kind"] == "image"
    asset_id = res["asset"]["id"]

    # 重复入库去重
    dup = client.post("/api/assets/import", json={
        "project": "测试项目", "path": str(img)}).json()
    assert dup["duplicate"] is True and dup["asset"]["id"] == asset_id

    listed = client.get("/api/assets", params={"project": "测试项目"}).json()
    assert listed["count"] == 1
    assert listed["assets"][0]["thumb_url"]

    found = client.get("/api/assets", params={
        "project": "测试项目", "query": "包装"}).json()
    assert found["count"] == 1

    gap = client.post("/api/assets/gap", json={
        "project": "测试项目", "task": "Task1",
        "requirements": {"items": [
            {"desc": "包装正面", "tags": ["包装"]},
            {"desc": "冲泡特写", "tags": ["冲泡"]},
        ]},
    }).json()
    assert gap["matched_count"] == 1
    assert gap["missing"][0]["desc"] == "冲泡特写"

    file_resp = client.get(f"/api/assets/{asset_id}/file")
    assert file_resp.status_code == 200
    assert file_resp.headers["accept-ranges"] == "bytes"

    range_resp = client.get(f"/api/assets/{asset_id}/file",
                            headers={"Range": "bytes=0-9"})
    assert range_resp.status_code == 206
    assert len(range_resp.content) == 10

    thumb = client.get(f"/api/assets/{asset_id}/thumb")
    assert thumb.status_code == 200


def test_scope_all_cross_project(tmp_path):
    client, _ = make_client(tmp_path)
    for name in ("甲项目", "乙项目"):
        client.post("/api/projects", json={"name": name})
        img = _png(tmp_path / f"{name}.png", color=(200, 10, 10))
        client.post("/api/assets/import", json={
            "project": name, "path": str(img), "tags": "通用"})

    all_rows = client.get("/api/assets", params={"scope": "all"}).json()
    assert all_rows["count"] == 2

    one = client.get("/api/assets", params={
        "project": "甲项目", "query": "通用"}).json()
    assert one["count"] == 1
