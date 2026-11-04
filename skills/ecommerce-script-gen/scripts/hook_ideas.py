#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hook_ideas.py — 钩子创意「供给器」，不是「生成器」。

只做三件机器能做的事：
  1. 从品类包推导 4 个拒绝理由槽位（不遗漏）
  2. 从 hook-patterns.md 读 12 种冲突原型 + 强弱 + 适配的拒绝理由
  3. 扫已有脚本的 hook_pattern 查重（不重复）

把「候选池骨架」打印出来，交给 Agent 填台词、写四拍、打五维分。
**不写创意文案**：技能管不遗漏、不重复；出彩是编剧的活。

用法：
  python3 scripts/hook_ideas.py --pack packs/虎坚果酱/category-pack.json --scripts-dir .
  python3 scripts/hook_ideas.py ... --json     # 结构化输出，给 Agent 消费
  python3 scripts/hook_ideas.py ... --top 8    # 多列几个可用格子
"""
import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATTERNS_MD = HERE.parent / "references" / "hook-patterns.md"

# 拒绝理由的四个固定槽位。槽位是通用的（买东西被人嫌弃，永远逃不出这四类）；
# 原料从品类包里抽，抽不到就留空让 Agent 现场补一句口语，绝不凭空编产品事实。
SLOTS = [
    ("浪费", ("吃不完", "剩", "开封", "放坏", "过期"), "已经买过 / 用不完，还买？"),
    ("陌生", ("没听说过", "没听过", "不认识", "啥玩意", "第一次"), "名字 / 原料没听过，不敢尝"),
    ("贵",   ("贵", "抢钱", "五十", "价格", "块"),     "单价看着不值"),
    ("失望", ("没味道", "干巴", "不好吃", "能有啥", "不就是"), "吃过亏，觉得也就那样"),
]

# 反转词 → 原型，用于历史脚本没写 hook_pattern 时的推断（推断值会明确标注）
INFER = [("还真香", "真香"), ("真香", "真香"), ("别乱花钱", "代际"), ("算下来", "算账")]


def canon(name):
    """原型名归一：文档写「真香型」，历史字段常写「真香」，查重时必须对齐。"""
    return re.sub(r"型$", "", str(name).strip())


def find_pack(arg):
    if arg:
        p = Path(arg)
        if not p.exists():
            sys.exit(f"✗ 品类包不存在：{p}")
        return p
    cands = []
    for base in (Path.cwd(), HERE.parent.parent.parent):
        cands += sorted(glob.glob(str(base / "packs" / "*" / "category-pack.json")))
    if not cands:
        sys.exit("✗ 没找到品类包，请给 --pack packs/{品类}/category-pack.json")
    if len(cands) > 1:
        print("⚠️ 找到多个品类包，默认用第一个；要指定请给 --pack\n    " + "\n    ".join(cands))
    return Path(cands[0])


def rejection_slots(pack):
    """从品类包抽原料填槽位：pains / price_talk / product_facts / elements.*"""
    el = pack.get("elements", {}) or {}
    pool = []
    for key in ("pains", "hooks", "golden_quotes", "ctas", "price_talk"):
        for row in el.get(key, []) or []:
            if isinstance(row, dict):
                pool.append(" ".join(str(v) for v in row.values() if isinstance(v, str)))
    for row in pack.get("price_talk", []) or []:
        if isinstance(row, dict):
            pool.append(" ".join(str(v) for v in row.values() if isinstance(v, str)))
    for row in pack.get("product_facts", []) or []:
        if isinstance(row, dict):
            pool.append(" ".join(str(v) for v in row.values() if isinstance(v, str)))
    # 价格槽位优先给真实价格口径，别拿「一瓶几百的」这种竞品痛点当本品单价
    price = [r for r in (el.get("price_talk") or []) if isinstance(r, dict)]
    if price:
        pool.insert(0, "本品价格 " + "；".join(
            " ".join(str(v) for v in r.values() if isinstance(v, str)) for r in price))
    blob = " ".join(pool)
    out = []
    for name, keys, hint in SLOTS:
        hit = ""
        for line in pool:
            if any(k in line for k in keys):
                hit = line[:70]
                break
        out.append({"slot": name, "raw": hit or "（包里没有对应原料，现场补一句真人会说的话）", "hint": hint})
    return out, blob


def read_patterns():
    """单一真源在 hook-patterns.md；解析失败就报错，不静默降级。"""
    if not PATTERNS_MD.exists():
        sys.exit(f"✗ 缺原型库：{PATTERNS_MD}")
    text = PATTERNS_MD.read_text(encoding="utf-8")
    rows, tier = [], ""
    for line in text.splitlines():
        m = re.match(r"^###\s*(强|中|慎)", line)
        if m:
            tier = m.group(1)
            continue
        m = re.match(r"^\*\*(\d+)\.\s*(.+?型)\*\*", line)
        if m:
            rows.append({"id": int(m.group(1)), "name": m.group(2), "tier": tier or "?", "fit": []})
            continue
        m = re.match(r"^>\s*适合[：:]\s*(.+)", line)
        if m and rows:
            rows[-1]["fit"] = [x.strip() for x in re.split(r"[、,，/]", m.group(1)) if x.strip()]
    if len(rows) < 10:
        sys.exit(f"✗ 原型库解析异常，只读到 {len(rows)} 种（应 12 种），检查 {PATTERNS_MD.name} 的标题格式")
    return rows


def scan_used(scripts_dir):
    """扫已有文案 JSON 的 hook_pattern；缺字段就按反转词推断并标注。"""
    used, detail = {}, []
    base = Path(scripts_dir)
    for f in sorted(base.glob("*.json")):
        if "segs" in f.name or "bounds" in f.name or "asset_index" in f.name:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        segs = d.get("segments") or []
        if not segs:
            continue
        hook = segs[0] or {}
        pat = hook.get("hook_pattern")
        inferred = False
        if not pat:
            # 钩子段可能把台词放在 dialogue 数组里；文件名也是强信号（历史脚本常叫 *_真香.json）
            blob = (json.dumps(hook, ensure_ascii=False) + " " +
                    json.dumps((d.get("ai_shots") or [{}])[0], ensure_ascii=False) + " " + f.name)
            for word, name in INFER:
                if word in blob:
                    pat, inferred = name, True
                    break
        if not pat:
            continue
        for nm in [x.strip() for x in re.split(r"[、,，/]", str(pat)) if x.strip()]:
            key = canon(nm)
            used[key] = used.get(key, 0) + 1
        detail.append({"file": f.name, "pattern": pat, "inferred": inferred,
                       "reason": hook.get("hook_reject_reason", "")})
    return used, detail


def main():
    ap = argparse.ArgumentParser(description="钩子创意供给器：列槽位 / 查重 / 推荐没用过的格子")
    ap.add_argument("--pack", default="", help="category-pack.json 路径")
    ap.add_argument("--scripts-dir", default=".", help="已有文案 JSON 所在目录（查重用）")
    ap.add_argument("--top", type=int, default=6, help="推荐几个可用格子")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    pack_path = find_pack(a.pack)
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    slots, _ = rejection_slots(pack)
    patterns = read_patterns()
    used, detail = scan_used(a.scripts_dir)

    # 候选池：原型 × 适配槽位；按「没用过 > 强 > 慎」排序
    cells = []
    for p in patterns:
        short = canon(p["name"])
        n = used.get(short, 0)
        if n >= 3:
            continue                # 连用 ≥3 条，本轮直接停用，不给它排名次
        for slot in (p["fit"] or [s["slot"] for s in slots]):
            if slot not in [s["slot"] for s in slots]:
                continue
            score = {"强": 5, "中": 3, "慎": 1}.get(p["tier"], 2) - n * 4
            if n == 0:
                score += 3
            if short == "悬念":
                score -= 6          # 中老年品类基本禁用
            if short == "算账":
                score -= 3          # 钩子段不念数值，算账型通常归混剪
            cells.append({"pattern": p["name"], "tier": p["tier"], "slot": slot,
                          "used": n, "score": score})
    cells.sort(key=lambda x: (-x["score"], -{"强": 3, "中": 2, "慎": 1}.get(x["tier"], 0), x["pattern"]))
    rec = cells[:a.top]

    if a.json:
        print(json.dumps({"pack": str(pack_path), "slots": slots,
                          "used": used, "detail": detail, "recommend": rec},
                         ensure_ascii=False, indent=2))
        return

    print(f"品类包  {pack_path}")
    print(f"文案目录 {Path(a.scripts_dir).resolve()}   扫描到 {len(detail)} 条脚本\n")

    print("【拒绝理由 · 四个槽位】槽位固定，原料从包里抽；口语化由 Agent 填")
    for s in slots:
        print(f"  {s['slot']:<3} ← {s['raw']}")
        print(f"       该找的话：{s['hint']}")

    print("\n【已用格子】")
    if not used:
        print("  （无记录）")
    else:
        for nm, n in sorted(used.items(), key=lambda x: -x[1]):
            inf = sum(1 for d in detail if d["inferred"] and canon(nm) in canon(d["pattern"]))
            tag = f"  ⚠️ {inf} 条为推断（历史脚本没写 hook_pattern）" if inf else ""
            print(f"  {nm:<6} × {n} 条{tag}")
    over = [nm for nm, n in used.items() if n >= 3]
    if over:
        print(f"  → ⚠️ {'、'.join(over)} 已连用 ≥3 条，本轮必须换原型")

    print(f"\n【推荐没用过的格子 · top {len(rec)}】只给结构，台词与评分由 Agent 补")
    for i, c in enumerate(rec, 1):
        flag = "  ⚠️中老年品类基本禁用" if canon(c["pattern"]) == "悬念" else (
               "  ⚠️钩子不念数值，通常归混剪" if canon(c["pattern"]) == "算账" else "")
        print(f"  {i}. {c['pattern']} × {c['slot']}   [{c['tier']}] 用过 {c['used']} 次  排序分 {c['score']}{flag}")

    print("\n下一步：挑 3 个格子 → 各写一句第一句台词 + 四拍 + 五维分（≥18 进终选）")
    print("       → 人挑一个 → 才写整片 → validate.py → prompt_lint.py → 出片")


if __name__ == "__main__":
    main()
