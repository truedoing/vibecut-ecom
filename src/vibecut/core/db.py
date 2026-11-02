"""SQLite 唯一真源：schema、迁移、写事务。

原则：
- 每个线程一条连接；WAL + busy_timeout。
- 所有写操作走 BEGIN IMMEDIATE，并用进程内 RLock 串行化。
- 媒体文件只在磁盘，库里只存项目内相对路径。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ..config import Settings, slugify

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL UNIQUE,
  slug        TEXT NOT NULL UNIQUE,
  media_root  TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id         INTEGER NOT NULL REFERENCES projects(id),
  name               TEXT NOT NULL,
  status             TEXT NOT NULL DEFAULT '待素材',
  brief              TEXT NOT NULL DEFAULT '',
  script_json        TEXT NOT NULL DEFAULT '{}',
  reference_set_json TEXT NOT NULL DEFAULT '{}',
  version            INTEGER NOT NULL DEFAULT 0,
  updated_by         TEXT NOT NULL DEFAULT '',
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL,
  UNIQUE(project_id, name)
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id, status);

CREATE TABLE IF NOT EXISTS assets (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id      INTEGER NOT NULL REFERENCES projects(id),
  legacy_id       TEXT NOT NULL DEFAULT '',
  kind            TEXT NOT NULL,
  origin          TEXT NOT NULL DEFAULT 'upload',
  rel_path        TEXT NOT NULL,
  thumb_rel_path  TEXT NOT NULL DEFAULT '',
  duration_sec    REAL,
  width           INTEGER,
  height          INTEGER,
  source_json     TEXT NOT NULL DEFAULT '{}',
  tags_json       TEXT NOT NULL DEFAULT '[]',
  tier            TEXT NOT NULL DEFAULT '',
  layer           TEXT NOT NULL DEFAULT '',
  grade           TEXT NOT NULL DEFAULT '',
  recognizability TEXT NOT NULL DEFAULT '',
  face_rights     TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'available',
  usage_count     INTEGER NOT NULL DEFAULT 0,
  last_used_at    TEXT,
  note            TEXT NOT NULL DEFAULT '',
  sha256          TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_project ON assets(project_id, status);
CREATE INDEX IF NOT EXISTS idx_assets_sha ON assets(sha256);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_legacy
  ON assets(project_id, legacy_id) WHERE legacy_id <> '';

CREATE TABLE IF NOT EXISTS asset_usage (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id INTEGER NOT NULL REFERENCES assets(id),
  task_id  INTEGER REFERENCES tasks(id),
  shot_id  TEXT NOT NULL DEFAULT '',
  used_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
  id           TEXT PRIMARY KEY,
  project_id   INTEGER NOT NULL,
  task_id      INTEGER REFERENCES tasks(id),
  kind         TEXT NOT NULL,
  status       TEXT NOT NULL,
  error        TEXT NOT NULL DEFAULT '',
  asset_id     INTEGER REFERENCES assets(id),
  selected     INTEGER NOT NULL DEFAULT 0,
  provider     TEXT NOT NULL DEFAULT '',
  model        TEXT NOT NULL DEFAULT '',
  prompt       TEXT NOT NULL DEFAULT '',
  params_json  TEXT NOT NULL DEFAULT '{}',
  batch_id     TEXT NOT NULL DEFAULT '',
  shot_id      TEXT NOT NULL DEFAULT '',
  ref_role     TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL,
  finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS versions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id     INTEGER NOT NULL REFERENCES tasks(id),
  rel_path    TEXT NOT NULL,
  note        TEXT NOT NULL DEFAULT '',
  created_by  TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id       INTEGER NOT NULL REFERENCES tasks(id),
  version_id    INTEGER REFERENCES versions(id),
  status        TEXT NOT NULL,
  findings_json TEXT NOT NULL DEFAULT '[]',
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS desub_reports (
  id              TEXT PRIMARY KEY,
  project_id      INTEGER REFERENCES projects(id),
  source_rel_path TEXT NOT NULL,
  result_rel_path TEXT NOT NULL DEFAULT '',
  risk            TEXT NOT NULL DEFAULT '',
  conflicts_json  TEXT NOT NULL DEFAULT '[]',
  report_json     TEXT NOT NULL DEFAULT '{}',
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id          TEXT PRIMARY KEY,
  project_id  INTEGER REFERENCES projects(id),
  task_id     INTEGER REFERENCES tasks(id),
  kind        TEXT NOT NULL,
  status      TEXT NOT NULL,
  progress    REAL NOT NULL DEFAULT 0,
  params_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  error       TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def _decode(row: dict | None) -> dict | None:
    """把 *_json 列解成 Python 对象，方便上层直接用。"""
    if row is None:
        return None
    for key, target in (("source_json", "source"), ("tags_json", "tags"),
                        ("script_json", "script"),
                        ("reference_set_json", "reference_set"),
                        ("params_json", "params"), ("result_json", "result"),
                        ("findings_json", "findings"),
                        ("conflicts_json", "conflicts"),
                        ("report_json", "report")):
        if key in row:
            try:
                row[target] = json.loads(row[key] or "null")
            except Exception:
                row[target] = None
    return row


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.db_path
        self._local = threading.local()
        self._write_lock = threading.RLock()

    # ── 连接 ──
    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=30,
                                   isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=20000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def init(self) -> None:
        self.settings.data_root.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        with self._write_lock:
            conn.executescript(SCHEMA)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def write(self):
        """写事务：进程内串行 + BEGIN IMMEDIATE，避免读-改-写互相覆盖。"""
        conn = self.connect()
        with self._write_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, params=()) -> list[dict]:
        rows = self.connect().execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_one(self, sql: str, params=()) -> dict | None:
        return _row_to_dict(self.connect().execute(sql, params).fetchone())

    # ── projects ──
    def ensure_project(self, name: str, media_root: str = "") -> dict:
        slug = slugify(name)
        with self.write() as conn:
            row = conn.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
            if row:
                if media_root:
                    conn.execute("UPDATE projects SET media_root=? WHERE id=?",
                                 (media_root, row["id"]))
                return dict(conn.execute("SELECT * FROM projects WHERE id=?",
                                         (row["id"],)).fetchone())
            conn.execute(
                "INSERT INTO projects(name, slug, media_root, created_at) VALUES(?,?,?,?)",
                (name, slug, media_root, now_iso()),
            )
            row = conn.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
        self.settings.ensure_dirs(slug)
        return dict(row)

    def get_project(self, name_or_id: str | int) -> dict | None:
        if isinstance(name_or_id, int) or str(name_or_id).isdigit():
            return self.query_one("SELECT * FROM projects WHERE id=?", (int(name_or_id),))
        return self.query_one("SELECT * FROM projects WHERE name=? OR slug=?",
                              (name_or_id, name_or_id))

    def list_projects(self) -> list[dict]:
        return self.query(
            "SELECT p.*, (SELECT COUNT(*) FROM assets a WHERE a.project_id=p.id) AS asset_count,"
            " (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id) AS task_count"
            " FROM projects p ORDER BY p.id"
        )

    # ── tasks ──
    def ensure_task(self, project_id: int, name: str, brief: str = "",
                    status: str = "待素材") -> dict:
        ts = now_iso()
        with self.write() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE project_id=? AND name=?",
                               (project_id, name)).fetchone()
            if row:
                return _decode(dict(row))
            conn.execute(
                "INSERT INTO tasks(project_id, name, status, brief, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?)",
                (project_id, name, status, brief, ts, ts),
            )
            row = conn.execute("SELECT * FROM tasks WHERE project_id=? AND name=?",
                               (project_id, name)).fetchone()
        return _decode(dict(row))

    def get_task(self, project_id: int, name: str) -> dict | None:
        return _decode(self.query_one(
            "SELECT * FROM tasks WHERE project_id=? AND name=?", (project_id, name)))

    def get_task_by_id(self, task_id: int) -> dict | None:
        return _decode(self.query_one("SELECT * FROM tasks WHERE id=?", (task_id,)))

    def list_tasks(self, project_id: int | None = None,
                   status: str | None = None) -> list[dict]:
        sql = "SELECT t.*, p.name AS project FROM tasks t JOIN projects p ON p.id=t.project_id"
        where, params = [], []
        if project_id is not None:
            where.append("t.project_id=?")
            params.append(project_id)
        if status:
            where.append("t.status=?")
            params.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY t.updated_at DESC"
        return [_decode(r) for r in self.query(sql, params)]

    def update_task(self, task_id: int, *, status: str | None = None,
                    brief: str | None = None, script=None, reference_set=None,
                    updated_by: str = "") -> dict | None:
        fields, params = [], []
        if status is not None:
            fields.append("status=?")
            params.append(status)
        if brief is not None:
            fields.append("brief=?")
            params.append(brief)
        if script is not None:
            fields.append("script_json=?")
            params.append(json.dumps(script, ensure_ascii=False))
        if reference_set is not None:
            fields.append("reference_set_json=?")
            params.append(json.dumps(reference_set, ensure_ascii=False))
        if not fields:
            return self.get_task_by_id(task_id)
        fields += ["version=version+1", "updated_at=?", "updated_by=?"]
        params += [now_iso(), updated_by]
        params.append(task_id)
        with self.write() as conn:
            conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", params)
        return self.get_task_by_id(task_id)

    # ── assets ──
    def insert_asset(self, project_id: int, **kw) -> dict:
        values = {
            "project_id": project_id,
            "legacy_id": kw.get("legacy_id", ""),
            "kind": kw["kind"],
            "origin": kw.get("origin", "upload"),
            "rel_path": kw["rel_path"],
            "thumb_rel_path": kw.get("thumb_rel_path", ""),
            "duration_sec": kw.get("duration_sec"),
            "width": kw.get("width"),
            "height": kw.get("height"),
            "source_json": json.dumps(kw.get("source") or {}, ensure_ascii=False),
            "tags_json": json.dumps(kw.get("tags") or [], ensure_ascii=False),
            "tier": kw.get("tier", ""),
            "layer": kw.get("layer", ""),
            "grade": kw.get("grade", ""),
            "recognizability": kw.get("recognizability", ""),
            "face_rights": kw.get("face_rights", ""),
            "status": kw.get("status", "available"),
            "usage_count": kw.get("usage_count", 0),
            "last_used_at": kw.get("last_used_at"),
            "note": kw.get("note", ""),
            "sha256": kw.get("sha256", ""),
            "created_at": kw.get("created_at") or now_iso(),
        }
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with self.write() as conn:
            cur = conn.execute(f"INSERT INTO assets({cols}) VALUES({marks})",
                               list(values.values()))
            asset_id = cur.lastrowid
        return self.get_asset(asset_id)

    def get_asset(self, asset_id: int) -> dict | None:
        return _decode(self.query_one(
            "SELECT a.*, p.name AS project, p.slug AS project_slug FROM assets a"
            " JOIN projects p ON p.id=a.project_id WHERE a.id=?", (asset_id,)))

    def find_asset_by_sha(self, project_id: int, sha256: str) -> dict | None:
        if not sha256:
            return None
        return _decode(self.query_one(
            "SELECT a.*, p.name AS project, p.slug AS project_slug FROM assets a"
            " JOIN projects p ON p.id=a.project_id"
            " WHERE a.project_id=? AND a.sha256=? LIMIT 1",
            (project_id, sha256)))

    def find_asset_by_legacy(self, project_id: int, legacy_id: str) -> dict | None:
        if not legacy_id:
            return None
        return _decode(self.query_one(
            "SELECT a.*, p.name AS project, p.slug AS project_slug FROM assets a"
            " JOIN projects p ON p.id=a.project_id"
            " WHERE a.project_id=? AND a.legacy_id=?",
            (project_id, legacy_id)))

    def search_assets(self, project_id: int | None = None, query: str = "",
                      tags: list[str] | None = None, layer: str | None = None,
                      kind: str | None = None, status: str = "available",
                      limit: int = 200) -> list[dict]:
        where, params = [], []
        if project_id is not None:
            where.append("a.project_id=?")
            params.append(project_id)
        if status:
            where.append("a.status=?")
            params.append(status)
        if kind:
            where.append("a.kind=?")
            params.append(kind)
        if layer:
            where.append("a.layer=?")
            params.append(layer)
        for tag in tags or []:
            where.append("a.tags_json LIKE ?")
            params.append(f"%{tag}%")
        if query:
            where.append("(a.note LIKE ? OR a.tags_json LIKE ? OR a.rel_path LIKE ?)")
            params += [f"%{query}%"] * 3
        sql = ("SELECT a.*, p.name AS project, p.slug AS project_slug FROM assets a "
               "JOIN projects p ON p.id=a.project_id")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY a.created_at DESC LIMIT ?"
        params.append(limit)
        return [_decode(r) for r in self.query(sql, params)]

    def list_assets(self, project_id: int, limit: int = 500) -> list[dict]:
        return self.search_assets(project_id=project_id, status="", limit=limit)

    def update_asset(self, asset_id: int, **fields) -> dict | None:
        allowed = {"tags_json", "tier", "layer", "grade", "status", "note",
                   "recognizability", "face_rights", "source_json"}
        sets, params = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in ("tags_json", "source_json") and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            sets.append(f"{key}=?")
            params.append(value)
        if sets:
            params.append(asset_id)
            with self.write() as conn:
                conn.execute(f"UPDATE assets SET {', '.join(sets)} WHERE id=?", params)
        return self.get_asset(asset_id)

    def record_usage(self, asset_id: int, task_id: int | None,
                     shot_id: str = "") -> None:
        ts = now_iso()
        with self.write() as conn:
            conn.execute(
                "INSERT INTO asset_usage(asset_id, task_id, shot_id, used_at)"
                " VALUES(?,?,?,?)", (asset_id, task_id, shot_id, ts))
            conn.execute(
                "UPDATE assets SET usage_count=usage_count+1, last_used_at=? WHERE id=?",
                (ts, asset_id))

    def delete_asset(self, asset_id: int) -> bool:
        with self.write() as conn:
            conn.execute("DELETE FROM asset_usage WHERE asset_id=?", (asset_id,))
            cur = conn.execute("DELETE FROM assets WHERE id=?", (asset_id,))
            return cur.rowcount > 0

    # ── attempts ──
    def insert_attempt(self, attempt_id: str, project_id: int, *,
                       task_id: int | None = None, kind: str = "",
                       status: str = "", error: str = "",
                       asset_id: int | None = None, selected: int = 0,
                       provider: str = "", model: str = "", prompt: str = "",
                       params=None, batch_id: str = "", shot_id: str = "",
                       ref_role: str = "", created_at: str | None = None,
                       finished_at: str | None = None) -> dict:
        with self.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO attempts"
                "(id, project_id, task_id, kind, status, error, asset_id, selected,"
                " provider, model, prompt, params_json, batch_id, shot_id, ref_role,"
                " created_at, finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (attempt_id, project_id, task_id, kind, status, error, asset_id,
                 selected, provider, model, prompt,
                 json.dumps(params or {}, ensure_ascii=False), batch_id, shot_id,
                 ref_role, created_at or now_iso(), finished_at))
        return _decode(self.query_one("SELECT * FROM attempts WHERE id=?", (attempt_id,)))

    def list_attempts(self, project_id: int | None = None,
                      task_id: int | None = None, limit: int = 200) -> list[dict]:
        where, params = [], []
        if project_id is not None:
            where.append("project_id=?")
            params.append(project_id)
        if task_id is not None:
            where.append("task_id=?")
            params.append(task_id)
        sql = "SELECT * FROM attempts"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [_decode(r) for r in self.query(sql, params)]

    # ── versions / reviews ──
    def add_version(self, task_id: int, rel_path: str, note: str = "",
                    created_by: str = "") -> dict:
        with self.write() as conn:
            cur = conn.execute(
                "INSERT INTO versions(task_id, rel_path, note, created_by, created_at)"
                " VALUES(?,?,?,?,?)",
                (task_id, rel_path, note, created_by, now_iso()))
            version_id = cur.lastrowid
        return self.query_one("SELECT * FROM versions WHERE id=?", (version_id,))

    def list_versions(self, task_id: int) -> list[dict]:
        return self.query(
            "SELECT * FROM versions WHERE task_id=? ORDER BY id DESC", (task_id,))

    def add_review(self, task_id: int, version_id: int | None, status: str,
                   findings: list | None = None) -> dict:
        with self.write() as conn:
            cur = conn.execute(
                "INSERT INTO reviews(task_id, version_id, status, findings_json, created_at)"
                " VALUES(?,?,?,?,?)",
                (task_id, version_id, status,
                 json.dumps(findings or [], ensure_ascii=False), now_iso()))
            review_id = cur.lastrowid
        return self.query_one("SELECT * FROM reviews WHERE id=?", (review_id,))

    def list_reviews(self, task_id: int) -> list[dict]:
        return [_decode(r) for r in self.query(
            "SELECT * FROM reviews WHERE task_id=? ORDER BY id DESC", (task_id,))]

    # ── desub ──
    def add_desub_report(self, report_id: str, project_id: int | None,
                         source_rel_path: str, result_rel_path: str,
                         risk: str, conflicts: list, report: dict) -> dict:
        with self.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO desub_reports"
                "(id, project_id, source_rel_path, result_rel_path, risk,"
                " conflicts_json, report_json, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (report_id, project_id, source_rel_path, result_rel_path, risk,
                 json.dumps(conflicts or [], ensure_ascii=False),
                 json.dumps(report or {}, ensure_ascii=False), now_iso()))
        return self.get_desub_report(report_id)

    def get_desub_report(self, report_id: str) -> dict | None:
        return _decode(self.query_one(
            "SELECT * FROM desub_reports WHERE id=?", (report_id,)))

    def list_desub_reports(self, project_id: int | None = None,
                           limit: int = 100) -> list[dict]:
        if project_id is None:
            rows = self.query("SELECT * FROM desub_reports ORDER BY created_at DESC LIMIT ?",
                              (limit,))
        else:
            rows = self.query(
                "SELECT * FROM desub_reports WHERE project_id=?"
                " ORDER BY created_at DESC LIMIT ?", (project_id, limit))
        return [_decode(r) for r in rows]
