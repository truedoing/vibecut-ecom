#!/usr/bin/env python3
"""品类包校验器 —— 结构层检查，不替人核实事实。

用法: python3 validate_pack.py packs/{category}/category-pack.json
退出码: 0 = 无 ERROR, 1 = 有 ERROR
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = "1.0"
TOP_KEYS = {
    "meta", "product_facts", "selling_point_map", "compliance_ext",
    "elements", "pending", "asset_index", "conflicts",
}
SEVEN_LIBS = ["hooks", "pains", "scenes", "proofs", "trust", "ctas", "golden_quotes"]
PROOF_LEVELS = {"L1", "L2", "L3", "L4"}
TRUST_LEVELS = {"T1", "T2", "T3"}
BLOCKABLE_RISKS = {
    "疾病功效", "无依据虚假比较", "无认证宣称", "虚假价格稀缺",
    "疾病功效宣称", "虚假比较", "无认证", "虚假稀缺",
}
STRONG_SOURCES = {"official_label", "product_page", "lab_report", "official_web"}
# official_web：出版社/百科/官方榜单页等网络官方一手来源（2026-11-06 图书包引入——
# 食品包的 strong 三类全是「实拍/检测」思路，虚拟知识品只能以官方网页为一手来源）
WEAK_SOURCES = {"vendor_claim", "detail_page", "reference_video", "script_design", "inferred", "unknown"}
# 这两类可标 verified:true，但必须在 verified_note 里写明「不是厂商事实」
SELF_CONFIRMED = {"script_design", "project_config"}
ALLOWED_VERIFIED = STRONG_SOURCES | SELF_CONFIRMED
# no_vendor_footage：无厂商素材的品类（图书/课程等知识品），四层比例重排（2026-11-06 图书包引入）
MODES = {"vendor_provided", "legacy_competitor_clips", "no_vendor_footage"}
CONFLICT_AUTO_WORDS = ["选定", "采用", "取值", "就按", "以…为准", "决定用"]

# 兜底禁词（基础档）。人群专属档与品类专属档由各自文档/包提供，这里只做基础扫描。
BANNED = [
    "清肠", "排毒", "控卡", "燃脂", "减肥", "瘦身", "治疗", "预防", "疗效",
    "三高", "血压", "血糖", "血脂", "便秘", "增强免疫", "防癌", "延年",
    "软化血管", "医生", "营养师", "秘方", "央视", "专家", "药用", "调理", "祛湿",
    "有机", "零农残", "蓝帽子", "库存不多", "错过再等", "最后一天", "限时", "抢光",
    "国家级", "最高级", "最佳", "纯天然", "绝对安全", "100%",
]
# 只扫这些「会真的进文案」的字段；vendor_claim / basis / reason / term 是分析文本，不扫。
SCAN_FIELDS = ["usable_rewrite", "example", "wording", "quote", "claim", "content"]

errors: list[str] = []
warns: list[str] = []
oks: list[str] = []


def err(code: str, msg: str) -> None:
    errors.append(f"[{code}] {msg}")


def warn(code: str, msg: str) -> None:
    warns.append(f"[{code}] {msg}")


def ok(msg: str) -> None:
    oks.append(msg)


def collect_scan_text(node, out: list[tuple[str, str]]) -> None:
    """递归收集需要兜底扫描的文本，带路径便于定位。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in SCAN_FIELDS and isinstance(v, str) and v.strip():
                out.append((k, v))
            elif k not in SCAN_FIELDS and isinstance(v, (dict, list)):
                collect_scan_text(v, out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            collect_scan_text(v, out)


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python3 validate_pack.py <category-pack.json>")
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

    # 1. schema 与顶层结构
    meta = d.get("meta") or {}
    if meta.get("schema_version") != SCHEMA_VERSION:
        err("SCHEMA", f"schema_version 是 {meta.get('schema_version')!r}，应为 {SCHEMA_VERSION}")
    else:
        ok(f"schema_version {SCHEMA_VERSION}")
    missing = TOP_KEYS - set(d.keys())
    if missing:
        err("SHAPE", f"顶层缺块: {sorted(missing)}")
    extra = set(d.keys()) - TOP_KEYS
    if extra:
        warn("SHAPE", f"顶层多出未约定字段 {sorted(extra)}（二次汇总字段不该进包）")
    if not missing:
        ok(f"顶层 {len(TOP_KEYS)} 键齐备")

    # 2. category
    cat = meta.get("category")
    if not cat or not str(cat).strip():
        err("META", "meta.category 为空，必须与 vibecut project list 的项目名一致")
    else:
        ok(f"category = {cat}")

    # 3. product_facts
    facts = d.get("product_facts") or []
    if not facts:
        err("FACTS", "product_facts 为空")
    pending_items = " ".join(str(p.get("item", "")) for p in (d.get("pending") or []))
    for f in facts:
        fid = f.get("id", "?")
        for key in ("source", "verified", "usable_in_copy", "blocking"):
            if key not in f:
                err("FACTS", f"{fid} 缺字段 {key}")
        src = f.get("source") or {}
        if not src.get("type") or not src.get("ref"):
            err("FACTS", f"{fid}.source 需含 type 与 ref")
        if f.get("verified") is True and src.get("type") not in ALLOWED_VERIFIED:
            warn("VERIFIED", f"{fid} 标 verified:true 但来源是 {src.get('type')!r} —— 厂商卖点图/竞品台词不算核实")
        if f.get("value") is None:
            ph = f.get("placeholder")
            needs_placeholder = f.get("usable_in_copy") is True or f.get("blocking") is True
            if needs_placeholder and not ph:
                err("FACTS", f"{fid}.value 为 null 且该字段会进文案/卡配音，必须有 placeholder")
            elif ph and ph not in pending_items:
                warn("PENDING", f"{fid} 的占位符 {ph} 在 pending 里没有对应项")
    if facts:
        n_ver = sum(1 for f in facts if f.get("verified") is True)
        ok(f"product_facts {len(facts)} 条（verified {n_ver} / 未核实 {len(facts)-n_ver}）")

    # 4. selling_point_map
    sps = d.get("selling_point_map") or []
    if not sps:
        err("SP", "selling_point_map 为空 —— 建包不是列禁用清单，要给出可用说法")
    for s in sps:
        sid = s.get("id", "?")
        rewrite = s.get("usable_rewrite")
        status = s.get("status")
        risk = s.get("risk_type", "")
        if not rewrite:
            if status == "blocked" and any(b in str(risk) for b in BLOCKABLE_RISKS):
                ok(f"{sid} 四类红线，允许 blocked")
            else:
                err("SP", f"{sid} 没有 usable_rewrite —— 厂商卖点不许直接删，先试改写（status={status}, risk={risk}）")
        else:
            if status == "blocked":
                warn("SP", f"{sid} 标 blocked 却填了 usable_rewrite，状态矛盾")
        for key in ("condition", "status"):
            if key not in s:
                err("SP", f"{sid} 缺字段 {key}")
        if status not in {"ready", "conditional", "blocked"}:
            err("SP", f"{sid}.status 非法: {status!r}")
    if sps:
        n_blocked = sum(1 for s in sps if s.get("status") == "blocked")
        ok(f"selling_point_map {len(sps)} 条（blocked {n_blocked}，其余均有可用说法）")

    # 5. compliance_ext
    ce = d.get("compliance_ext") or {}
    if not (ce.get("category_specific") or []):
        err("COMPLIANCE", "compliance_ext.category_specific 为空 —— 品类包必须给出品类专属条目")
    else:
        ok(f"compliance_ext 品类专属 {len(ce['category_specific'])} 条")
    if not ce.get("shared_overlay_ref") or not ce.get("base_redline_ref"):
        warn("COMPLIANCE", "缺 shared_overlay_ref / base_redline_ref —— 共享参数应写 ref，不要复制正文")

    # 6. elements
    els = d.get("elements") or {}
    miss_libs = [k for k in SEVEN_LIBS if k not in els]
    if miss_libs:
        err("ELEMENTS", f"elements 缺库: {miss_libs}")
    else:
        ok(f"elements 七库齐备（+ price_talk {'有' if 'price_talk' in els else '无'}）")
    for p in els.get("proofs") or []:
        if p.get("level") not in PROOF_LEVELS:
            err("ELEMENTS", f"proofs.level 非法: {p.get('level')!r}，应为 L1–L4")
    for t in els.get("trust") or []:
        if t.get("level") not in TRUST_LEVELS:
            err("ELEMENTS", f"trust.level 非法: {t.get('level')!r}，应为 T1–T3")

    # 7. pending
    pend = d.get("pending") or []
    if not pend:
        warn("PENDING", "pending 为空 —— 若确实全部核实过，请在 meta.notes 说明")
    for p in pend:
        pid = p.get("id", "?")
        if not isinstance(p.get("blocking"), bool):
            err("PENDING", f"{pid}.blocking 必须是布尔（卡不卡配音要分开标）")
        for key in ("owner", "how_to_resolve"):
            if not p.get(key):
                warn("PENDING", f"{pid} 缺 {key}")
    if pend:
        n_block = sum(1 for p in pend if p.get("blocking") is True)
        ok(f"pending {len(pend)} 项（blocking {n_block}）")

    # 8. conflicts —— 不自动选值
    cfs = d.get("conflicts") or []
    for c in cfs:
        cid = c.get("id", "?")
        if c.get("status") == "open":
            vals = c.get("values") or []
            if len(vals) < 2:
                err("CONFLICT", f"{cid} 标 open 但 values 少于 2 项")
            res = str(c.get("resolution") or "")
            hit = [w for w in CONFLICT_AUTO_WORDS if w in res]
            if hit:
                err("CONFLICT", f"{cid}.resolution 疑似自动选值（命中 {hit}）—— 冲突必须交人定")
            for v in vals:
                if not v.get("source"):
                    warn("CONFLICT", f"{cid} 某个 value 缺 source")
        if c.get("status") not in {"open", "resolved"}:
            err("CONFLICT", f"{cid}.status 非法: {c.get('status')!r}")
    if cfs:
        warn("CONFLICT", f"conflicts 有 {len(cfs)} 条 —— 待裁决清单属于人审，应写进 品类包说明.md，JSON 固定 []")
    ok(f"conflicts {len(cfs)} 条（open {sum(1 for c in cfs if c.get('status')=='open')}）")


    # 8.5 机读源纯净度 —— 解释性文字不进 JSON
    desc_hits: list[str] = []
    if "notes" in meta:
        desc_hits.append("meta.notes")
    if any("verified_note" in f for f in facts):
        desc_hits.append("product_facts[].verified_note")
    if any("basis" in x for x in sps):
        desc_hits.append("selling_point_map[].basis")
    if "note" in ce:
        desc_hits.append("compliance_ext.note")
    if "note" in els:
        desc_hits.append("elements.note")
    for k in ("mode_note", "measured_finding"):
        if k in (d.get("asset_index") or {}):
            desc_hits.append(f"asset_index.{k}")
    if desc_hits:
        warn("PURITY", "JSON 含解释性/数据质量描述字段，应移到说明.md: " + ", ".join(desc_hits))
    else:
        ok("机读源纯净：无解释性描述字段")

    # 9. asset_index
    ai = d.get("asset_index") or {}
    if ai.get("mode") not in MODES:
        err("ASSET", f"asset_index.mode 非法: {ai.get('mode')!r}，应为 {sorted(MODES)}")
    else:
        ok(f"asset_index.mode = {ai['mode']}")
    layers = ai.get("layers") or []
    if not any(l.get("hard_constraint") is True for l in layers):
        warn("ASSET", "四层里没有标 hard_constraint:true —— 自有产品镜头是硬约束")
    if "vendor_assets" not in ai:
        err("ASSET", "asset_index 缺 vendor_assets（可为空数组，但不能缺）")

    # 10. 兜底禁词扫描（只扫会进文案的字段）
    scan: list[tuple[str, str]] = []
    collect_scan_text(sps, scan)
    collect_scan_text(els, scan)
    hits: list[str] = []
    for field, text in scan:
        for w in BANNED:
            if w in text:
                hits.append(f"{field}: {text[:34]}… 命中「{w}」")
    if hits:
        for h in hits[:12]:
            warn("BANNED", h)
        if len(hits) > 12:
            warn("BANNED", f"…另有 {len(hits)-12} 处")
    else:
        ok("兜底禁词扫描：无命中（仅扫可用说法与元素库文本）")

    # 11. 发布闸门
    review = meta.get("review") or {}
    if review.get("published_to_tdrive") is True and review.get("human_review") != "passed":
        err("PUBLISH", "published_to_tdrive=true 但 human_review 不是 passed —— 未经人审不得上传")
    if review.get("self_check") not in {"pending", "passed"}:
        warn("REVIEW", f"meta.review.self_check 值异常: {review.get('self_check')!r}")
    ok(f"发布状态：human_review={review.get('human_review')}, published_to_tdrive={review.get('published_to_tdrive')}")

    # 12. 体积 sanity check（防产物膨胀）
    kb = len(raw) / 1024
    if kb > 200:
        err("SIZE", f"产物 {kb:.1f} KB 过大 —— 检查是否把方法论/准则全文抄进了包")
    elif kb > 120:
        warn("SIZE", f"产物 {kb:.1f} KB 偏大，确认七库是否过度展开")
    else:
        ok(f"产物体积 {kb:.1f} KB")

    # 输出
    print(f"=== 品类包校验：{path.name} ===")
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
