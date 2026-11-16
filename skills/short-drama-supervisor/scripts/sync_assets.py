#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分析完的产物要能给别人用：列「该传什么、传给谁、齐不齐」。

为什么单独一个脚本
------------------
11-16 张老师发现：剧跑完了，项目资产里 `剧集包/` 和 `素材索引/` 还是空的。
产物只躺在本机，换台机器就得重新分析 17 集原片——那是最贵的一段。
所以「传」不能靠记得，得有个东西把清单算出来，缺哪一件当场说。

它**不上传**，只算清单。传的动作由主管（我）拿这份清单去调网盘工具，
因为上传前必须张老师点头——项目资产是团队共享的，删错了别人也受影响。

什么算齐
--------
- 剧集包：`drama-pack.json` 在、校验过（gatecheck 文件在）；
- 素材索引：**每集** 的 lines / shots / sentences 都在。缺一类就标「不齐」，
  并写清缺到第几集——不齐的东西传上去，下一个人拿到也用不了。

什么都不传
----------
原片 mp4、`edge_frames/`（几百兆大图，中间产物）、`ai_assets`、build 目录、
`.env`。这些规则见专家说明「项目资产同步」一节，别自己加。

用法
----
  python3 sync_assets.py --drama 以真心守护至亲      # 一部剧的清单
  python3 sync_assets.py --all                      # 所有剧，便于一次看全
  python3 sync_assets.py --drama 以真心守护至亲 --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import data_root  # noqa: E402

ROOT = data_root()
PACKS = ROOT / "packs" / "short-drama"
ASSETS = ROOT / "assets" / "short-drama"

# 索引里真该传的东西。**不含 edge_frames**：那是几百兆的抽帧图，属于中间产物。
INDEX_ITEMS = ["lines", "shots", "sentences", "cuts"]
INDEX_FILES = ["segments_skeleton.json", "char_map.json", "ep_durations.json", "index.sqlite"]
PACK_FILES = ["drama-pack.json", "drama-pack.gatecheck.json", "剧集包说明.md"]

SKIP_DIRS = {"edge_frames", "ai_assets", "__pycache__", "build"}


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _human(n: int) -> str:
    if n >= 1048576:
        return f"{n/1048576:.1f} MB"
    if n >= 1024:
        return f"{n/1024:.0f} KB"
    return f"{n} B"


def _eps_of(folder: Path, pattern: str) -> list[int]:
    """这个目录里有哪些集。

    只认文件名**完全长这样**的（`shots_ep01.json`），不认 `shots_ep01.v3.json`
    这种旧版本副本，也不认 `_v4_backup` 里的东西——传上去别人会不知道用哪份。
    """
    out = []
    for p in folder.glob("*.json"):
        m = re.fullmatch(pattern, p.name)
        if m:
            out.append(int(m.group(1)))
    return sorted(set(out))


def pack_plan(drama: str) -> dict:
    """剧集包该传哪些文件。"""
    d = PACKS / drama
    files = []
    for name in PACK_FILES:
        p = d / name
        if p.exists():
            files.append({"name": name, "bytes": p.stat().st_size, "path": str(p)})
    has = {f["name"] for f in files}
    notes = []
    if "drama-pack.json" not in has:
        notes.append("剧集包还没建")
    elif "drama-pack.gatecheck.json" not in has:
        notes.append("没跑过校验（没有 gatecheck 文件），传之前先 validate_pack.py")
    if "剧集包说明.md" not in has:
        notes.append("缺「剧集包说明.md」：别人拿到 JSON 不知道字段怎么读")
    return {
        "target": f"剧集包/{drama}/",
        "files": files,
        "ready": "drama-pack.json" in has and "drama-pack.gatecheck.json" in has,
        "notes": notes,
    }


def index_plan(drama: str) -> dict:
    """素材索引该传哪些、齐不齐。"""
    d = ASSETS / drama
    groups = []
    notes = []

    per = {}
    for item in INDEX_ITEMS:
        folder = d / item
        if not folder.is_dir():
            per[item] = []
            continue
        pattern = {"lines": r"lines_ep(\d+)\.json", "shots": r"shots_ep(\d+)\.json",
                   "sentences": r"sentences_ep(\d+)\.json", "cuts": r"ep(\d+)\.json"}[item]
        per[item] = _eps_of(folder, pattern)
        groups.append({
            "dir": item, "eps": per[item], "count": len(per[item]),
            "bytes": _size(folder), "path": str(folder),
        })

    singles = []
    for name in INDEX_FILES:
        p = d / name
        if p.exists():
            singles.append({"name": name, "bytes": p.stat().st_size, "path": str(p)})

    # 齐不齐：以「有几句台词」的那几集为准，三样都得有
    base = set(per.get("lines") or [])
    miss = {}
    for item in ("shots", "sentences"):
        lack = sorted(base - set(per.get(item) or []))
        if lack:
            miss[item] = lack
    if not base:
        notes.append("一集台词都还没有，索引没东西可传")
    elif miss:
        parts = [f"{k} 缺 {len(v)} 集（{'、'.join('ep%02d' % e for e in v[:6])}"
                 f"{'…' if len(v) > 6 else ''}）" for k, v in miss.items()]
        notes.append("；".join(parts) + "——别人拿到会缺「谁在说」这块")
    if not (per.get("shots") or []):
        notes.append("没有镜头打标（shots）：要么没切镜头，要么画面打标关着")

    return {
        "target": f"素材索引/{drama}/",
        "dirs": groups,
        "files": singles,
        "ready": bool(base) and not miss,
        "notes": notes,
    }


def one(drama: str) -> dict:
    pk = pack_plan(drama)
    ix = index_plan(drama)
    total = sum(f["bytes"] for f in pk["files"]) \
        + sum(g["bytes"] for g in ix["dirs"]) \
        + sum(f["bytes"] for f in ix["files"])
    return {"drama": drama, "pack": pk, "index": ix, "bytes": total}


def all_dramas() -> list[str]:
    names = set()
    for base in (PACKS, ASSETS):
        if base.is_dir():
            names |= {p.name for p in base.iterdir()
                      if p.is_dir() and p.name not in SKIP_DIRS}
    return sorted(names)


def show(r: dict) -> None:
    print(f"\n=== {r['drama']}  （合计 {_human(r['bytes'])}）")
    pk, ix = r["pack"], r["index"]
    print(f"  剧集包 → {pk['target']}  {'✅ 可传' if pk['ready'] else '⚠ 还不能传'}")
    for f in pk["files"]:
        print(f"      {f['name']:<28} {_human(f['bytes'])}")
    for n in pk["notes"]:
        print(f"      · {n}")

    print(f"  素材索引 → {ix['target']}  {'✅ 齐' if ix['ready'] else '⚠ 不齐'}")
    for g in ix["dirs"]:
        eps = g["eps"]
        span = f"ep{eps[0]:02d}–ep{eps[-1]:02d}" if eps else "空"
        print(f"      {g['dir'] + '/':<28} {g['count']:>3} 个  {span:<16} {_human(g['bytes'])}")
    for f in ix["files"]:
        print(f"      {f['name']:<28} {_human(f['bytes'])}")
    for n in ix["notes"]:
        print(f"      · {n}")


def main() -> int:
    ap = argparse.ArgumentParser(description="算出该传哪些分析产物到项目资产")
    ap.add_argument("--drama", help="剧名")
    ap.add_argument("--all", action="store_true", help="所有剧")
    ap.add_argument("--json", action="store_true", help="机器读")
    a = ap.parse_args()

    if not a.drama and not a.all:
        ap.print_help()
        return 2
    names = all_dramas() if a.all else [a.drama]
    res = [one(n) for n in names]

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    print("项目资产同步清单（这份清单只算不传；传之前要人点头）")
    print("不传：原片 mp4 / edge_frames 抽帧图 / ai_assets / build / .env")
    for r in res:
        show(r)
    ready = [r["drama"] for r in res if r["pack"]["ready"] or r["index"]["ready"]]
    print("\n可以传的：" + ("、".join(ready) if ready else "还没有"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
