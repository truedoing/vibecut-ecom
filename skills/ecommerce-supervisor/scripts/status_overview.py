#!/usr/bin/env python3
"""总台账现算：扫 runs/*/*/状态.json，输出每条线「演到哪 / 卡在哪 / 下一步能走哪」。

设计理由：不维护第二份台账文件（第二真源迟早过期）。几十条线之前，扫目录足够快。

流水线按 状态.json 的 line 字段分派（2026-11-08 起）：
  - 缺省 / "light-food"  → 轻食带货七段（兼容旧台账，旧文件一个字不用改）
  - "short-drama"        → 短剧二创：片级七段 + G0 品类级资产门禁
品类级资产不占片级 stage 位（11-06 实测：挂进片级会让每条线都误报「待人审」），
它们住在 g0_category_assets，由本脚本单独汇总并参与 G0 判断。

用法：
  python3 scripts/status_overview.py                     # 全部线
  python3 scripts/status_overview.py --root runs
  python3 scripts/status_overview.py --line short-drama  # 只看短剧线
  python3 scripts/status_overview.py --detail            # 展开品类级资产明细
  python3 scripts/status_overview.py --stale-only        # 只看要返工的
"""
import argparse
import json
from pathlib import Path

# 流水顺序只用于「报进度」和「推下一步」，不用于卡流程（不做工作流引擎）
LIGHT_FOOD_STAGES = ["structure", "pack", "script",
                     "hook_AI1_v1", "anchor", "narration", "final_mix"]

# 11-09 形态翻案：短剧线多出「电视框 AI 规格」这一环（场景图 + 提示词 + lint + dry-run）。
# 单独列 stages，不塞进轻食线——轻食线没有这个产物，塞进去会让每条轻食线误报「待建」。
SHORT_DRAMA_STAGES = ["structure", "pack", "script", "hook_frame_spec",
                      "hook_frame_video",  # 11-09 09:00 实际出片产物，人审前独立占位
                      "hook_AI1_v1", "anchor", "narration", "final_mix"]

PIPELINES = {
    "light-food": {
        "label": "轻食带货",
        "stages": LIGHT_FOOD_STAGES,
        "g0": False,
        "stage_labels": {},
    },
    "short-drama": {
        "label": "短剧二创",
        # 片级键名沿用轻食七段（下游技能还没拆，换名只会让旧状态文件失真）；
        # 语义差异靠 stage_labels 说明，不靠改键名。
        "stages": SHORT_DRAMA_STAGES,
        "g0": True,
        # script 开工前必须就绪的品类级资产。
        # 注意：这不是 G0 人审门禁（gate），是开工依赖 —— 两者是不同维度：
        # gate 管「人审能不能过」，script_inputs 管「这一环能不能动手」。
        "script_inputs": ["asset_index"],
        # 11-09 订正：这组标签还写着「交替解说」形态，形态翻案后已全部过期。
        # 旧文案保留在磨合记录第 13/14 环，这里改的是给下一环看的活口径。
        "stage_labels": {
            "structure": "结构＝电视框三拍（引/框/织）",
            "pack": "剧集包（G0 人审）",
            "script": "v4 执行单（正片零解说 + 顺叙轴/主题蒙太奇）",
            "hook_frame_spec": "电视框 AI 规格（场景图 + 提示词 + dry-run）",
            "hook_frame_video": "5s 电视框成片（出片后待人审）",
            "hook_AI1_v1": "10s 带声样片（现仅作音色锚来源）",
            "anchor": "王婶片内音色锚",
            "narration": "一句点题词（MiMo voiceclone）",
            "final_mix": "混剪成片（含屏幕贴图）",
        },
    },
}
DEFAULT_LINE = "light-food"

# 主表里品类资产的一格摘要用短名
G0_SHORT = {
    "drama_pack": "剧集包",
    "character_ch003": "人物卡",
    "asset_index": "抽帧索引",
    "twin_world_scene": "孪生场景",
}


def pipeline_of(d):
    return PIPELINES.get(d.get("line") or DEFAULT_LINE, PIPELINES[DEFAULT_LINE])


def usable(a):
    """ready / approved 都算机器可用；approved 才允许人依赖。"""
    return a.get("state") in ("ready", "approved")


def g0_items(d):
    """品类级资产：排除 note 等说明字段，只留 dict 项。"""
    return {k: v for k, v in (d.get("g0_category_assets") or {}).items()
            if isinstance(v, dict)}


def g0_hard_blockers(d):
    """硬门禁：gate=hard 且未 approved。gate 缺省 soft —— 硬卡必须显式声明。"""
    return [k for k, v in g0_items(d).items()
            if v.get("gate", "soft") == "hard" and v.get("state") != "approved"]


def g0_compact(d):
    items = g0_items(d)
    if not items:
        return "—"
    out = []
    for k, v in items.items():
        short = G0_SHORT.get(k, k)
        if v.get("state") == "approved":
            mark = "✓"
        elif v.get("state") in ("ready", "draft"):
            mark = "◐"          # 产物在、人没判 —— 和「没做」区分开
        elif v.get("gate", "soft") == "hard":
            mark = "✗"
        else:
            mark = "○"
        out.append(f"{short}{mark}")
    return " ".join(out)


def stage_of(arts, d=None):
    """最后一个 approved 的 artifact = 演到哪；全空则未开工。"""
    stages = pipeline_of(d or {})["stages"]
    done = [k for k in stages if arts.get(k, {}).get("state") == "approved"]
    return done[-1] if done else "未开工"


def blockers(arts, d=None):
    """硬卡优先；可延后的品类资产不进这里（G0 摘要列与明细已经显示）。"""
    out = []
    if d and pipeline_of(d)["g0"]:
        for x in (d.get("open_conflicts") or []):
            out.append(f"conflict:{x.get('id', '?')}")
        for k, v in g0_items(d).items():
            if v.get("gate", "soft") == "hard" and v.get("state") != "approved":
                out.append(f"gate:g0/{k}")
    _g0 = g0_items(d) if d and pipeline_of(d)["g0"] else {}
    for name, a in arts.items():
        # 「还没轮到」不等于「卡住」：上游没到位就标 blocked 的下游段，
        # 只是排在队里，不该进卡点列（否则每条的卡点列都会列一串下游）。
        waiting = [dep for dep in a.get("depends_on", [])
                   if not usable(arts.get(dep, {})) and not usable(_g0.get(dep, {}))]
        if a.get("state") == "blocked" and not waiting:
            out.append(f"blocked:{name}")
        if a.get("state") == "stale":
            out.append(f"stale:{name}")
    return out


def pending_review(arts, d=None):
    """机器过了、人还没点头的——这些是门禁停点。"""
    stages = pipeline_of(d or {})["stages"]
    return [k for k in stages if arts.get(k, {}).get("state") == "ready"]


def next_step(arts, d=None):
    """第一个还不可用的 artifact（依赖已可用），否则报待审。

    短剧线额外过一道 G0：包人审 + 品类级硬资产都 approved 才算过门。
    """
    d = d or {}
    cfg = pipeline_of(d)
    stages = cfg["stages"]
    hard = g0_hard_blockers(d) if cfg["g0"] else []

    pack = arts.get("pack", {})
    if cfg["g0"] and pack.get("state") == "approved" and hard:
        return f"待品类资产 {','.join(hard)}"
    # 包过了 G0、但包里还有未决冲突 → 下一步是「拍板」，不是开工
    # （多来源冲突不自动选值，技能层如此，台账层也必须如此）
    oc = d.get("open_conflicts") or []
    if cfg["g0"] and oc and pack.get("state") == "approved":
        ids = ",".join(x.get("id", "?") for x in oc)
        return f"待解冲突 {ids}（交人定，不自动选值）"

    for k in stages:
        a = arts.get(k)
        if a is None:
            return f"待建 {k}"
        if a.get("state") == "approved":
            continue
        # 品类级开工依赖：素材没分析清楚，执行单就不能动笔
        # （11-08 实跑：包里 hook_map 没有画面描述，硬写执行单只够用 70%，
        #   一条片子要回原片看 5 次；双模式后还要找两套插解说位置，更离不开索引）
        if k == "script" and cfg.get("script_inputs"):
            g0 = g0_items(d)
            missing = [n for n in cfg["script_inputs"] if not usable(g0.get(n, {}))]
            if missing:
                names = ",".join(G0_SHORT.get(n, n) for n in missing)
                return f"先建品类资产 {names}（{k} 上游）"
        # depends_on 里可能写品类级资产名（如 asset_index）。它们住在
        # g0_category_assets，不在 arts 里 —— 只查 arts 会永远误报「等上游」。
        # 11-08 实跑发现：asset_index 已 ready，台账仍报「等上游 asset_index」。
        _g0 = g0_items(d) if cfg["g0"] else {}
        unmet = [dep for dep in a.get("depends_on", [])
                 if not usable(arts.get(dep, {})) and not usable(_g0.get(dep, {}))]
        if unmet:
            return f"等上游 {','.join(unmet)}"
        if a.get("state") == "ready":
            if k == "pack" and cfg["g0"] and hard:
                return f"待人审 pack（+品类资产 {','.join(hard)} 未就绪）"
            return f"待人审 {k}"     # ready = 机器可用、人没点头，不能跳过
        # 11-09：上游 rev 变了导致 stale 时，「可开工」会让人误以为旧产物还能用。
        # stale 的语义是「重跑」，不是「开工」——第 13 环记过这条，这里补到代码里。
        if a.get("state") == "stale":
            return f"需重跑 {k}（上游已变）"
        return f"可开工 {k}（{a.get('state')}）"
    pend = pending_review(arts, d)
    return f"待人审 {','.join(pend)}" if pend else "已交付"


def print_g0_details(rows_data, detail=False):
    """短剧线品类级资产明细：默认打简要，--detail 展开证据。"""
    items = [(name, d) for name, d in rows_data if pipeline_of(d)["g0"] and g0_items(d)]
    if not items:
        return
    print()
    print("--- 短剧线品类级资产（G0，不占片级 stage 位） ---")
    for name, d in items:
        print(f"  {d.get('category', '?')}")
        for k, v in g0_items(d).items():
            st = v.get("state", "?")
            gate = v.get("gate", "soft")
            due = v.get("due") or ""
            label = G0_SHORT.get(k, k)
            flag = "硬卡" if gate == "hard" else "可延后"
            tail = f" · {due}" if due else ""
            print(f"    {label:<10} {st:<10} [{flag}]{tail}")
            if detail:
                if v.get("note"):
                    print(f"      note: {v['note']}")
                for e in v.get("evidence", []) or []:
                    print(f"      ✓ {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="runs")
    ap.add_argument("--line", help="只看某条产线（如 short-drama / light-food）")
    ap.add_argument("--detail", action="store_true", help="展开品类级资产的证据与备注")
    ap.add_argument("--stale-only", action="store_true")
    args = ap.parse_args()

    files = sorted(Path(args.root).glob("*/*/状态.json"))
    if not files:
        print(f"{args.root} 下没有找到 状态.json")
        return

    rows, rows_data = [], []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            rows.append((str(f), "?", "解析失败", str(e)[:30], "—", "解析失败", "-"))
            continue
        if args.line and (d.get("line") or DEFAULT_LINE) != args.line:
            continue
        arts = d.get("artifacts", {})
        cfg = pipeline_of(d)
        b = blockers(arts, d)
        # 下游全是「等上游」的自动等待，列 5 个没信息量：只报首个 + 数量
        btxt = "—" if not b else (b[0] if len(b) == 1 else f"{b[0]} +{len(b)-1}")
        rows.append((f"{d.get('category','?')}/{d.get('task', f.parent.name)}",
                     cfg["label"], stage_of(arts, d), btxt,
                     g0_compact(d), next_step(arts, d), d.get("updated_at", "-")))
        rows_data.append((d.get("category", "?"), d))

    if args.stale_only:
        rows = [r for r in rows if r[3] != "—"]
    if not rows:
        print(f"没有匹配的线（line={args.line or '全部'}）")
        return

    w = max(len(r[0]) for r in rows) + 2
    print(f"{'线'.ljust(w)} {'线型':<10} {'演到哪':<14} {'卡点':<26} {'G0品类资产':<30} {'下一步'}")
    print("-" * (w + 10 + 14 + 26 + 30 + 28))
    for r in rows:
        print(f"{r[0].ljust(w)} {r[1]:<10} {r[2]:<14} {r[3]:<26} {r[4]:<30} {r[5]}")

    if not args.stale_only:
        print_g0_details(rows_data, args.detail)


if __name__ == "__main__":
    main()
