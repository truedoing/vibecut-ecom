#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短剧线状态总览（short-drama-supervisor 专用，按纯剪预告 v0.2 形态重写）。

与旧电商主管的 status_overview.py 完全独立，不复用旧 stages：
旧表里短剧 stages 还是 11-09 电视框三拍（hook_frame_spec / hook_frame_video / 一句点题词），
会把已废弃产物报成「可开工」。本脚本只认 v0.2 的七个环节。

核心修复：**stale 是算出来的，不是人写进状态文件的。**
每个产物带 depends_on（依赖谁）+ depends_rev（建它时各依赖的 rev 快照）。
只要上游 rev 变了，下游立刻被判 stale，一条链自动传下去，不靠谁记得。

用法：
  python3 status_overview.py                          # 扫 runs/ 下所有短剧状态文件
  python3 status_overview.py --root runs --detail     # 展开每个产物的明细
  python3 status_overview.py --stale-only             # 只看要返工的
"""
import argparse
import json
from pathlib import Path

# v0.2 链路：选品 → 建包 → 素材索引 → 角色资产(可选) → 编排 → 织入片(可选) → 装配
STAGES = ["select", "pack", "asset_index", "ai_assets",
          "script", "weave_video", "trailer"]
STAGE_LABELS = {
    "select": "选品（GO / NO-GO）",
    "pack": "剧集包（G0 人审）",
    "asset_index": "素材索引 / 台词库",
    "ai_assets": "王婶角色资产（可选）",
    "script": "编排 plan.json（G1 段表人审）",
    "weave_video": "王婶织入片（可选，G2 出片验收）",
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


def rev_of(art):
    return art.get("rev") if isinstance(art, dict) else None


def stale_reasons(artifacts):
    """返回 {产物名: [作废原因]}。只读派生，不改状态文件。"""
    reasons = {}
    for key, art in artifacts.items():
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

    # 传递闭包：被判失效的产物，其下游一并失效，直到收敛。
    # 三测试要求的是「改 pack rev 后下游一条链全标 stale」，只比一跳不够。
    changed = True
    while changed:
        changed = False
        for key, art in artifacts.items():
            if not isinstance(art, dict) or art.get("state") in SKIP or key in reasons:
                continue
            for dep in art.get("depends_on") or []:
                if dep in reasons:
                    reasons[key] = [f"上游 {dep} 已失效（依赖链传播）"]
                    changed = True
                    break
    return reasons


def next_step(artifacts, reasons):
    if reasons:
        return "返工：" + "、".join(sorted(reasons))
    # 从「最新已完成的环节」往后找，不能从头扫——否则成片都出了还会报「可开工选品」。
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

    found = 0
    for p, d in iter_status(a.root):
        artifacts = d.get("artifacts") or {}
        legacy = sorted(set(artifacts) & LEGACY_KEYS)
        if legacy and a.stale_only:
            continue  # 旧形态不是依赖卡点，别混进「只看卡点」视图
        reasons = stale_reasons(artifacts)
        if a.stale_only and not reasons:
            continue
        found += 1
        rel = p.relative_to(Path(a.root))
        print(f"\n=== {rel} ===")
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
        if unlogged and done:
            print(f"  未登记  {', '.join(unlogged)}（旧批次可能没记，不倒推为待办）")
        if reasons:
            print("  卡点    依赖变更，以下产物应判 stale：")
            for k, rs in sorted(reasons.items()):
                print(f"            - {k}: {'; '.join(rs)}")
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
