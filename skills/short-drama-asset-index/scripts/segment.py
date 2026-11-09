#!/usr/bin/env python3
"""短剧冲突段落切分（骨架层）。

只做机械切分，不做语义判断：
- 依据已抽好的台词时间轴（lines/lines_epNN.json，来自内嵌硬字幕 OCR）
- 切分信号：台词间隔 > GAP_LIMIT 秒（话题/人物组切换）或 段长 > MAX_LEN 秒（强制）
- 段长 < MIN_LEN 秒时与相邻段合并（集首尾除外）

不认人、不判冲突强度、不判临界 —— 这些要靠人（或后续音频说话人分离）补。
诚实留白：`speakers` 恒为 null，`speakers_pending` 为 true。

用法:
    python segment.py --all
    python segment.py --ep 1 6
    python segment.py --all --write      # 合并写进 asset_index.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 段长约束（秒）。依据：短剧单集 37-133 秒，16:9 竖屏 2-3 秒一切，
# 一段需容纳 3-8 句台词才够一个「冲突单元」。
MIN_LEN = 8.0
MAX_LEN = 26.0
GAP_LIMIT = 4.5      # 台词间隔超过此值视为场景/话题切换
TAIL_PAD = 1.2       # 段尾留白，避免切在台词正中
LEAD_PAD = 1.0       # 段首留白
PAD_TOTAL = LEAD_PAD + TAIL_PAD + 2.5   # start/end 相对首末台词的额外时长
SPAN_MAX = MAX_LEN - PAD_TOTAL          # 台词跨度上限（判切分用）
SPAN_MIN = MIN_LEN - PAD_TOTAL


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_lines(lines_dir: Path, ep: int) -> dict:
    p = lines_dir / f"lines_ep{ep:02d}.json"
    if not p.exists():
        raise FileNotFoundError(f"缺台词时间轴: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def segments_of(lines: list[dict], duration: float) -> list[dict]:
    """把带秒的台词序列切成冲突段落骨架。"""
    if not lines:
        return []

    # 1) 先按间隔切成分片
    chunks: list[list[dict]] = [[lines[0]]]
    for prev, cur in zip(lines, lines[1:]):
        gap = cur["t"] - prev["t"]
        if gap > GAP_LIMIT:
            chunks.append([cur])
        else:
            chunks[-1].append(cur)

    # 2) 先递归切长：先把所有超长段对半切到 <= SPAN_MAX
    def split_long(chs: list[list[dict]]) -> list[list[dict]]:
        res: list[list[dict]] = []
        for ch in chs:
            span = ch[-1]["t"] - ch[0]["t"]
            if span > SPAN_MAX and len(ch) >= 4:
                mid = len(ch) // 2
                res.extend(split_long([ch[:mid], ch[mid:]]))
            else:
                res.append(ch)
        return res

    chunks = split_long(chunks)

    # 3) 再合并过短分片（span < SPAN_MIN），优先并入前一片；
    #    但若与前片相隔超过 GAP_LIMIT（说明是新场景首句），不合并。
    merged: list[list[dict]] = []
    for ch in chunks:
        span = ch[-1]["t"] - ch[0]["t"]
        if merged and span < SPAN_MIN:
            gap = ch[0]["t"] - merged[-1][-1]["t"]
            prev_span = merged[-1][-1]["t"] - merged[-1][0]["t"]
            if gap <= GAP_LIMIT and prev_span + span + gap <= SPAN_MAX:
                merged[-1].extend(ch)
                continue
        merged.append(ch)

    out = merged

    # 4) 转成段记录（段间不重叠：start 不早于上一段 end，end 不越过下一段首句）
    segs = []
    for i, ch in enumerate(out, 1):
        first_t = ch[0]["t"]
        last_t = ch[-1]["t"]
        prev_end = segs[-1]["end"] if segs else 0.0
        start = max(prev_end, first_t - LEAD_PAD, 0.0)
        if i < len(out):
            next_t = out[i][0]["t"]
            end = min(next_t - 0.3, last_t + PAD_TOTAL)
        else:
            end = duration
        end = max(end, start + 1.0)
        segs.append({
            "id": f"p{i:02d}",
            "start": round(start, 1),
            "end": round(end, 1),
            "duration": round(end - start, 1),
            "lines": [{"t": l["t"], "text": l["text"]} for l in ch],
            "speakers": None,
            "speakers_pending": True,
            **({"head_from_zero": True} if start == 0.0 else {}),
            **({"tail_to_end": True} if i == len(out) else {}),
        })
    return segs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", nargs="*", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--write", action="store_true", help="合并写入 asset_index.json")
    args = ap.parse_args()

    root = repo_root()
    lines_dir = root / "assets/short-drama/八零憨妻的甜蜜日常/lines"
    if args.all:
        eps = list(range(1, 20))          # 第 20 集大结局按任务要求排除
    elif args.ep:
        eps = args.ep
    else:
        ap.error("需要 --all 或 --ep")

    result = {}
    total_seg = 0
    for ep in eps:
        d = load_lines(lines_dir, ep)
        segs = segments_of(d["lines"], d["duration"])
        result[ep] = {"duration": d["duration"], "segments": segs}
        total_seg += len(segs)
        lens = [s["duration"] for s in segs]
        print(f"EP{ep:02d} {d['duration']:6.1f}s -> {len(segs)} 段  "
              f"段长 {min(lens):.0f}-{max(lens):.0f}s  句数 {[len(s['lines']) for s in segs]}")

    print(f"\n合计 {len(eps)} 集 / {total_seg} 段 / 平均 {total_seg/len(eps):.1f} 段每集")

    if args.write:
        out = root / "assets/short-drama/八零憨妻的甜蜜日常/segments_skeleton.json"
        out.write_text(json.dumps({"generated_by": "segment.py", "gap_limit": GAP_LIMIT,
                                   "min_len": MIN_LEN, "max_len": MAX_LEN,
                                   "episodes": {str(k): v for k, v in result.items()}},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
