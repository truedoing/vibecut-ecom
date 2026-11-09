#!/usr/bin/env python3
"""剧集包校验器（短剧专用）—— 结构层检查，不替人核实事实。

用法: python3 validate_pack.py packs/short-drama/<剧名>/drama-pack.json
退出码: 0 = 无 ERROR, 1 = 有 ERROR

与商品包校验器不通用：字段族、红线体系、四层语义全都不同，刻意不做成一份。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = "1.0"
KIND = "short_drama"
TOP_KEYS = {
    "meta", "drama_facts", "characters", "hook_map",
    "compliance", "elements", "pending", "asset_index", "conflicts",
}
EIGHT_LIBS = ["hooks", "pains", "scenes", "proofs", "trust", "ctas", "golden_quotes", "audiences"]
PROOF_LEVELS = {"E1", "E2", "E3", "E4"}
TRUST_LEVELS = {"T1", "T2", "T3"}
HOOK_TYPES = {"conflict", "suspense", "contrast", "reveal"}
HOOK_STATUS = {"ready", "conditional", "blocked"}
PLATFORMS = {"kuaishou", "douyin", "other"}
MODES = {"official_task_material", "self_licensed", "no_license"}
DESUB = {"done", "pending", "not_needed"}
CHECK_STATUS = {"ok", "watch", "pending", "blocked"}
MUST_CHECK = {
    "ending_spoiler", "disability_disease_wording", "rebuild_evidence",
    "offsite_diversion", "ai_label",
}
# 只有这三类来源可以标 verified —— task_template / external_search / inferred 永远不行
VERIFIABLE_SOURCES = {"local_material", "subtitle_ocr", "platform_task_page", "platform_list"}
SOURCE_TYPES = VERIFIABLE_SOURCES | {"task_template", "external_search", "inferred"}
# 反向检查：商品包字段出现在剧集包里，说明抄错了结构
PRODUCT_FIELDS = {"product_facts", "selling_point_map", "price_talk", "ingredients",
                  "nutrition", "net_weight", "allergen"}
CONFLICT_AUTO_WORDS = ["选定", "采用", "取值", "就按", "为准", "决定用"]
MIN_HOOKS = 6

errors: list[str] = []
warns: list[str] = []
oks: list[str] = []


def err(code: str, msg: str) -> None:
    errors.append(f"[{code}] {msg}")


def warn(code: str, msg: str) -> None:
    warns.append(f"[{code}] {msg}")


def ok(msg: str) -> None:
    oks.append(msg)


def check_time(node, where: str) -> None:
    """时间码必须是 >=0 的数字。"""
    if not isinstance(node, (int, float)) or node < 0:
        err("TIME", f"{where} 时间码非法: {node!r}")


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 validate_pack.py <drama-pack.json>")
        return 1
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"文件不存在: {path}")
        return 1
    raw = path.read_bytes()
    try:
        d = json.loads(raw.decode("utf-8"))
    except Exception as e:
        print(f"JSON 解析失败: {e}")
        return 1

    # 1. schema 与顶层
    meta = d.get("meta") or {}
    if meta.get("schema_version") != SCHEMA_VERSION:
        err("SCHEMA", f"schema_version 是 {meta.get('schema_version')!r}，应为 {SCHEMA_VERSION}")
    else:
        ok(f"schema_version {SCHEMA_VERSION}")
    if meta.get("kind") != KIND:
        err("SCHEMA", f"meta.kind 应为 {KIND!r}，实际 {meta.get('kind')!r}")
    missing = TOP_KEYS - set(d.keys())
    if missing:
        err("SHAPE", f"顶层缺块: {sorted(missing)}")
    extra = set(d.keys()) - TOP_KEYS
    if extra:
        warn("SHAPE", f"顶层多出未约定字段 {sorted(extra)}")
    if not missing:
        ok(f"顶层 {len(TOP_KEYS)} 键齐备")

    # 2. 反向检查：不许混入商品字段
    hit = PRODUCT_FIELDS & set(d.keys())
    if hit:
        err("MIXED", f"出现商品包字段 {sorted(hit)} —— 剧集包与商品包 schema 不互认，抄错结构了")

    # 3. meta
    if not str(meta.get("drama") or "").strip():
        err("META", "meta.drama 为空")
    else:
        ok(f"drama = {meta['drama']}")
    if meta.get("platform") not in PLATFORMS:
        err("META", f"meta.platform 非法: {meta.get('platform')!r}")
    if meta.get("task_id") is None:
        warn("META", "meta.task_id 为 null —— 任务页字段未登记，结算与授权无法回溯")
    review = meta.get("review") or {}
    if review.get("published_to_tdrive") is True and review.get("human_review") != "passed":
        err("PUBLISH", "published_to_tdrive=true 但 human_review 不是 passed —— 未经人审不得上传")
    ok(f"发布状态：human_review={review.get('human_review')}, ttdrive={review.get('published_to_tdrive')}")

    # 4. drama_facts
    facts = d.get("drama_facts") or []
    if not facts:
        err("FACTS", "drama_facts 为空")
    fields_seen = set()
    for f in facts:
        fid = f.get("id", "?")
        for key in ("field", "label", "value", "source", "verified", "usable_in_copy", "blocking"):
            if key not in f:
                err("FACTS", f"{fid} 缺字段 {key}")
        fields_seen.add(f.get("field"))
        src = f.get("source") or {}
        if not src.get("type") or not src.get("ref"):
            err("FACTS", f"{fid}.source 需含 type 与 ref")
        elif src.get("type") not in SOURCE_TYPES:
            err("FACTS", f"{fid}.source.type 非法: {src.get('type')!r}")
        if f.get("verified") is True and src.get("type") not in VERIFIABLE_SOURCES:
            err("VERIFIED", f"{fid} 标 verified:true 但来源是 {src.get('type')!r} —— 模板文案/外部检索/推断都不算核实")
        if f.get("value") is None:
            if (f.get("usable_in_copy") is True or f.get("blocking") is True) and not f.get("placeholder"):
                err("FACTS", f"{fid}.value 为 null 且会进口播/卡文案，必须有 placeholder")
        if f.get("verified") is not True and f.get("usable_in_copy") is True:
            warn("FACTS", f"{fid} 未核实却标了可用进口播")
    for need in ("title", "logline", "premise", "style"):
        if need not in fields_seen:
            err("FACTS", f"drama_facts 缺关键字段 {need}")
    if fields_seen:
        ok(f"drama_facts {len(facts)} 条，关键字段齐备")

    # 5. characters
    chars = d.get("characters") or []
    if len(chars) < 3:
        err("CAST", f"characters 只有 {len(chars)} 人，至少 3 人")
    roles = {c.get("role") for c in chars}
    for need in ("protagonist", "antagonist"):
        if need not in roles:
            err("CAST", f"characters 缺 role={need}")
    for c in chars:
        cid = c.get("id", "?")
        ev = c.get("evidence") or {}
        if not ev.get("quote") or ev.get("ep") is None:
            err("CAST", f"{cid} 缺 evidence（ep + quote）—— 人物位置必须有台词证明")
        else:
            check_time(ev.get("t"), f"{cid}.evidence")
    if chars:
        ok(f"characters {len(chars)} 人（protagonist/antagonist 齐）")

    # 6. hook_map —— 本技能核心产出
    hooks = d.get("hook_map") or []
    finale = (d.get("asset_index") or {}).get("finale_episodes") or []
    if not finale:
        err("ASSET", "asset_index.finale_episodes 为空 —— 必须标明哪几集是大结局")
    if len(hooks) < MIN_HOOKS:
        err("HOOK", f"hook_map 只有 {len(hooks)} 条，至少 {MIN_HOOKS} 条（要撑 6 条视频）")
    types_used = set()
    for h in hooks:
        hid = h.get("id", "?")
        for key in ("beat", "ep", "t_start", "t_end", "quote", "truncate_at",
                    "opening_lines", "hook_type", "risk", "status"):
            if key not in h:
                err("HOOK", f"{hid} 缺字段 {key}")
        if h.get("ep") in finale:
            err("HOOK", f"{hid} 取了大结局第 {h.get('ep')} 集 —— 结局不得作为钩子")
        if h.get("hook_type") not in HOOK_TYPES:
            err("HOOK", f"{hid}.hook_type 非法: {h.get('hook_type')!r}")
        if h.get("status") not in HOOK_STATUS:
            err("HOOK", f"{hid}.status 非法: {h.get('status')!r}")
        types_used.add(h.get("hook_type"))
        for tk in ("t_start", "t_end"):
            check_time(h.get(tk), f"{hid}.{tk}")
        if isinstance(h.get("t_start"), (int, float)) and isinstance(h.get("t_end"), (int, float)):
            if h["t_end"] <= h["t_start"]:
                err("HOOK", f"{hid} t_end <= t_start")
        ta = h.get("truncate_at") or {}
        if not ta.get("quote"):
            err("HOOK", f"{hid}.truncate_at 缺 quote —— 断点必须是一句完整台词之后")
        if ta.get("ep") in finale:
            err("HOOK", f"{hid}.truncate_at 落在大结局")
        lines = h.get("opening_lines") or []
        if not lines:
            err("HOOK", f"{hid} 没有 opening_lines")
        elif len(lines) < 3:
            warn("HOOK", f"{hid} 只有 {len(lines)} 条开场白（建议 3 条，对应三种结构）")
    if hooks:
        ok(f"hook_map {len(hooks)} 条，覆盖 {len(types_used)} 种钩子类型")

    # 7. compliance
    cp = d.get("compliance") or {}
    mc = cp.get("must_check") or {}
    miss = MUST_CHECK - set(mc.keys())
    if miss:
        err("COMPLIANCE", f"compliance.must_check 缺必查项: {sorted(miss)}")
    for k, v in mc.items():
        if k not in MUST_CHECK:
            warn("COMPLIANCE", f"must_check 多出未约定项 {k}")
        if (v or {}).get("status") not in CHECK_STATUS:
            err("COMPLIANCE", f"must_check.{k}.status 非法: {(v or {}).get('status')!r}")
        if (v or {}).get("status") == "blocked":
            warn("COMPLIANCE", f"must_check.{k} 为 blocked —— 这条不解决不许发布")
    if cp.get("ending_policy") != "do_not_use":
        err("COMPLIANCE", f"ending_policy 必须为 do_not_use，实际 {cp.get('ending_policy')!r}")
    if not (cp.get("banned_in_voiceover") or []):
        err("COMPLIANCE", "banned_in_voiceover 为空 —— 旁白禁词表必须给")
    if not (cp.get("quote_risk_terms") or []):
        warn("COMPLIANCE", "quote_risk_terms 为空 —— 原台词里的风险词应列出，只提示不扫禁")
    if mc:
        ok(f"compliance 必查项 {len(MUST_CHECK & set(mc))}/{len(MUST_CHECK)} 项，ending_policy=do_not_use")

    # 8. elements
    els = d.get("elements") or {}
    miss_lib = [k for k in EIGHT_LIBS if k not in els]
    if miss_lib:
        err("ELEMENTS", f"elements 缺库: {miss_lib}")
    else:
        ok("elements 八库齐备")
    for p in els.get("proofs") or []:
        if p.get("level") not in PROOF_LEVELS:
            err("ELEMENTS", f"proofs.level 非法: {p.get('level')!r}，应为 E1–E4")
    for t in els.get("trust") or []:
        if t.get("level") not in TRUST_LEVELS:
            err("ELEMENTS", f"trust.level 非法: {t.get('level')!r}，应为 T1–T3")
    gq = els.get("golden_quotes") or []
    if not gq:
        err("ELEMENTS", "golden_quotes 为空 —— 短剧包的核心资产就是高光台词")
    for g in gq:
        gid = g.get("id", "?")
        if g.get("ep") is None or g.get("t") is None:
            err("ELEMENTS", f"{gid} 缺 ep 或 t —— 金句没有时间码就不是证据")
        else:
            check_time(g.get("t"), f"{gid}.t")
            if g.get("ep") in finale:
                err("ELEMENTS", f"{gid} 取了大结局")
    au = els.get("audiences") or []
    if not au:
        err("ELEMENTS", "audiences 为空 —— 人群必须从剧情推，不能省")
    for a in au:
        aid = a.get("id", "?")
        ev = a.get("evidence") or []
        if len(ev) < 2:
            err("ELEMENTS", f"{aid}.evidence 少于 2 条 —— 人群结论要能被反驳")
        if not (a.get("address_as") or []):
            err("ELEMENTS", f"{aid} 缺 address_as（口播怎么称呼观众）")
        if a.get("confidence") not in {"high", "medium", "low"}:
            err("ELEMENTS", f"{aid}.confidence 非法: {a.get('confidence')!r}")
        if a.get("confidence") == "high" and not a.get("calibration"):
            warn("ELEMENTS", f"{aid} 无后台数据却标 confidence=high")
    if gq or au:
        ok(f"golden_quotes {len(gq)} 条（均带时间码）/ audiences {len(au)} 组")

    # 9. pending
    pend = d.get("pending") or []
    if not pend:
        warn("PENDING", "pending 为空 —— 若确实全部核实过，请在说明.md 注明")
    for p in pend:
        pid = p.get("id", "?")
        if not isinstance(p.get("blocking"), bool):
            err("PENDING", f"{pid}.blocking 必须是布尔")
        for key in ("item", "owner", "how_to_resolve"):
            if not p.get(key):
                warn("PENDING", f"{pid} 缺 {key}")
    if pend:
        ok(f"pending {len(pend)} 项（blocking {sum(1 for p in pend if p.get('blocking') is True)}）")

    # 10. asset_index
    ai = d.get("asset_index") or {}
    if ai.get("mode") not in MODES:
        err("ASSET", f"asset_index.mode 非法: {ai.get('mode')!r}")
    eps = ai.get("episodes") or []
    if not eps:
        err("ASSET", "asset_index.episodes 为空")
    for e in eps:
        eid = e.get("ep", "?")
        if not e.get("file"):
            err("ASSET", f"第 {eid} 集缺 file")
        if e.get("desub") not in DESUB:
            err("ASSET", f"第 {eid} 集 desub 非法: {e.get('desub')!r}")
    for fe in finale:
        if not any(e.get("ep") == fe for e in eps):
            warn("ASSET", f"finale_episodes 里的第 {fe} 集不在 episodes 清单")
    if ai.get("mode") == "no_license":
        err("ASSET", "mode=no_license —— 无授权不得建包")
    if eps:
        ok(f"asset_index {len(eps)} 集登记，大结局 {finale}")

    # 11. conflicts —— 不自动选值
    cfs = d.get("conflicts") or []
    for c in cfs:
        cid = c.get("id", "?")
        if c.get("status") not in {"open", "resolved"}:
            err("CONFLICT", f"{cid}.status 非法: {c.get('status')!r}")
        if c.get("status") == "open":
            vals = c.get("values") or []
            if len(vals) < 2:
                err("CONFLICT", f"{cid} 标 open 但 values 少于 2 项")
            res = str(c.get("resolution") or "")
            hit_w = [w for w in CONFLICT_AUTO_WORDS if w in res]
            if hit_w:
                err("CONFLICT", f"{cid}.resolution 疑似自动选值（命中 {hit_w}）—— 冲突必须交人定")
            for v in vals:
                if not v.get("source"):
                    warn("CONFLICT", f"{cid} 某个 value 缺 source")
    ok(f"conflicts {len(cfs)} 条（open {sum(1 for c in cfs if c.get('status') == 'open')}）")

    # 12. 纯净度：解释性字段不该进 JSON
    desc = []
    if "notes" in meta:
        desc.append("meta.notes")
    for f in facts:
        if "verified_note" in f:
            desc.append("drama_facts[].verified_note")
    for key, v in mc.items():
        if "why" in (v or {}):
            desc.append(f"compliance.must_check.{key}.why")
    for key in ("note", "mode_note"):
        if key in ai:
            desc.append(f"asset_index.{key}")
    if desc:
        warn("PURITY", "JSON 含解释性字段，应移到说明.md: " + ", ".join(desc))
    else:
        ok("机读源纯净：无解释性字段")

    # 13. 体积
    kb = len(raw) / 1024
    if kb > 120:
        err("SIZE", f"产物 {kb:.1f} KB 过大 —— 检查是否把台词稿/方法论抄进了包")
    elif kb > 60:
        warn("SIZE", f"产物 {kb:.1f} KB 偏大，确认 hook_map 是否过度展开")
    else:
        ok(f"产物体积 {kb:.1f} KB")

    print(f"=== 剧集包校验：{path.name} ===")
    for line in oks:
        print(f"  OK    {line}")
    for line in warns:
        print(f"  WARN  {line}")
    for line in errors:
        print(f"  ERROR {line}")
    print(f"\n合计：{len(errors)} ERROR / {len(warns)} WARN")
    if errors:
        print("结论：❌ 不通过，修到零 ERROR 再交人审")
        return 1
    if warns:
        print("结论：✅ 结构通过，有 WARN 需人工确认（校验器不替人核实事实）")
    else:
        print("结论：✅ 零错零警告，结构通过（校验器不替人核实事实）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
