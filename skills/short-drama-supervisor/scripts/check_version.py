#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查本机装的专家包有没有新版。

有两份东西要对上：
  1. 本机装的版本    → 插件目录 .codebuddy-plugin/plugin.json 里的 version
  2. 项目资产里发布的版本 → 数据目录 updates/版本.json（由主管从项目资产取下来）

远端那份取不到时不报错，只说「没拿到」，不拦别的活。

用法：
    python check_version.py
    python check_version.py --json      # 给程序看的
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import _env
except Exception:  # 兜底：按层级找
    _env = None


PACKAGE_NAME = "short-drama-trailer-editor"


def _candidate_roots() -> list[Path]:
    """专家包可能在哪：数据目录、脚本往上几层、用户级插件目录。

    本机技能是软链到仓库的，所以光看 _env.HOST 会指到数据目录，找不到包；
    装了插件以后又要能从插件目录找到。两处都找一遍最省事。
    """
    seen, out = set(), []

    def add(p: Path) -> None:
        try:
            p = p.resolve()
        except Exception:
            return
        if p.is_dir() and p not in seen:
            seen.add(p)
            out.append(p)

    if _env:
        add(_env.HOST)
    for p in list(Path(__file__).resolve().parents)[:6]:
        add(p)
    home = Path.home()
    for base in (home / ".workbuddy" / "plugins" / "marketplaces",
                 home / "workbuddy" / "plugins" / "marketplaces"):
        if base.is_dir():
            for pj in base.glob("*/plugins/*/.codebuddy-plugin/plugin.json"):
                add(pj.parent.parent)
    # 同名包排前面
    out.sort(key=lambda p: (PACKAGE_NAME not in p.name, len(str(p))))
    return out


def _local_version() -> str:
    """本机装的版本。"""
    for base in _candidate_roots():
        pj = base / ".codebuddy-plugin" / "plugin.json"
        if pj.exists():
            try:
                return str(json.loads(pj.read_text(encoding="utf-8")).get("version") or "").strip()
            except Exception:
                continue
    return ""


def _remote_manifest() -> dict | None:
    """项目资产里最新的版本记录，主管会先把它取到数据目录 updates/ 下。"""
    root = _env.data_root() if _env else Path(__file__).resolve().parents[3]
    for name in ("版本.json", "version.json", "latest.json"):
        f = root / "updates" / name
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


def _vtuple(v: str) -> tuple:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums[:3]) if nums else (0,)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出机器可读的 JSON")
    args = ap.parse_args()

    local = _local_version()
    remote = _remote_manifest()

    if not local:
        msg = "读不到本机版本（插件目录里没找到 plugin.json），跳过检查"
        print(json.dumps({"status": "unknown", "message": msg}, ensure_ascii=False)
              if args.json else msg)
        return 0

    if not remote:
        msg = f"本机 v{local}；没拿到最新版本记录（资产里还没发布过版本清单）"
        print(json.dumps({"status": "no-manifest", "local": local, "message": msg},
                         ensure_ascii=False) if args.json else msg)
        return 0

    latest = str(remote.get("latest") or "").strip()
    newer = _vtuple(latest) > _vtuple(local)

    if args.json:
        print(json.dumps({
            "status": "update-available" if newer else "up-to-date",
            "local": local,
            "latest": latest,
            "released_at": remote.get("released_at"),
            "notes": remote.get("notes") or [],
            "zip": remote.get("zip"),
        }, ensure_ascii=False))
        return 0

    if not newer:
        print(f"已是最新版本 v{local}")
        return 0

    print(f"有新版：本机 v{local} → 最新 v{latest}"
          + (f"（{remote.get('released_at')} 发布）" if remote.get("released_at") else ""))
    notes = remote.get("notes") or []
    if notes:
        print("这次更新了什么：")
        for n in notes:
            print(f"  · {n}")
    if remote.get("zip"):
        print(f"安装包：{remote['zip']}")
    print("要升级的话说一声，我把它装上去。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
