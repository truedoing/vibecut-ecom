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


def banned_hits(text, ban):
    """按句判断禁词：句中含禁词且没有否定词才算违规（"不要…的抑扬顿挫"是合法写法）。"""
    import re
    hits = []
    for seg in re.split(r"[，。；、,.;]", text or ""):
        if any(w in seg for w in ban) and not any(n in seg for n in ("不要", "别", "不是", "禁")):
            hits += [w for w in ban if w in seg]
    return hits


def image_size(path):
    """用 ffprobe 取宽高；取不到返回 (0, 0) 不阻塞校验。"""
    import json as _json, subprocess
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=15).stdout
        s = _json.loads(out)["streams"][0]
        return int(s["width"]), int(s["height"])
    except Exception:
        return 0, 0


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
        LOOK_KEYS = ("action", "wardrobe_wear", "grooming", "skin", "expression", "sight")
        VOICE_BAN = ("吐字清晰", "字正腔圆", "抑扬顿挫", "亲切热情", "从容笃定", "播音强调", "播音腔强调")
        ENV_BAN = ("现代简约", "样板间", "北欧风", "侘寂")
        for c in load_cards():
            if c["id"] in seen:
                errs.append(f"{c['id']} 重复")
            seen.add(c["id"])
            d = LIB / "characters" / c["_dir"]
            for f in portrait_files(c):
                p = d / f
                if not p.exists():
                    errs.append(f"{c['id']} 缺定妆图 {f}")
                else:
                    w, h = image_size(p)
                    if w and h and w >= h:
                        errs.append(f"{c['id']} {f} 不是竖版（{w}x{h}；竖版才够放双联）")
            vs = c.get("voice", {}).get("sample", "")
            if vs and not (d / vs).exists():
                errs.append(f"{c['id']} voice.sample={vs} 不存在")
            for k in ("id", "name", "age_feel", "region", "temperament", "wardrobe"):
                if not c.get(k):
                    errs.append(f"{c['id']} card.json 缺 {k}")
            desc = c.get("voice", {}).get("desc", "")
            if not desc:
                errs.append(f"{c['id']} voice.desc 未填（换平台重建的根，必须写）")
            else:
                hit = banned_hits(desc, VOICE_BAN)
                if hit:
                    errs.append(f"{c['id']} voice.desc 含播音腔词 {hit}（会生成播音员，改生活化写法）")
            look = c.get("look") or {}
            miss = [k for k in LOOK_KEYS if not look.get(k)]
            if miss:
                errs.append(f"{c['id']} look 缺 {miss}（六维缺项会退化成摆拍/显老）")
            if not c.get("home_env"):
                errs.append(f"{c['id']} 缺 home_env（定妆照的场景与光线取自环境卡）")
        for c in load_cards("environments"):
            if not c.get("light"):
                errs.append(f"{c['id']} 环境卡缺 light（混剪对齐要用）")
            if not c.get("place"):
                errs.append(f"{c['id']} 环境卡缺 place（定妆照背景要用）")
            place = c.get("place", "")
            hit = banned_hits(place, ENV_BAN)
            if hit:
                errs.append(f"{c['id']} place 含 {hit}（容易出样板间，改'住了几年的普通家庭'）")
            if not c.get("props"):
                errs.append(f"{c['id']} 环境卡缺 props（生活痕迹清单，决定有没有烟火气）")
        print("OK" if not errs else "\n".join(errs))
        sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
