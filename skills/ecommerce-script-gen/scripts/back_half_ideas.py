#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
back_half_ideas.py — 后半段（15–60 秒）可变量供给器。

钩子有六型 + 12 冲突原型在轮换，后半段过去是模板：每条片子的卖点顺序、举证方式、
逼单话术都一样，从第 16 秒开始眼熟。这个脚本给后半段补上同样的纪律：
不遗漏（六个卖点都列出来）、不重复（指纹查重）、可选（给 3 个未用过的组合）。

它只做三件机器能做的事：
  1. 列五个槽位：卖点主角 / 举证顺序 / 证据形态 / 数字处理 / 逼单 CTA
  2. 扫已有脚本，抽「卖点主角 + 举证顺序 + 逼单」三元组当**指纹**（老脚本没写字段就按文本推断）
  3. 推荐不与近 3 条撞指纹的组合

**不写台词**：选哪个组合、怎么说，是编剧的活。
事实层（配料三样 / 数值 / 规格 / 产地 / 三个价）不许变，变的只有顺序与说法。

用法：
  python3 scripts/back_half_ideas.py --pack packs/{品类}/category-pack.json --scripts-dir .
  python3 scripts/back_half_ideas.py ... --json
  python3 scripts/back_half_ideas.py ... --check "配料|数值→配料→吃法|折算单罐价"
"""
import argparse
import glob
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---------- 槽位定义：左侧是内部代号，右侧是识别关键词 ----------
LEADS = [
    ("配料", ("配料", "三样", "白砂糖", "干干净净", "认得的字")),
    ("吃法", ("抹", "蘸", "拌", "舀", "拉丝", "咸甜", "抹开")),
    ("数值", ("每100克", "每 100 克", "膳食纤维", "维生素", "毫克", "9.2")),
    ("产地", ("新疆", "图木舒克", "产地", "种的", "慢烘", "沙漠")),
    ("规格", ("150克", "150 克", "开封", "冰箱", "两周", "吃完")),
    ("价格", ("拍一发四", "合下来", "直播间", "五十九", "二十五", "块")),
]
CHAIN_HINT = {
    "顺序要回答的是：观众先想听哪一句。",
}
CLOSES = [
    ("拍一发四折算", ("拍一发四", "合下来", "四罐", "折算")),
    ("低门槛先一罐", ("先来一罐", "先拿一罐", "先试一罐", "一罐试试")),
    ("转给家里做饭的", ("转给", "发给", "家里做饭", "拿不准")),
    ("场景提问互动", ("你家", "你们家", "早上吃什么", "评论区")),
    ("风险逆转", ("开封", "两周吃完", "放冰箱", "不浪费")),
    ("先转再拍", ("先转", "转完再", "让她看看")),
]
NUMBERS = ["原值直读", "折算单罐价", "对比日常价", "换成人话（须有出处）", "不念数字只打字幕"]
PROOFS = ["翻罐读配料表", "三样倒出来并置", "数值特写", "撕封条开罐", "抹开成品镜头", "生产线灌装"]


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
    return Path(cands[0])


def seg_text(s):
    """段里可能把口播放在 text/line/vo/dialogue，取一个能用的。"""
    parts = []
    for k in ("text", "line", "vo", "narration", "content"):
        v = s.get(k)
        if isinstance(v, str):
            parts.append(v)
    d = s.get("dialogue")
    if isinstance(d, list):
        for x in d:
            parts.append(str(x.get("line") or x.get("text") or "") if isinstance(x, dict) else str(x))
    return " ".join(p for p in parts if p)


def classify(text):
    """按关键词给一句口播打卖点标签；命中多个时返回最靠前的那个（句子在讲什么）。"""
    best, pos = None, 10 ** 9
    for name, keys in LEADS:
        for k in keys:
            i = text.find(k)
            if i != -1 and i < pos:
                pos, best = i, name
    return best


def close_of(texts):
    blob = " ".join(texts)
    for name, keys in CLOSES:
        if any(k in blob for k in keys):
            return name
    return "（未识别）"


def fingerprint(lead, chain, close):
    return f"{lead}|{'→'.join(chain)}|{close}"


def scan_used(scripts_dir):
    """扫历史脚本：优先读顶层 back_half 字段，没有就按混剪段文本推断。"""
    rows = []
    for f in sorted(Path(scripts_dir).glob("*.json")):
        if any(t in f.name for t in ("segs", "bounds", "asset_index", "pack")):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        segs = d.get("segments") or []
        if not segs:
            continue
        bh = d.get("back_half") or {}
        mix = [s for s in segs[1:] if isinstance(s, dict)]
        texts = [seg_text(s) for s in mix]
        inferred = False
        if bh.get("lead") and bh.get("chain") and bh.get("close"):
            lead, chain, close = bh["lead"], list(bh["chain"]), bh["close"]
        else:
            chain, inferred = [], True
            for t in texts:
                c = classify(t)
                if c and c not in chain:
                    chain.append(c)
            lead = chain[0] if chain else "（未识别）"
            close = close_of(texts[-2:])
            chain = chain[:4]
        rows.append({"file": f.name, "lead": lead, "chain": chain, "close": close,
                     "fp": fingerprint(lead, chain, close), "inferred": inferred})
    return rows


def main():
    ap = argparse.ArgumentParser(description="后半段可变槽位供给器：列槽位 / 查重指纹 / 推荐未用组合")
    ap.add_argument("--pack", default="")
    ap.add_argument("--scripts-dir", default=".", help="已有文案 JSON 目录（查重）")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", default="", help='校验候选：--check "配料|数值→配料→吃法|拍一发四折算"')
    ap.add_argument("--self", default="", help="当前正在编辑的脚本文件名，查重时排除（否则自指误报撞车）")
    a = ap.parse_args()

    pack = json.loads(find_pack(a.pack).read_text(encoding="utf-8"))
    rows = scan_used(a.scripts_dir)
    if a.self:
        rows = [r for r in rows if Path(r["file"]).name != Path(a.self).name]
    recent = rows[-3:]
    recent_fp = {r["fp"] for r in recent}
    recent_leads = {r["lead"] for r in recent}
    recent_chain_heads = {(r["chain"][0] if r["chain"] else "") for r in recent}

    if a.check:
        parts = [x.strip() for x in a.check.split("|")]
        if len(parts) != 3:
            sys.exit('✗ --check 需要三段：卖点|顺序|逼单，用竖线分隔')
        lead, chain_s, close = parts
        chain = [x for x in re.split(r"→|->", chain_s) if x]
        fp = fingerprint(lead, chain, close)
        hits = [r for r in rows if r["fp"] == fp]
        print(f"候选指纹  {fp}")
        if fp in recent_fp:
            print("✗ 与近 3 条撞指纹，重排顺序或换逼单")
            sys.exit(1)
        if hits:
            print(f"⚠️ 历史用过 {len(hits)} 次：{', '.join(h['file'] for h in hits)}（不禁止，但要自觉能让观众看出区别）")
        else:
            print("✓ 全新组合")
        if lead in recent_leads:
            print(f"⚠️ 卖点主角「{lead}」近 3 条里出现过，建议换一个当主打")
        return

    if a.json:
        print(json.dumps({"used": rows, "recent": [r["fp"] for r in recent],
                          "recommend": _recommend(recent_fp, recent_leads, recent_chain_heads, a.top)},
                         ensure_ascii=False, indent=2))
        return

    print(f"品类包 {pack.get('meta', {}).get('category', '?')}   扫描到 {len(rows)} 条脚本\n")
    print("【六个卖点 · 谁当主角都行，但事实值不许改】")
    for name, keys in LEADS:
        print(f"  {name}  ← {'/'.join(keys[:4])}")
    print(f"\n【举证顺序】常见三条：配料→数值→吃法（稳）｜数值→配料→吃法（理性）｜吃法→配料→数值（先勾口欲）")
    print(f"【证据形态】{' · '.join(PROOFS)}")
    print(f"【数字处理】{' · '.join(NUMBERS)}")
    print(f"【逼单】{' · '.join(n for n, _ in CLOSES)}")

    print("\n【已用指纹】")
    if not rows:
        print("  （无记录）")
    for r in rows:
        tag = "  ⚠️推断" if r["inferred"] else ""
        print(f"  {r['file']}  {r['fp']}{tag}")
    if len(recent_fp) < len(recent):
        print("  → ⚠️ 近 3 条里有完全相同的指纹，量产会显得同质化")

    rec = _recommend(recent_fp, recent_leads, recent_chain_heads, a.top)
    print(f"\n【推荐组合 · top {len(rec)}】只给结构，台词与画面由 Agent 填")
    for i, c in enumerate(rec, 1):
        print(f"  {i}. 主角={c['lead']}  顺序={c['chain']}  逼单={c['close']}")
        print(f"     为什么：{c['why']}")
    print("\n下一步：挑一个 → 写混剪 9 句 → 配上证据形态 → validate.py")


def _recommend(recent_fp, recent_leads, recent_chain_heads, top):
    chains = [
        ["配料", "数值", "吃法"], ["数值", "配料", "吃法"],
        ["吃法", "配料", "数值"], ["产地", "配料", "吃法"],
        ["规格", "吃法", "价格"], ["吃法", "产地", "数值"],
        ["配料", "吃法", "规格"], ["产地", "吃法", "价格"],
    ]
    out = []
    for lead, _ in LEADS:
        for chain in chains:
            for close, _ in CLOSES:
                fp = fingerprint(lead, chain, close)
                if fp in recent_fp:
                    continue
                score = 0
                why = []
                if lead not in recent_leads:
                    score += 3
                    why.append("主角近 3 条没用过")
                if chain[0] not in recent_chain_heads:
                    score += 2
                    why.append("开场那一类卖点没连着用")
                if chain[0] == lead:
                    score += 1
                    why.append("开场就上主打卖点，整段聚焦")
                out.append({"lead": lead, "chain": "→".join(chain), "close": close,
                            "score": score, "why": "；".join(why) or "可用，但不算新鲜"})
    out.sort(key=lambda x: -x["score"])
    # 同一条推荐里不要三个都只差逼单话术：按 (主角, 顺序) 去重，逼单也别连着重复
    picked, seen, used_close = [], set(), set()
    for c in out:
        k = (c["lead"], c["chain"])
        if k in seen:
            continue
        if c["close"] in used_close and len(picked) < top:
            continue
        seen.add(k)
        used_close.add(c["close"])
        picked.append(c)
        if len(picked) >= top:
            break
    for c in out:                      # 不够数就放宽逼单限制补齐
        if len(picked) >= top:
            break
        if (c["lead"], c["chain"]) in seen:
            continue
        seen.add((c["lead"], c["chain"]))
        picked.append(c)
    return picked[:top]


if __name__ == "__main__":
    main()
