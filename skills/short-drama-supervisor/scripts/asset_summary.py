#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""素材分析的「概况」：耗时、用量、数据质量分。

为什么要它（11-13 他提的）：
素材分析页原来只有逐集明细，看不出「这批数据到底能不能直接用」。
30 部剧的时候，人需要的是一个能扫的数字，不是 30 张明细表。

**质量分是给排片用的，不是给评分用的**：分数低说明「挑台词之前得人看」，
不代表分析失败。所以每一维都必须说清是哪里扣的，不许只甩一个总数。

维度与权重写死在 DIMS 里，改动要连算法和 rule 文字一起改——
**分数不透明就等于没有分数**，没人会信一个不知道该往哪补的分。

算法（每一维都先算 0~1 的达成率，再乘权重）：
  集覆盖     25  已分析集数 ÷ 全剧集数，到 60% 算满分（60% 是他定的够用线）
  台词完整   15  有台词库的集数 ÷ 已分析集数
  认得出谁   25  能定到人物的句子 ÷ 总句数
  情绪标注   15  带情绪的句子 ÷ 总句数
  镜头切干净 20  一镜凑 4 句以上的处数占比：≤2% 满分，≥10% 零分，中间线性
"""
from __future__ import annotations

import json
from pathlib import Path

# 百万 token 的价钱（元）。这是按「10 秒 480p 视频约 2 元」倒推的图生成模型单价，
# 只用来说明量级，页面必须标「估算」。
PRICE_PER_MTOK = 2.0

DIMS = [
    {"key": "cover", "label": "集覆盖", "weight": 25,
     "rule": "已分析的集数 ÷ 全剧集数，到 60% 算满分"},
    {"key": "lines", "label": "台词完整", "weight": 15,
     "rule": "有台词库的集数 ÷ 已分析集数"},
    {"key": "named", "label": "认得出谁说的", "weight": 25,
     "rule": "能定到具体人物的句子 ÷ 总句数"},
    {"key": "emotion", "label": "情绪标注", "weight": 15,
     "rule": "带情绪的句子 ÷ 总句数"},
    {"key": "cuts", "label": "镜头切干净", "weight": 20,
     "rule": "一镜凑 4 句以上的处数占比：2% 以内满分，10% 以上零分"},
]

LEVELS = [
    (85, "好", "挑台词可以直接按人名筛，个别处看一眼"),
    (70, "可用", "能用，但关键场面挑之前要抽查几句"),
    (50, "勉强", "先补最弱的那一项，否则会挑错人"),
    (0, "不够", "还没到能挑台词的程度"),
]

# 镜头切分的满分/零分线（占比）
CUT_OK, CUT_BAD = 0.02, 0.10


def _pct(a: float, b: float) -> float:
    return round(a / b * 100, 1) if b else 0.0


def human_sec(sec: float | None) -> str:
    """把秒说成人话：42 秒 / 3 分 20 秒 / 1 小时 2 分。"""
    if sec is None:
        return "—"
    s = int(round(sec))
    if s < 60:
        return f"{s} 秒"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} 分 {s} 秒" if s else f"{m} 分"
    h, m = divmod(m, 60)
    return f"{h} 小时 {m} 分"


def build(drama: str, eps: list[dict], video_count: int, cover_pct: float,
          log_bundle: dict | None = None, tokens: int = 0,
          price: float = PRICE_PER_MTOK) -> dict:
    """算概况。eps 是 api_assets 已经整理好的逐集列表。"""
    n_ep = len(eps)
    lines_eps = sum(1 for e in eps if e.get("has_lines"))
    sent = sum(e.get("sentence_count") or 0 for e in eps)
    named = sum(e.get("named_count") or 0 for e in eps)
    emo = sum(e.get("emotion_count") or 0 for e in eps)
    dense = sum(e.get("dense_shots") or 0 for e in eps)
    shots = sum(e.get("shot_count") or 0 for e in eps)

    lines_total = sum(e.get("line_count") or 0 for e in eps)

    # ---- 五维达成率（0~1）。算不出来的维度给 0 分，另标 done=False，
    # 因为「这一步还没做」本身就是没做到，不能当「数据缺失」放过——
    # 试算时吃过这个亏：只建了台词库的剧被折算成 100 分「好」。
    rates: dict[str, float] = {}
    done: dict[str, bool] = {}
    notes: dict[str, str] = {}

    rates["cover"] = min(1.0, (cover_pct / 100) / 0.6) if video_count else 0.0
    done["cover"] = n_ep > 0
    notes["cover"] = (f"已分析 {n_ep} 集 / 全剧 {video_count} 集 = {cover_pct}%，"
                      f"够用线 60%")

    rates["lines"] = (lines_eps / n_ep) if n_ep else 0.0
    done["lines"] = n_ep > 0
    notes["lines"] = f"台词库 {lines_eps} 集 / 已分析 {n_ep} 集" + \
        ("" if lines_eps == n_ep else "，有集缺台词")

    if sent:
        rates["named"] = named / sent
        notes["named"] = f"{named} 句能定到人 / 共 {sent} 句"
    else:
        rates["named"] = 0.0
        notes["named"] = "还没做台词与画面对齐，不知道每句是谁说的"
    done["named"] = bool(sent)

    if sent:
        rates["emotion"] = emo / sent
        notes["emotion"] = f"{emo} 句有情绪 / 共 {sent} 句"
    else:
        rates["emotion"] = 0.0
        notes["emotion"] = "还没做台词与画面对齐，没有情绪标注"
    done["emotion"] = bool(sent)

    if sent:
        rate = dense / sent
        # ≤2% 满分，≥10% 零分，中间线性；一刀切太糙，也免得一处漏切就把分打到底
        if rate <= CUT_OK:
            rates["cuts"] = 1.0
        elif rate >= CUT_BAD:
            rates["cuts"] = 0.0
        else:
            rates["cuts"] = 1 - (rate - CUT_OK) / (CUT_BAD - CUT_OK)
        notes["cuts"] = f"{dense} 处一镜凑 4 句以上 / {sent} 句 = {rate * 100:.1f}%"
    else:
        rates["cuts"] = 0.0
        notes["cuts"] = "还没做台词与画面对齐，查不出镜头切漏"
    done["cuts"] = bool(sent)

    dims, total = [], 0.0
    for d in DIMS:
        r = rates[d["key"]]
        got = round(r * d["weight"], 1)
        total += got
        dims.append({
            "key": d["key"], "label": d["label"], "weight": d["weight"],
            "rule": d["rule"],
            "rate": round(r, 3),
            "done": done[d["key"]],
            "rate_label": f"{r * 100:.0f}%" if done[d["key"]] else "没做",
            "score": got, "max": d["weight"],
            "note": notes.get(d["key"], ""),
        })

    score = round(total, 1)
    level, level_hint = next(((name, hint) for t, name, hint in LEVELS if score >= t),
                             ("不够", ""))

    # 最该补的一维：按「丢了多少分」排序，不是按达成率——权重不同，同样达成率意义不同
    lost = sorted(dims, key=lambda d: d["max"] - d["score"], reverse=True)
    weakest = None
    if lost and lost[0]["max"] - lost[0]["score"] >= 2:
        weakest = {"key": lost[0]["key"], "label": lost[0]["label"],
                   "lost": round(lost[0]["max"] - lost[0]["score"], 1),
                   "note": lost[0]["note"]}

    # ---- 耗时：来自 analysis_log（脚本记的 / 事后补记的），没有就是没有，不拿时间戳猜
    lb = log_bundle or {"stages": [], "has_log": False}
    stages = lb.get("stages") or []
    known_sec = sum(s["sec"] for s in stages if s.get("sec"))
    # 哪些环节根本没有耗时记录：合计里少了它们，就不能叫「这批一共花了多久」
    missing = [s["label"] for s in stages if not s.get("sec")]
    time_block = {
        "stages": stages,
        "has_log": bool(lb.get("has_log")),
        "total_sec": round(known_sec, 1) if known_sec else None,
        "total_label": human_sec(known_sec) if known_sec else "—",
        "missing": missing,
        "complete": bool(known_sec) and not missing,
        # 各环节是分段跑的，相加≈实际耗时；但并发跑时单集耗时之和远大于墙上时间，
        # 所以这里明确写成「各环节加起来」，不叫「跑完这批花了多久」。
        "note": "各环节耗时相加；并行跑时它小于「单集耗时 × 集数」的总和",
    }

    # ---- 用量
    usage = {
        "tokens": tokens,
        "yuan": round(tokens / 1e6 * price, 2),
        "price_per_mtok": price,
        "note": f"按每百万 token {price} 元估算",
        "shots": shots,
        "shots_eps": sum(1 for e in eps if e.get("has_shots")),
        "shot_version": next((e.get("shot_version") for e in reversed(eps)
                              if e.get("shot_version")), ""),
    }

    return {
        "drama": drama,
        "quality": {"score": score, "level": level, "level_hint": level_hint,
                    "dims": dims, "weakest": weakest},
        "time": time_block,
        "usage": usage,
        "counts": {
            "eps": n_ep, "video_count": video_count,
            "lines_total": lines_total, "sentences": sent,
            "named_pct": _pct(named, sent), "emotion_pct": _pct(emo, sent),
            "dense": dense, "shots": shots,
        },
    }


def tokens_of(asset_dir: Path) -> int:
    """把正式镜头标注文件里的 token 加总。

    只认 `shots_epNN.json`：目录里还有 `.v1/.v2/.v3` 老版本残留，
    一起加会把用量算成两倍。
    """
    total = 0
    d = asset_dir / "shots"
    if not d.is_dir():
        return 0
    for p in d.glob("shots_ep[0-9][0-9].json"):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        total += int((j.get("usage") or {}).get("total_tokens") or 0)
    return total
