#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 资产角色库：查询 / 校验 / 编号分配。

用法（在仓库根执行）：
  python3 skills/ecommerce-ai-assets/scripts/registry.py list
  python3 skills/ecommerce-ai-assets/scripts/registry.py query --audience 中老年 --gender 女
  python3 skills/ecommerce-ai-assets/scripts/registry.py show ch_001
  python3 skills/ecommerce-ai-assets/scripts/registry.py validate
  python3 skills/ecommerce-ai-assets/scripts/registry.py next-id
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "assets" / "ai_assets"


def load_cards(kind="characters"):
    out = []
    d = LIB / kind
    if not d.exists():
        return out
    for p in sorted(d.iterdir()):
        f = p / "card.json"
        if f.exists():
            c = json.loads(f.read_text(encoding="utf-8"))
            c["_dir"] = p.name
            out.append(c)
    return out


def portrait_files(c):
    """卡里 portraits 声明的图；没声明则默认 portrait.jpg（单图双联）。"""
    p = c.get("portraits", {})
    return list(p.values()) if p else ["portrait.jpg"]


def show_card(c):
    v = c.get("voice", {})
    print(f"{c['id']} {c['name']}  {c.get('gender','')}/{c.get('age_feel','')}  "
          f"{c.get('region','')} · {c.get('role_type','')}")
    print(f"  气质: {c.get('temperament','')} | 服装: {c.get('wardrobe','')}")
    print(f"  人群: {'/'.join(c.get('target_audience', []))}")
    print(f"  音色: {v.get('desc','')}"
          + (f" [{v.get('platform','')}:{v.get('voice_id','')}]" if v.get("voice_id") else " [未绑定]")
          + (" [有锚样本]" if (LIB / "characters" / c["_dir"] / v.get("sample", "voice.mp3")).exists() else ""))
    missing = [f for f in portrait_files(c) if not (LIB / "characters" / c["_dir"] / f).exists()]
    if missing:
        print(f"  ⚠ 缺图: {missing}")
    if c.get("used_in"):
        print(f"  用过: {'; '.join(c['used_in'][-3:])}")


def age_ok(feel, lo, hi):
    import re
    m = re.findall(r"\d+", feel or "")
    if len(m) < 2:
        return True
    a, b = int(m[0]), int(m[1])
    return a <= hi and b >= lo


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    q = sub.add_parser("query")
    q.add_argument("--audience", default="")
    q.add_argument("--gender", default="")
    q.add_argument("--min-age", type=int, default=0)
    q.add_argument("--max-age", type=int, default=200)
    q.add_argument("--kw", default="", help="命中气质/服装/角色类型")
    s = sub.add_parser("show"); s.add_argument("id")
    sub.add_parser("validate")
    sub.add_parser("next-id")
    a = ap.parse_args()

    if a.cmd == "list":
        cards = load_cards()
        if not cards:
            print("（库为空）")
            return
        for c in cards:
            show_card(c)
            print()
    elif a.cmd == "query":
        for c in load_cards():
            if a.audience and a.audience not in "".join(c.get("target_audience", [])):
                continue
            if a.gender and a.gender != c.get("gender"):
                continue
            if not age_ok(c.get("age_feel"), a.min_age, a.max_age):
                continue
            blob = c.get("temperament", "") + c.get("wardrobe", "") + c.get("role_type", "")
            if a.kw and a.kw not in blob:
                continue
            show_card(c)
            print()
    elif a.cmd == "show":
        for c in load_cards():
            if c["id"] == a.id:
                show_card(c)
                return
        sys.exit(f"找不到 {a.id}")
    elif a.cmd == "next-id":
        import re
        used = [0]
        for p in (LIB / "characters").rglob("*"):
            m = re.match(r"ch_(\d+)", p.name)
            if m:
                used.append(int(m.group(1)))
        print(f"ch_{max(used) + 1:03d}")
    elif a.cmd == "validate":
        errs = []
        seen = set()
        for c in load_cards():
            if c["id"] in seen:
                errs.append(f"{c['id']} 重复")
            seen.add(c["id"])
            d = LIB / "characters" / c["_dir"]
            for f in portrait_files(c):
                if not (d / f).exists():
                    errs.append(f"{c['id']} 缺定妆图 {f}")
            vs = c.get("voice", {}).get("sample", "")
            if vs and not (d / vs).exists():
                errs.append(f"{c['id']} voice.sample={vs} 不存在")
            for k in ("id", "name", "age_feel", "region", "temperament", "wardrobe"):
                if not c.get(k):
                    errs.append(f"{c['id']} card.json 缺 {k}")
            if not c.get("voice", {}).get("desc"):
                errs.append(f"{c['id']} voice.desc 未填（换平台重建的根，必须写）")
        for c in load_cards("environments"):
            if not c.get("light"):
                errs.append(f"{c['id']} 环境卡缺 light（混剪对齐要用）")
        print("OK" if not errs else "\n".join(errs))
        sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
