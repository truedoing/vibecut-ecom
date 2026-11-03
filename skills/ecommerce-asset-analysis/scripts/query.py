#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""素材索引查询器：让文案技能按条件取镜头，而不是把整个 asset_index.json 读进上下文。

用法：
  python3 query.py <asset_index.json> --usage texture --size 特写 --min-len 3 --limit 8
  python3 query.py <asset_index.json> --kw 涂抹,开罐
  python3 query.py <asset_index.json> --category 桌拍素材 --list
  python3 query.py <asset_index.json> --catalog <输出目录>   # 生成 catalog.md（每镜头一行）
"""
import argparse, json, os, sys
from pathlib import Path


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def resolve_root(d, override=""):
    """素材根解析优先级：--root > 环境变量 ASSET_ROOT > 索引里的 asset_root。
    这样索引可跨机器共享，各人只需在自己的环境里指到本地素材根。"""
    return (override or os.environ.get("ASSET_ROOT", "")
            or d.get("asset_root") or d.get("source", ""))


def shots_of(d):
    for rel, e in d["videos"].items():
        stem = Path(rel).stem
        for i, s in enumerate(e.get("shots", [])):
            t = s.get("tag", {})
            yield {
                "id": f"{stem}#{i}",
                "file": rel,
                "category": e.get("category", ""),
                "start": s["start"],
                "end": s["end"],
                "dur": round(s["end"] - s["start"], 1),
                "desc": t.get("desc", ""),
                "subject": t.get("subject", ""),
                "size": t.get("shot_size", ""),
                "scene": t.get("scene", ""),
                "text": t.get("text", ""),
                "usage": t.get("usage", []),
            }


def match(s, a):
    if a.usage and not (set(a.usage) & set(s["usage"])):
        return False
    if a.size and s["size"] not in a.size:
        return False
    if a.scene and a.scene not in s["scene"]:
        return False
    if a.category and a.category not in s["category"]:
        return False
    if s["dur"] < a.min_len:
        return False
    if a.kw and not any(k in (s["desc"] + s["subject"] + s["text"]) for k in a.kw):
        return False
    return True


def render(rows, root):
    if not rows:
        print("（无匹配）")
        return
    for s in rows:
        print(f"{s['id']:16s} {s['dur']:5.1f}s [{s['size']}/{s['scene']}] "
              f"{'/'.join(s['usage']):22s} {s['desc']}")
        print(f"{'':16s} → {root}/{s['file']}  取 {s['start']:.0f}-{s['end']:.0f}s")


def catalog(d, out_dir, root=""):
    from collections import Counter
    root = root or resolve_root(d)
    lines = [f"# {d.get('label','素材')} 素材目录（按用途分组，每镜头一行）", "",
             f"> 共 {sum(len(e.get('shots', [])) for e in d['videos'].values())} 镜头 / "
             f"{len(d['videos'])} 个文件 · 素材根目录 `{root}` · 机读版 asset_index.json", ""]
    rows = list(shots_of(d))
    groups = {}
    for s in rows:
        groups.setdefault(s["usage"][0] if s["usage"] else "other", []).append(s)
    for u in ["product", "texture", "hand", "process", "origin", "other"]:
        if u not in groups:
            continue
        lines += [f"## {u}（{len(groups[u])} 个）", "",
                  "| shot_id | 时长 | 景别/场景 | 描述 | 取用 |", "|---|---|---|---|---|"]
        for s in groups[u]:
            desc = s["desc"].replace("|", "｜")
            lines.append(f"| {s['id']} | {s['dur']:.1f}s | {s['size']}/{s['scene']} | {desc} | "
                         f"{s['file']} {s['start']:.0f}-{s['end']:.0f}s |")
        lines.append("")
    p = Path(out_dir) / "catalog.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已生成 {p}（{len(rows)} 镜头）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("index")
    ap.add_argument("--usage", default="", help="逗号分隔，任一命中")
    ap.add_argument("--size", default="", help="逗号分隔，如 特写,近景")
    ap.add_argument("--scene", default="")
    ap.add_argument("--category", default="")
    ap.add_argument("--kw", default="", help="逗号分隔关键词，命中 desc/subject/text")
    ap.add_argument("--min-len", type=float, default=0.0)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--list", action="store_true", help="只列文件不筛镜头")
    ap.add_argument("--catalog", default="", help="生成 catalog.md 到指定目录")
    ap.add_argument("--root", default="",
                    help="素材根目录（不传则读环境变量 ASSET_ROOT，再退到索引里的 asset_root）")
    a = ap.parse_args()
    d = load(a.index)
    root = resolve_root(d, a.root)
    if a.catalog:
        catalog(d, a.catalog, root)
        return
    if a.list:
        for rel, e in sorted(d["videos"].items()):
            print(f"{Path(rel).stem:12s} {e['manifest']['duration']:6.1f}s "
                  f"{len(e.get('shots', []))}镜头  {e['category']}")
        return
    a.usage = [x for x in a.usage.split(",") if x]
    a.size = [x for x in a.size.split(",") if x]
    a.kw = [x for x in a.kw.split(",") if x]
    rows = [s for s in shots_of(d) if match(s, a)]
    rows.sort(key=lambda s: -s["dur"])
    render(rows[:a.limit], root)
    print(f"\n命中 {len(rows)} 个，显示前 {min(len(rows), a.limit)} 个")


if __name__ == "__main__":
    main()
