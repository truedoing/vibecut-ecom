#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短剧线状态总览（short-drama-supervisor 专用，v0.3 三层结构）。

与旧电商主管的 status_overview.py 完全独立，不复用旧 stages：
旧表里短剧 stages 还是 11-09 电视框三拍（hook_frame_spec / hook_frame_video / 一句点题词），
会把已废弃产物报成「可开工」。本脚本只认 v0.2 的七个环节。

核心修复：**要重做是算出来的，不是人写进状态文件的。**
1) 文件内：每个产物带 depends_on + depends_rev（建它时的版本快照），
   只要上游版本变了，下游立刻被判要重做，一条链自动传下去。
2) 跨文件：工作台 shared 记共用层当前版本，每条片子记 shared_snapshot，
   共用层一改，只把用到它的片子标出来（剧包/玩法/角色资产各管各的范围）。

旧台账没有 shared_snapshot、或工作台不存在时，行为与 11-10 验收版本完全一致。

用法：
  python3 status_overview.py                          # 扫 runs/ 下所有短剧状态文件
  python3 status_overview.py --root runs --detail     # 展开每个产物的明细
  python3 status_overview.py --stale-only             # 只看要返工的
"""
import argparse
import json
from pathlib import Path

# v0.2 链路：选片 → 建包 → 素材索引 → 角色资产(可选) → 编排 → 素材段(可选) → 装配
STAGES = ["select", "pack", "asset_index", "ai_assets",
          "script", "weave_video", "trailer"]
STAGE_LABELS = {
    "select": "选片（做 / 不做）",
    "pack": "建包（剧集资料，G0 人审）",
    "asset_index": "素材索引 / 台词库",
    "ai_assets": "玩法要用的角色资产（可选）",
    "script": "方向卡 + 剪辑清单（G1 人审）",
    "weave_video": "按玩法生成的素材段（可选，G2 验收）",
    "trailer": "装配成片（G3 成片人审）",
}
OPTIONAL = {"ai_assets", "weave_video"}
DONE = {"done", "approved"}
SKIP = {"superseded", "blocked"}
VALID = {"pending", "in_progress", "done", "approved", "stale", "superseded", "blocked"}

# 11-09 旧形态键名：遇到就明确报「不迁移」，不拿旧键当进度
LEGACY_KEYS = {"structure", "hook_frame_spec", "hook_frame_video",
               "hook_AI1_v1", "anchor", "narration", "final_mix"}


def iter_status(root):
    for p in sorted(Path(root).glob("**/状态.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[跳过] {p}: {e}")
            continue
        # 只认短剧线。旧电商主管用「缺省=轻食」兼容老台账，本主管不背这个兼容：
        # 无 line 字段的轻食/图书状态文件一律不扫，避免把别线的产物报成短剧进度。
        if d.get("line") != "short-drama":
            continue
        yield p, d


def drama_of(rel_path):
    """runs/<剧名>/<任务>/状态.json → <剧名>。"""
    parts = rel_path.parts
    return parts[0] if parts else ""


def load_workspace(root):
    """读 runs/工作台.json：当前选中对象 + 共用层当前版本。缺了就按旧口径跑。"""
    p = Path(root) / "工作台.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[提示] 工作台读取失败，只算文件内依赖：{e}")
        return {}


def shared_reasons(doc, workspace, drama):
    """共用层改版 → 这条片子哪些要重看。只读派生，不改状态文件。

    影响范围在这里一次算全，不再跑通用传播，避免把「改清单」误传成「素材段要重做」——
    出片是唯一花钱的环节，能复用就复用。
    """
    snap = doc.get("shared_snapshot") or {}
    shared = workspace.get("shared") or {}
    if not snap or not shared:
        return {}
    out = {}

    def add(key, msg):
        out.setdefault(key, []).append(f"共用层：{msg}")

    # 剧包 / 素材索引：只影响本剧的清单与成片，不牵连别剧，也不重做素材段
    base_now = (shared.get("drama_base") or {}).get(drama) or {}
    base_old = snap.get("drama_base") or {}
    for k, label in (("pack", "剧集包"), ("asset_index", "素材索引")):
        if k in base_now and k in base_old and base_now[k] != base_old[k]:
            add("script", f"本剧{label}版本 {base_old[k]}→{base_now[k]}，剪辑清单要重看")
            add("trailer", "清单已重看，成片要重拼（已生成的素材段可复用）")

    # 玩法配方 / 共用角色资产：只影响点了这道玩法的片子
    strategy = doc.get("ai_strategy") or "none"
    if strategy != "none":
        now = (shared.get("ai_strategies") or {}).get(strategy) or {}
        old = (snap.get("ai_strategies") or {}).get(strategy)
        if isinstance(now, dict) and now.get("rev") is not None and old is not None \
                and now["rev"] != old:
            add("weave_video", f"玩法 {strategy} 配方版本 {old}→{now['rev']}，素材段要重做")
            add("trailer", "素材段重做后成片要重拼")
        assets_now = (shared.get("ai_assets") or {}).get("rev")
        assets_old = snap.get("ai_assets")
        if assets_now is not None and assets_old is not None and assets_now != assets_old:
            add("weave_video", f"共用角色资产版本 {assets_old}→{assets_now}，素材段要重做")
            add("trailer", "素材段重做后成片要重拼")
    return out


def strategy_warnings(doc, workspace):
    """片子点了玩法但菜单没登记齐 → 明说，别静默跳过配方改版的传播。

    实测（11-10 22:1x）：新玩法没登记、没写版本、或片子没记建它时的版本时，
    shared_reasons 一路静默，配方改了也不报「要重做」。这类缺登记只提醒，
    不混进要重做列表（不改「下一步」，也不触发返工流程）。
    """
    strategy = doc.get("ai_strategy") or "none"
    if strategy == "none":
        return []
    shared = workspace.get("shared") or {}
    if not shared:
        return []
    entry = (shared.get("ai_strategies") or {}).get(strategy)
    if entry is None:
        return [f"点了玩法 {strategy}，工作台菜单里却没有它；配方改版不会被发现，请先登记"]
    if not isinstance(entry, dict) or entry.get("rev") is None:
        return [f"玩法 {strategy} 在菜单里没写版本；配方改版不会被发现，请补 rev"]
    old = ((doc.get("shared_snapshot") or {}).get("ai_strategies") or {}).get(strategy)
    if old is None:
        return [f"这条片子没记建它时玩法 {strategy} 的版本；配方改版不会被发现，请补 shared_snapshot"]
    return []


def rev_of(art):
    return art.get("rev") if isinstance(art, dict) else None


def propagate(reasons, artifacts, skipped=()):
    """被判失效的产物，其下游一并失效，直到收敛。"""
    changed = True
    while changed:
        changed = False
        for key, art in artifacts.items():
            if key in skipped:
                continue
            if not isinstance(art, dict) or art.get("state") in SKIP or key in reasons:
                continue
            for dep in art.get("depends_on") or []:
                if dep in reasons:
                    reasons[key] = [f"上游 {dep} 已失效（依赖链传播）"]
                    changed = True
                    break
    return reasons


def stale_reasons(artifacts, skip_optional=False):
    """返回 {产物名: [作废原因]}。只读派生，不改状态文件。

    11-11 修第 40 环登记的结构问题：明确写了「纯剪」的片子，角色资产 / 素材段两环整环跳过、
    零花费，改剪辑清单不该把它们报成「要重做」。只认**明确写 ai_strategy == "none"** 的台账；
    旧台账没写这个字段时不跳过，避免把 D5 那种有织入版的老片子误当纯剪。
    """
    reasons = {}
    skipped = OPTIONAL if skip_optional else set()
    for key, art in artifacts.items():
        if key in skipped:
            continue
        if not isinstance(art, dict) or art.get("state") in SKIP:
            continue
        rs = []
        deps = art.get("depends_on") or []
        snap = art.get("depends_rev") or {}
        if art.get("state") == "stale" and not deps:
            rs.append("状态文件手写 stale（旧口径；建议补 depends_on 交给脚本判）")
        for dep in deps:
            target = artifacts.get(dep)
            if not isinstance(target, dict):
                rs.append(f"依赖 {dep} 不存在")
                continue
            if target.get("state") == "superseded":
                rs.append(f"依赖 {dep} 已废弃")
                continue
            cur = rev_of(target)
            old = snap.get(dep) if isinstance(snap, dict) else snap
            if cur is None:
                rs.append(f"依赖 {dep} 无 rev，无法校验")
            elif old is None:
                rs.append(f"缺 depends_rev 快照（{dep} 当前 rev={cur}），无法校验")
            elif cur != old:
                rs.append(f"依赖 {dep} rev {old}→{cur}")
        if rs:
            reasons[key] = rs

    # 传递闭包：三测试要求「改 pack rev 后下游一条链全标 stale」，只比一跳不够。
    return propagate(reasons, artifacts, skipped)


def next_step(artifacts, reasons):
    if reasons:
        return "返工：" + "、".join(sorted(reasons))
    # 从「最新已完成的环节」往后找，不能从头扫——否则成片都出了还会报「可开工选片」。
    last_done = -1
    for i, s in enumerate(STAGES):
        art = artifacts.get(s)
        if isinstance(art, dict) and art.get("state") in DONE:
            last_done = i
    for s in STAGES[last_done + 1:]:
        art = artifacts.get(s)
        if s in OPTIONAL:
            continue
        if isinstance(art, dict) and art.get("state") in SKIP:
            continue
        if isinstance(art, dict) and art.get("state") not in DONE:
            return f"继续 {s}（{STAGE_LABELS[s]}，当前 {art.get('state')}）"
        return f"可开工 {s}（{STAGE_LABELS[s]}）"
    return "全线完成，等人审 / 新一批"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="runs")
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--stale-only", action="store_true")
    a = ap.parse_args()

    ws = load_workspace(a.root)
    active = ws.get("active") or {}
    if active and not a.stale_only:
        print(f"当前工作对象：{active.get('drama', '?')} / {active.get('task', '?')}"
              "（不指定就沿用；换剧只改工作台，历史不删）")

    found = 0
    for p, d in iter_status(a.root):
        artifacts = d.get("artifacts") or {}
        rel = p.relative_to(Path(a.root))
        drama = drama_of(rel)
        legacy = sorted(set(artifacts) & LEGACY_KEYS)
        if legacy and a.stale_only:
            continue  # 旧形态不是依赖卡点，别混进「只看卡点」视图
        skip_optional = d.get("ai_strategy") == "none"
        reasons = stale_reasons(artifacts, skip_optional)
        # 共用层影响范围由 shared_reasons 一次算全，不再跑通用传播（省钱规矩）
        for k, rs in shared_reasons(d, ws, drama).items():
            reasons.setdefault(k, []).extend(rs)
        warnings = strategy_warnings(d, ws)
        if a.stale_only and not reasons and not warnings:
            continue
        found += 1
        is_active = (active.get("drama") == drama and active.get("task") == p.parent.name)
        print(f"\n=== {rel} ===" + ("  [当前]" if is_active else ""))
        if legacy:
            print(f"  [旧形态] 含 11-09 电视框三拍键名 {legacy}；本主管按 v0.2 只认新键名，"
                  "不迁移、不计入进度，也不拿旧依赖报 stale。")
            continue
        must = [s for s in STAGES if s not in OPTIONAL]
        done = [s for s in must
                if isinstance(artifacts.get(s), dict)
                and artifacts[s].get("state") in DONE]
        unlogged = [s for s in must if not isinstance(artifacts.get(s), dict)]
        print(f"  演到哪  {done[-1] if done else '未开工'}"
              f"（已完成 {len(done)}/{len(must)} 个必选环节）")
        print(f"  AI 玩法 {d.get('ai_strategy') or 'none'}"
              f"（不写＝纯剪）   循环轮次 {d.get('cycle_round') or 1}")
        if skip_optional:
            print("            （纯剪：角色资产 / 素材段整环跳过，改清单不牵动它们）")
        for w in warnings:
            print(f"  [提醒]  {w}")
        if unlogged and done:
            print(f"  未登记  {', '.join(unlogged)}（旧批次可能没记，不倒推为待办）")
        if reasons:
            print("  卡点    以下产物要重做：")
            for k, rs in sorted(reasons.items()):
                print(f"            - {k}: {'; '.join(rs)}")
            if any("素材段可复用" in x for rs in reasons.values() for x in rs):
                print("            * 改清单不等于重做出片：素材段留着换插入位，别重跑花钱那步")
        else:
            print("  卡点    无")
        print(f"  下一步  {next_step(artifacts, reasons)}")
        if a.detail:
            for k, art in sorted(artifacts.items()):
                if not isinstance(art, dict):
                    continue
                state = art.get("state")
                flag = ""
                if state not in VALID:
                    flag = "  ⚠️ 状态值不在定义域内"
                if state == "superseded":
                    flag += "  (已废弃，不参与调度)"
                print(f"            {k:<16} {str(state):<12} "
                      f"rev={rev_of(art)} depends_on={art.get('depends_on') or []}{flag}")
    if not found:
        print("（没有需要返工的短剧状态文件）" if a.stale_only
              else "（没有匹配的短剧状态文件）")


if __name__ == "__main__":
    main()
