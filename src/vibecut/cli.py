"""vibecut 命令行：Agent 与人都走这一套接口。

默认输出 JSON（便于 Agent 解析）；加 --human 输出表格。
除 init/serve 外，所有命令都通过 HTTP 调用本地服务。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

from .config import load_settings
from .core.db import Database

TIMEOUT = 3600


# ── 输出 ──
def _emit(obj, human: bool = False, kind: str = "") -> None:
    if human and kind == "assets" and isinstance(obj, dict):
        rows = obj.get("assets") or []
        print(f"{'ID':>5}  {'类型':<6} {'层':<3} {'项目':<8} 备注/标签")
        for a in rows:
            tags = ",".join(a.get("tags") or [])
            note = (a.get("note") or "").replace("\n", " ")[:32]
            print(f"{a['id']:>5}  {a.get('kind',''):<6} {a.get('layer',''):<3} "
                  f"{(a.get('project') or ''):<8} {note} {tags}")
        print(f"共 {len(rows)} 条")
        return
    if human and kind == "tasks" and isinstance(obj, dict):
        rows = obj.get("tasks") or []
        print(f"{'ID':>5}  {'项目':<8} {'任务':<16} {'状态':<6} 最后编辑")
        for t in rows:
            print(f"{t['id']:>5}  {(t.get('project') or ''):<8} {t['name']:<16} "
                  f"{t['status']:<6} {t.get('updated_by','')} {t.get('updated_at','')}")
        print(f"共 {len(rows)} 条")
        return
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _fail(message: str, hint: str = "", code: int = 1) -> int:
    print(json.dumps({"ok": False, "error": message, "hint": hint},
                     ensure_ascii=False, indent=2))
    return code


# ── HTTP ──
class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def _handle(self, resp: requests.Response) -> dict:
        try:
            data = resp.json()
        except Exception:
            data = {"ok": resp.ok, "text": resp.text[:2000]}
        if not resp.ok:
            data.setdefault("ok", False)
            data.setdefault("http_status", resp.status_code)
            data.setdefault("error", data.get("detail") or "请求失败")
        return data

    def get(self, path: str, **params) -> dict:
        return self._handle(requests.get(self.base + path, params=params, timeout=TIMEOUT))

    def post(self, path: str, payload: dict) -> dict:
        return self._handle(requests.post(self.base + path, json=payload, timeout=TIMEOUT))

    def patch(self, path: str, payload: dict) -> dict:
        return self._handle(requests.patch(self.base + path, json=payload, timeout=TIMEOUT))


# ── 命令实现 ──
def cmd_init(args) -> int:
    settings = load_settings(args.data_root)
    Database(settings).init()
    settings.data_root.mkdir(parents=True, exist_ok=True)
    _emit({"ok": True, "data_root": str(settings.data_root),
           "db": str(settings.db_path),
           "next": "vibecut serve"})
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    from .server.app import create_app

    settings = load_settings(args.data_root)
    Database(settings).init()
    host = args.host or settings.host
    port = args.port or settings.port
    app = create_app(settings)
    print(f"[vibecut] http://{host}:{port}  data_root={settings.data_root}")
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def cmd_project(args, client: Client) -> int:
    if args.action == "list":
        return _emit_or_fail(client.get("/api/projects"), args)
    if args.action == "add":
        return _emit_or_fail(client.post("/api/projects", {
            "name": args.name, "media_root": args.media_root or ""}), args)
    return _fail("未知 project 子命令")


def cmd_assets(args, client: Client) -> int:
    if args.action == "ingest":
        payload = {
            "project": args.project, "path": args.file,
            "tags": args.tags, "layer": args.layer, "grade": args.grade,
            "tier": args.tier, "note": args.note, "origin": args.origin,
            "copy": not args.no_copy,
        }
        return _emit_or_fail(client.post("/api/assets/import", payload), args, "assets")
    if args.action == "search":
        params = {"project": args.project, "query": args.query,
                  "tags": args.tags, "layer": args.layer, "kind": args.kind,
                  "scope": args.scope, "limit": args.limit}
        return _emit_or_fail(client.get("/api/assets", **params), args, "assets")
    if args.action == "list":
        return _emit_or_fail(client.get(
            "/api/assets", project=args.project, scope=args.scope,
            limit=args.limit), args, "assets")
    if args.action == "gap":
        requirements = None
        if args.requirements:
            requirements = json.loads(Path(args.requirements).read_text(encoding="utf-8"))
        return _emit_or_fail(client.post("/api/assets/gap", {
            "project": args.project, "task": args.task,
            "requirements": requirements}), args)
    return _fail("未知 assets 子命令")


def cmd_desub(args, client: Client) -> int:
    if not args.file and not (args.project and args.ep):
        return _fail("需要 --file，或 --project + --ep + --start + --end")
    payload = {
        "project": args.project, "source": args.file, "ep": args.ep,
        "start": args.start, "end": args.end,
        "sample_every": args.sample_every, "mask_pad": args.mask_pad,
        "backend": args.backend, "det_only": args.det_only,
    }
    path = "/api/desub/check" if args.action == "check" else "/api/desub/run"
    return _emit_or_fail(client.post(path, payload), args)


def cmd_task(args, client: Client) -> int:
    if args.action == "list":
        return _emit_or_fail(client.get(
            "/api/tasks", project=args.project, status=args.status), args, "tasks")
    if args.action == "show":
        return _emit_or_fail(client.get(f"/api/tasks/{args.project}/{args.name}"), args)
    if args.action == "new":
        return _emit_or_fail(client.post("/api/tasks", {
            "project": args.project, "name": args.name, "brief": args.brief}), args)
    if args.action == "status":
        return _emit_or_fail(client.patch(f"/api/tasks/{args.project}/{args.name}", {
            "status": args.status, "by": args.by}), args)
    return _fail("未知 task 子命令")


def _emit_or_fail(data: dict, args, kind: str = "") -> int:
    _emit(data, human=args.human, kind=kind)
    return 0 if data.get("ok") else 1


# ── argparse ──
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vibecut", description="VibeCut 电商素材台")
    p.add_argument("--data-root", help="数据根目录（默认 ~/VibeCutEcomData）")
    p.add_argument("--api", help="服务地址（默认 http://127.0.0.1:8766）")
    p.add_argument("--human", action="store_true", help="输出人读表格")
    p.add_argument("--version", action="version", version="vibecut 0.1.0")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="初始化数据目录与数据库").set_defaults(func=cmd_init)
    serve = sub.add_parser("serve", help="启动本地服务")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.set_defaults(func=cmd_serve)

    project = sub.add_parser("project", help="项目管理")
    project_sub = project.add_subparsers(dest="action", required=True)
    pa = project_sub.add_parser("add")
    pa.add_argument("name")
    pa.add_argument("--media-root", default="")
    project_sub.add_parser("list")
    project.set_defaults(func=cmd_project)

    assets = sub.add_parser("assets", help="素材库")
    a = assets.add_subparsers(dest="action", required=True)
    ai = a.add_parser("ingest")
    ai.add_argument("--project", required=True)
    ai.add_argument("--file", required=True)
    ai.add_argument("--tags", default="")
    ai.add_argument("--layer", default="")
    ai.add_argument("--grade", default="")
    ai.add_argument("--tier", default="")
    ai.add_argument("--note", default="")
    ai.add_argument("--origin", default="upload")
    ai.add_argument("--no-copy", action="store_true", help="原地登记，不复制文件")

    asr = a.add_parser("search")
    asr.add_argument("--project")
    asr.add_argument("--query", default="")
    asr.add_argument("--tags", default="")
    asr.add_argument("--layer")
    asr.add_argument("--kind")
    asr.add_argument("--scope", default="project", choices=["project", "all"])
    asr.add_argument("--limit", type=int, default=200)

    al = a.add_parser("list")
    al.add_argument("--project", required=True)
    al.add_argument("--scope", default="project", choices=["project", "all"])
    al.add_argument("--limit", type=int, default=500)

    ag = a.add_parser("gap")
    ag.add_argument("--project", required=True)
    ag.add_argument("--task", required=True)
    ag.add_argument("--requirements", help="WorkBuddy 给出的素材需求 JSON")
    assets.set_defaults(func=cmd_assets)

    desub = sub.add_parser("desub", help="去字幕")
    d = desub.add_subparsers(dest="action", required=True)
    for name, help_text in (("check", "只判风险，不修复"), ("run", "执行修复")):
        dp = d.add_parser(name, help=help_text)
        dp.add_argument("--project")
        dp.add_argument("--file")
        dp.add_argument("--ep", type=int)
        dp.add_argument("--start", type=float, default=0.0)
        dp.add_argument("--end", type=float, default=0.0)
        dp.add_argument("--sample-every", type=int, default=6)
        dp.add_argument("--mask-pad", type=int, default=4)
        dp.add_argument("--backend", default="auto",
                        choices=["auto", "onnx", "lama", "cv2"])
        dp.add_argument("--det-only", action="store_true")
        if name == "check":
            # 风险检查可以抽得更稀，优先速度
            dp.set_defaults(sample_every=10)
    desub.set_defaults(func=cmd_desub)

    task = sub.add_parser("task", help="任务台账")
    t = task.add_subparsers(dest="action", required=True)
    tl = t.add_parser("list")
    tl.add_argument("--project")
    tl.add_argument("--status")
    ts = t.add_parser("show")
    ts.add_argument("--project", required=True)
    ts.add_argument("--name", required=True)
    tn = t.add_parser("new")
    tn.add_argument("--project", required=True)
    tn.add_argument("--name", required=True)
    tn.add_argument("--brief", default="")
    tst = t.add_parser("status")
    tst.add_argument("--project", required=True)
    tst.add_argument("--name", required=True)
    tst.add_argument("--status", required=True)
    tst.add_argument("--by", default="")
    task.set_defaults(func=cmd_task)

    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 允许 --human 出现在任意位置
    human = "--human" in argv
    if human:
        argv = [a for a in argv if a != "--human"]
    args = build_parser().parse_args(argv)
    args.human = human or getattr(args, "human", False)
    settings = load_settings(args.data_root)

    if args.command in ("init", "serve"):
        return args.func(args)

    client = Client(args.api or settings.api_base)
    try:
        return args.func(args, client)
    except requests.ConnectionError:
        return _fail("连接不上 VibeCut 服务",
                     f"先在另一个终端运行: vibecut serve --host {settings.host} --port {settings.port}")
    except requests.Timeout:
        return _fail("请求超时", "去字幕等长任务可能需要更久，检查服务日志")
    except FileNotFoundError as e:
        return _fail(str(e))


if __name__ == "__main__":
    sys.exit(main())
