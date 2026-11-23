#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""成片时间轴的唯一真源（2026-11-23 建）

**为什么要有这个**
段序 ≠ 成片时间轴：装配器会在 `out="black"` 的段后插一段转场黑场（默认 0.45s）、
在 `out="card"` 的段后插一张文字卡（默认 1.2s）。手算十有八九漏掉。

11-23 这一小时里，这个坑踩了**三次**：
  1. 解说落点手算，漏了 0.45s → 8 段解说全部早半秒
  2. 同期复述率检测器也用同一套错算法 → **检测出来的「复述率」根本是错的段**
  3. 事后又写了一个 plan_timeline.py 才发现第二处也不对

根因不是手滑，是**同一段算法抄了三份**（装配器、复述率检测器、话/静分布工具），
改一处忘一处。现在统一到这里，谁需要时间轴都 import 本模块。

**本模块必须跟 mix_trailer.py 的转场规则保持一致**
（改动请同时看 mix_trailer.py 里 build_segments 附近的逻辑）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# ── 成品语速基准：放在音色资产里，不写死在代码里 ──
#
# 成品语速是**「某个音色 + 某个变速档」的属性**，不是通用常数。
# 写死在这儿，11-23 就踩了：按「含标点」算出 6.506 字/秒，字数上限放宽 22%，
# 两段解说配完装不进画面窗口，白配一轮。
#
# 真值在 assets/voice_assets/<音色>/rate.json —— 换音色就换那个文件，不用改代码。
# 这里只留兜底常量（读不到资产目录时用），并在读不到时明确警告。
RATE_FALLBACK = 5.3

# 说书人C 的实测值（见 assets/voice_assets/narration_说书人C/rate.json）
#   D10：191 字 / 33.82 秒 = 5.648
#   D11：402 字 / 75.34 秒 = 5.336（逐段 5.02–5.73）
#   合计：593 字 / 109.16 秒 = 5.43 → 取 5.3 留余量
#
# ⚠️ 口径是**纯字，不含标点**。
# ⚠️ 逐段有波动（5.02–5.73）；「深·一字一顿」实测只有 4.8，比平均慢 10%。
#    情绪指令会改语速，这类段别按平均数卡。


def _data_root() -> Path:
    """数据根：环境变量 → 往上找工作台 → 默认仓库根。"""
    env = os.environ.get("SHORT_DRAMA_ASSET_ROOT") or os.environ.get("SHORT_DRAMA_DATA_ROOT")
    if env:
        return Path(env).expanduser()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "assets" / "voice_assets").is_dir():
            return parent
    return here.parents[2]


def load_rate(voice: str = "narration_说书人C") -> tuple[float, str]:
    """读某音色的成品语速基准 → (字/秒, 来源说明)。

    读不到就回落到兜底值，但**必须把话说出来**——语速猜错会让字数上限整体偏掉。
    """
    p = _data_root() / "assets" / "voice_assets" / voice / "rate.json"
    if p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            r = float(d.get("rate_finished") or 0)
            if r > 0:
                return r, f"{voice}/rate.json（{d.get('rate_source', '')}）"
        except Exception:
            pass
    return RATE_FALLBACK, f"兜底值 {RATE_FALLBACK}（没读到 {voice}/rate.json——换音色务必重测）"


RATE_FINISHED, RATE_SOURCE = load_rate()


def seg_duration(seg: dict) -> float:
    """一段本身的时长（不含段后转场）。"""
    if seg.get("type") == "ep":
        return round(float(seg["b"]) - float(seg["a"]), 3)
    if seg.get("type") == "video":
        if "a" in seg and "b" in seg:
            return round(float(seg["b"]) - float(seg["a"]), 3)
        return 0.0
    if seg.get("type") == "broll":
        # 垫画面段：一段里剪几个相关镜头拼起来放，原声静音，解说垫在上面。
        # 时长 = 各镜头时长之和（与装配器一致）。
        return round(sum(float(c["b"]) - float(c["a"])
                         for c in (seg.get("clips") or [])), 3)
    return round(float(seg.get("d", seg.get("duration", 0.0)) or 0.0), 3)


def card_duration(seg: dict, plan: dict) -> float:
    """一段后面那张文字卡占多久。段级可覆盖（out_card_d），否则用全局设置。"""
    if seg.get("out_card_d") is not None:
        return round(float(seg["out_card_d"]), 3)
    tr = plan.get("transitions") or {}
    return round(float((tr.get("card") or {}).get("d", 1.2)), 3)


def build_timeline(plan: dict) -> list[dict]:
    """按装配器的规则累出成片时间轴。

    规则（与 mix_trailer.py 一致）：
      - 段本身时长：ep = b-a，broll = 各镜头时长之和，black/card = d，video = 文件时长
      - 段上写了 out="black" → 段后插 transitions.black.d（默认 0.45s）转场黑场
      - 段上写了 out="card"  → 段后插 transitions.card.d（默认 1.2s）文字卡
      - 最后一段的 out 转场不生效（后面没有段落）

    返回每项：{kind: "seg"|"trans", label, start, end, dur, seg}
    """
    tr = plan.get("transitions") or {}
    black_d = float((tr.get("black") or {}).get("d", 0.45))

    tl: list[dict] = []
    t = 0.0
    segs = plan.get("segs") or []
    for i, seg in enumerate(segs):
        d = seg_duration(seg)
        tl.append({"kind": "seg", "label": seg.get("label", f"seg{i}"),
                   "start": round(t, 3), "end": round(t + d, 3), "dur": d, "seg": seg})
        t += d
        out = seg.get("out") or "cut"
        if i < len(segs) - 1:  # 最后一段的转场不生效
            if out == "black":
                tl.append({"kind": "trans", "label": "转场黑场", "start": round(t, 3),
                           "end": round(t + black_d, 3), "dur": black_d})
                t += black_d
            elif out == "card":
                cd = card_duration(seg, plan)
                tl.append({"kind": "trans", "label": "转场文字卡", "start": round(t, 3),
                           "end": round(t + cd, 3), "dur": cd})
                t += cd
    return tl


def find_host(tl: list[dict], at: float) -> dict | None:
    """某个成片秒数落在哪一段画面里（转场黑场不算画面）。"""
    for it in tl:
        if it["kind"] == "seg" and it["start"] <= at < it["end"]:
            return it
    return None


def chars(text: str) -> int:
    """字数——**纯字，不含标点**（跟语速口径一致）。"""
    import re
    return len(re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", text or ""))


def load_timeline(plan_path: str | Path) -> list[dict]:
    """读 plan.json 直接返回时间轴。"""
    import json
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    return build_timeline(plan)
