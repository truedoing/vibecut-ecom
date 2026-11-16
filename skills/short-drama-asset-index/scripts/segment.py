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
import time
from pathlib import Path

try:  # 数据目录不靠数层级猜：见同目录 _env.py（换机器也不用改代码）
    from _env import data_root as _data_root
except Exception:  # 极端情况下退回老办法，至少不崩
    def _data_root():
        return Path(__file__).resolve().parents[3]

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
    return _data_root()


ROOT = repo_root()
# 进度上报给主管的网页看；找不到模块就降级，不影响切分本身。
# 主管技能住在插件包的 skills/ 下，不在数据目录里，得问 _env 要宿主位置（11-16 修）。
try:
    from _env import HOST as _HOST
except Exception:
    _HOST = Path(__file__).resolve().parents[3]
_SUP = _HOST / "skills" / "short-drama-supervisor" / "scripts"
try:
    if _SUP.is_dir() and str(_SUP) not in sys.path:
        sys.path.insert(0, str(_SUP))
    import progress
    import analysis_log
except Exception:
    class _NoProgress:
        def __getattr__(self, _name):
            return lambda *a, **k: None
    progress = _NoProgress()
    analysis_log = _NoProgress()


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
            "line_times": [l["t"] for l in ch],
            "speakers": None,
            "speakers_pending": True,
            **({"head_from_zero": True} if start == 0.0 else {}),
            **({"tail_to_end": True} if i == len(out) else {}),
        })
    return segs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drama", required=True,
                    help="剧名＝剧库目录名，如 父爱跨越山海")
    ap.add_argument("--ep", nargs="*", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--write", action="store_true", help="合并写入 segments_skeleton.json")
    args = ap.parse_args()

    t0 = time.time()
    root = repo_root()
    lines_dir = root / "assets/short-drama" / args.drama / "lines"
    if not lines_dir.exists():
        ap.error(f"台词库不存在：{lines_dir}")
    if args.all:
        # 集数从台词库实际文件推断，不写死——换剧时台词库做到第几集就切到第几集
        eps = sorted(int(p.stem.replace("lines_ep", ""))
                     for p in lines_dir.glob("lines_ep*.json"))
        if not eps:
            ap.error(f"{lines_dir} 下没有 lines_epNN.json")
    elif args.ep:
        eps = args.ep
    else:
        ap.error("需要 --all 或 --ep")

    result = {}
    total_seg = 0
    for ep in eps:
        progress.start("segment", args.drama, ep, total=1, unit="集")
        try:
            d = load_lines(lines_dir, ep)
            segs = segments_of(d["lines"], d["duration"])
            result[ep] = {"duration": d["duration"], "segments": segs}
            total_seg += len(segs)
            lens = [s["duration"] for s in segs]
            print(f"EP{ep:02d} {d['duration']:6.1f}s -> {len(segs)} 段  "
                  f"段长 {min(lens):.0f}-{max(lens):.0f}s  句数 {[len(s['line_times']) for s in segs]}")
            progress.update("segment", args.drama, ep, done=1, total=1,
                            note=f"{len(segs)} 段")
            progress.finish("segment", args.drama, ep, note=f"{len(segs)} 段")
        except Exception as e:
            progress.fail("segment", args.drama, ep,
                          note=f"{e.__class__.__name__}: {str(e)[:100]}")
            raise

    print(f"\n合计 {len(eps)} 集 / {total_seg} 段 / 平均 {total_seg/len(eps):.1f} 段每集")

    # 耗时台账：切镜头这一环只读台词文件、不调模型，通常几秒到十几秒
    analysis_log.record(args.drama, "segment", started=t0,
                        eps=len(eps), items=total_seg,
                        note=f"间隔上限 {GAP_LIMIT} 秒，段长 {MIN_LEN}–{MAX_LEN} 秒")

    if args.write:
        out = root / "assets/short-drama" / args.drama / "segments_skeleton.json"
        out.write_text(json.dumps({"generated_by": "segment.py", "gap_limit": GAP_LIMIT,
                                   "min_len": MIN_LEN, "max_len": MAX_LEN,
                                   "lines_ref": {"dir": "lines/", "pattern": "lines_epNN.json",
                                                 "note": "台词文字只存在 lines/lines_epNN.json；本文件只存时间码 line_times，不复制文字",
                                                 "since": "2026-11-10"},
                                   "episodes": {str(k): v for k, v in result.items()}},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
