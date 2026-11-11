#!/usr/bin/env python3
"""把 asset_index.json v2 渲染成人看的素材台账（md）。

一份 JSON 是机读真源，md 只是它的渲染视图 —— 不新增判断，只换排版。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

STATE_ICON = {"临界": "◐", "已兑现": "●", "铺垫": "○"}


def line_lookup(base: Path, ep: int, cache: dict) -> dict:
    """台词文字的唯一真源在 lines/lines_epNN.json，这里只按需取，不另存一份。"""
    if ep not in cache:
        d = json.loads((base / "lines" / f"lines_ep{ep:02d}.json").read_text(encoding="utf-8"))
        arr = d["lines"] if isinstance(d, dict) else d
        cache[ep] = {x["t"]: str(x.get("text", "")).strip() for x in arr}
    return cache[ep]


def seg_times(s: dict) -> list:
    """兼容 11-10 之前的旧格式（段内直接嵌 lines 文字），但不再写出那种格式。"""
    return s.get("line_times") or [x["t"] for x in s.get("lines", [])]


def main() -> int:
    root = Path(__file__).resolve().parents[3]
    base = root / "assets/short-drama/八零憨妻的甜蜜日常"
    d = json.loads((base / "asset_index.json").read_text(encoding="utf-8"))
    line_cache: dict = {}

    st = d["stats"]
    lines: list[str] = []
    A = lines.append

    A(f"# 素材台账 · {d['category']}")
    A("")
    A("> 由 `asset_index.json` 渲染，**以 JSON 为准**；本文件只是给人看的视图。")
    A("> 台词文字取自 `lines/lines_epNN.json`（唯一真源），本表只显示、不另存一份。")
    A(">")
    A("> ⚠️ **旧做法提醒（2026-11-10 补）**：本表里「王婶怎么进 / 王婶的开口方式 / 插在哪」"
      "是 11-08 那套「让虚构的王婶在旁边接话」留下的，属于**历史字段**，正文原文保留备查，")
    A("> **别照着去插王婶**：现在默认纯剪原片，王婶只是 AI 玩法菜单里的一道菜，"
      "要由每条片子的选题单点选才用。")
    A("> `space` / `exit_type` 这两列的数据本身仍然有效——纯剪也要判断这段切不切得出去。")
    A(">")
    A(f"> 覆盖 1–{max(d['scope']['covered'])} 集（第 20 集大结局按任务要求第 3 条排除）｜"
      f"{st['total_seconds']/60:.1f} 分钟素材")
    A("")
    A("## 一句话结论")
    A("")
    pool = d["anchor_pool"]
    enough = [t for t, m in d["themes"].items() if m["enough_for_one_episode"]]
    A(f"**{st['segments']} 个冲突段落 / {st['anchors']} 个锚点 / "
      f"{len(enough)} 个主题够撑一条片子。**")
    A("")
    A(f"锚点里 `临界` {st['by_state']['临界']} 条（可做钩子和截断），"
      f"`已兑现` {st['by_state']['已兑现']} 条（可做举证和收束）。"
      f"按每条片子用 3–4 个锚点算，上限约 {st['anchors']//3.5:.0f} 条，"
      f"但主题交叉会重复取材，稳妥产能是 {len(enough)}–{len(enough)+2} 条。")
    A("")
    A("## 状态口径（这套索引最要紧的三个词）")
    A("")
    A("| 状态 | 含义 | 拿来干吗 |")
    A("|---|---|---|")
    A("| ◐ 临界 | 结果还没出现那一瞬 | 做开头钩子、做截断点。**不泄气** |")
    A("| ● 已兑现 | 结果已经落地 | 做举证、做收束。放开头会替观众爽完 |")
    A("| ○ 铺垫 | 没有对抗，交代信息或过渡 | 只当缝合，不进锚点 |")
    A("")
    be = st.get("by_exit", {})
    A("## 二扩点分布（97 段边界帧实测）")
    A("")
    A("王婶不进原片画面，她是另一个镜头。所以真正要问的不是「同不同框」，"
      "而是**这段最后能不能切出去**。")
    A("")
    A("| 出口 | 段数 | 含义 | 王婶怎么进 |")
    A("|---|---|---|---|")
    A(f"| 人出画 | {be.get('人出画',0)} | 走远、出门、走过，有明确方向 | ✅ 顺着方向二扩 |")
    A(f"| 边界物 | {be.get('边界物',0)} | 门洞、路口、墙头、田埂在画面里 | ✅ 切到对面空间 |")
    A(f"| 自顾 | {be.get('自顾',0)} | 单人在画面内做自己的事 | ✅ 切到同类动作，最自然 |")
    A(f"| 空镜 | {be.get('空镜',0)} | 画面无人物 | ✅ 最自由 |")
    A(f"| 锁空间 | {be.get('锁空间',0)} | 双人对话、正反打、面部特写 | ❌ 只能画外音插话或段前立人 |")
    A("")
    A(f"**可二扩 {st.get('expandable', 0)} 段，占 "
      f"{st.get('expandable',0)/max(st['segments'],1)*100:.0f}%；"
      f"锁空间 {be.get('锁空间',0)} 段，占 {be.get('锁空间',0)/max(st['segments'],1)*100:.0f}%。**")
    A("")
    A("这条比预想的要紧：**六成段落切不出去**。所以王婶不能只靠「段落后二扩」，"
      "必须同时用「开头立人」和「中间画外音插话」——四个策略是一个组合，不是四选一。")
    A("")
    A("## 主题 → 能出几条")
    A("")
    A("| 主题 | 一句话看法 | 锚点 | 跨集 | 判定 |")
    A("|---|---|---|---|---|")
    for tid, m in d["themes"].items():
        verdict = "✅ 可单出一条" if m["enough_for_one_episode"] else "⚠️ 只当段落用"
        A(f"| {tid} {m['name']} | {m['claim']} | {m['anchor_count']} | "
          f"{m['ep_spread']} | {verdict} |")
    A("")
    A("> 王婶的开口方式（每个主题一句话，直接可当第一句）：")
    for tid, m in d["themes"].items():
        A(f"> - **{m['name']}**：{m['voice']}")
    A("")
    A("## 锚点池（按主题分组）")
    A("")
    for tid, m in d["themes"].items():
        A(f"### {tid} {m['name']}")
        A("")
        A(f"*{m['claim']}*")
        A("")
        A("| 锚点 | 集/秒 | 角色 | 强度 | 场景·出口 | 王婶能怎么进 | 为什么 |")
        A("|---|---|---|---|---|---|---|")
        for a in pool:
            if tid not in a["themes"]:
                continue
            role = {"hook": "钩子", "payoff": "收束", "insert": "插入"}[a["anchor_role"]]
            ins = ("二扩 / 插话 / 立人" if "场景二扩" in a.get("insert_options", [])
                   else "插话 / 立人（切不出去）")
            A(f"| {a['id']} | E{a['ep']} {a['start']:.0f}s | {STATE_ICON[a['critical_state']]} "
              f"{a['critical_state']}·{role} | {a['intensity']} | "
              f"{a.get('space','—')}·{a.get('exit_type','—')} | {ins} | {a['why']} |")
        A("")
    A("## 逐集段落全表")
    A("")
    A("| 段 | 秒 | 场景 | 出口 | 冲突 | 强度 | 状态 | 主题 | 视角 | 台词起头 |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for e in d["episodes"]:
        for s in e["segments"]:
            times = seg_times(s)
            first = "—"
            if times:
                first = line_lookup(base, e["ep"], line_cache).get(times[0], "—").replace("|", "／")[:22]
            mark = "◐" if s["critical_state"] == "临界" else ("●" if s["critical_state"] == "已兑现" else "○")
            th = "/".join(s["themes"])
            A(f"| {s['id']} | {s['start']:.0f}–{s['end']:.0f} | {s.get('space','—')} | "
              f"{s.get('exit_type','—')} | {s['conflict']['type']} | "
              f"{s['conflict']['intensity']} | {mark}{s['critical_state']} | {th} | {s['viewpoint']} | {first} |")
    A("")
    A("## 还没做的（别当已完成）")
    A("")
    for x in d["pending"]:
        A(f"- {x}")
    A("")
    A(f"> 状态：{d['status']}")

    out = base / "素材台账.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写 {out}（{len(lines)} 行）")

    missing = []
    for e in d["episodes"]:
        owned = {t for s in e["segments"] for t in seg_times(s)}
        for t in sorted(set(line_lookup(base, e["ep"], line_cache)) - owned):
            missing.append(f"ep{e['ep']:02d} t={t}")
    if missing:
        print(f"提示：台词库有 {len(missing)} 句未归入任何段落（补句后请把秒数补进对应段的 line_times）："
              f"{', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
