#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在**已装配的底片**上找解说落点（2026-11-23 建）

**为什么要有这个**
11-23 那版解说落点是手挑的，结果返工了一轮：手算时间轴漏了 0.45s 转场黑场，
8 段解说全早了半秒。这个工具把「落点该挑在哪」变成**量出来**的，不靠手感。

**关键前提**：叠音时解说期间原剧是**完全静音**的（duck=0）。
所以解说的落点必须落在**底片上没有原声**的地方，否则等于把那段原声压掉了。
这个工具扫的就是这件事——不猜，直接测底片的音量包络。

**它做两件事**：
  1. 按清单的时间轴切段，报告每段里的「无原声空档」（>= min_silence 秒）
  2. 给出每个空档能装多少字（按实测语速）

用法：
    python3 narration_spots.py --plan runs/x/plan.json --video runs/x/build/底片.mp4
    python3 narration_spots.py --plan runs/x/plan.json --video ... --min-silence 0.8

参数：
    --plan        剪辑清单 plan.json（算时间轴用）
    --video       已装配的底片 mp4（扫音量用）
    --min-silence  多长算一段空档（秒），默认 0.8
    --thr         静音阈值 = 峰值 × 此值，默认 0.12（比 speech_runs 的 0.18 灵敏）
    --win         分析窗口（秒），默认 0.08
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from timeline import build_timeline, load_rate  # noqa: E402


def envelope(path: str, win: float = 0.08, ar: int = 16000) -> np.ndarray:
    """整条片的 RMS 包络（每 win 秒一个值）。"""
    raw = subprocess.check_output(
        ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1",
         "-ar", str(ar), "-f", "s16le", "-"])
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    n = int(ar * win)
    m = max(len(a) // n, 1)
    a = a[:m * n].reshape(m, n)
    return np.sqrt((a ** 2).mean(axis=1))


def main() -> int:
    ap = argparse.ArgumentParser(description="在底片上找解说落点")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--min-silence", type=float, default=0.8)
    ap.add_argument("--thr", type=float, default=0.12)
    ap.add_argument("--win", type=float, default=0.08)
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    tl = build_timeline(plan)
    rate, src = load_rate()
    env = envelope(args.video, args.win)
    thr = float(env.max()) * args.thr
    print(f"底片：{args.video}")
    print(f"包络 {len(env)} 点（每 {args.win}s 一点），静音阈值 {thr:.0f}（峰值 {env.max():.0f}）")
    print(f"语速 {rate} 字/秒　来源：{src}")
    print()

    total_slots = 0
    for it in tl:
        if it["kind"] != "seg" or it["seg"].get("type") != "ep":
            continue
        s, e = it["start"], it["end"]
        i0, i1 = int(s / args.win), int(e / args.win)
        seg_env = env[i0:min(i1, len(env))]
        if len(seg_env) == 0:
            continue
        quiet = seg_env < thr
        # 找出连续静默段
        slots: list[tuple[float, float]] = []
        i = 0
        while i < len(quiet):
            if quiet[i]:
                j = i
                while j < len(quiet) and quiet[j]:
                    j += 1
                a = s + i * args.win
                b = s + min(j, len(quiet)) * args.win
                if b - a >= args.min_silence:
                    slots.append((a, b))
                i = j
            else:
                i += 1
        total_slots += len(slots)
        head = f"{it['label'][:24]:<26} {s:7.2f}–{e:7.2f}"
        if not slots:
            print(f"{head}  （整段都有原声，无空档）")
            continue
        print(f"{head}  空档 {len(slots)} 处：")
        for a, b in slots:
            cap = int((b - a) * rate)
            print(f"      {a:7.2f}–{b:7.2f}  ({b - a:5.2f}s)  能装 {cap:3d} 字")

    print(f"\n全片可用空档 {total_slots} 处。")
    print("落点挑法：优先挑**长空档**，让解说整块落在没有原声的地方；")
    print("短空档留给短句，长解说放在段与段之间的长空档里。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
