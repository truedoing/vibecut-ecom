#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 plan.json 算成片时间轴 + 每条解说的可用窗口

**为什么要有这个**
`plan.json` 的段序**不等于**成片时间轴：装配器会在 `out="black"` 的段后面插一段转场黑场
（默认 0.45s）、在 `out="card"` 的段后面插一张文字卡（默认 1.2s）。
手算时间轴十有八九漏掉这些转场——11-23 实测就漏了 0.45s，
结果 S01 之后所有解说起点都早了半秒，**同期复述率检测对错了段**（检测器也用同一套错算法）。

**这个脚本是时间轴的唯一真源**：解说的 `at`（成片起点）、`limit`（可用秒数上限）都从这里出。
装配器 `mix_trailer.py` 打印的段落表跟它对得上。

用法：
    python3 plan_timeline.py --plan runs/.../plan.json
    python3 plan_timeline.py --plan runs/.../plan.json --narration runs/.../build/解说配置.json

加 --narration 时会打印每条解说的落点、所在画面段、可用窗口、按实测语速的字数上限。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

# 时间轴算法与语速基准都放在 timeline.py——那是唯一真源。
# （2026-11-23 建：同一段算法抄了三份，改一处忘一处，踩了三次。）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from timeline import (  # noqa: E402
    RATE_FINISHED,
    build_timeline,
    chars,
    find_host,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="算成片时间轴与解说可用窗口")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--narration", default="")
    ap.add_argument("--caps", action="store_true",
                    help="只出「每段能写多少字」的上限表——写文案之前先跑这个")
    ap.add_argument("--rate", type=float, default=RATE_FINISHED, help="成品语速 字/秒")
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    tl = build_timeline(plan)
    total = tl[-1]["end"] if tl else 0.0

    # ── 只出「字数上限表」：写文案之前先知道每块能写多长 ──
    # 2026-11-23 加。11-23 那次文案返工三轮，根因就是先写文案、后算窗口：
    # 写完才发现装不下 → 拆块 → 还是超 → 再压 → 重配。顺序反了。
    # 现在这个模式是给「文案还没写」的阶段用的：先拿表，再照着上限写。
    if args.caps:
        print("=== 每段解说能写多长（照上限写，别超）===")
        print(f"成片总长 {total:.2f}s　语速 {args.rate} 字/秒（纯字口径）")
        print(f"{'段':<26}{'起':>7}{'止':>8}{'可用':>7}{'字数上限':>9}")
        print("-" * 60)
        for it in tl:
            if it["kind"] != "seg" or it["seg"].get("type") != "ep":
                continue
            win = it["dur"]
            cap = math.floor(win * args.rate)
            print(f"{it['label'][:24]:<26}{it['start']:>7.2f}{it['end']:>8.2f}"
                  f"{win:>7.2f}{cap:>9d}")
        print("-" * 60)
        print("用法：在这张表的窗口里挑落点，每块解说照「字数上限」写，超了就拆成两块。")
        print("⚠️ 落点别落在关键原声上——先跑 speech_runs.py 看哪段话说到哪儿停了。")
        return 0

    print("=== 成片时间轴 ===")
    for it in tl:
        tag = "段" if it["kind"] == "seg" else "转"
        print(f"  {tag} {it['label']:26s} {it['start']:7.2f} → {it['end']:7.2f}  ({it['dur']:5.2f}s)")
    print(f"  合计 {total:.2f}s")

    if args.narration:
        cfg = json.loads(Path(args.narration).read_text(encoding="utf-8"))
        print("\n=== 解说落点与可用窗口 ===")
        cues, texts = cfg.get("cues") or [], cfg.get("texts") or []
        # 已有配音的，用 manifest 里的**实测时长**；没有的才按语速估
        man = {}
        for mp in Path(args.narration).parent.glob("配音输出/manifest.json"):
            for row in json.loads(mp.read_text(encoding="utf-8")):
                man[int(row.get("idx", 0))] = float(row.get("duration") or 0)
        prev_end = None
        for i, cue in enumerate(cues):
            at = float(cue["at"])
            text = texts[i] if i < len(texts) else ""
            n = chars(text)
            hit = find_host(tl, at)
            if not hit:
                print(f"  第{i + 1}段 at={at:7.2f}  ✗ 落在任何画面段之外（多半是转场黑场里）")
                continue
            win = hit["end"] - at
            real = man.get(i + 1)
            if real:
                need, how = real, f"实测{real:.2f}s"
            else:
                need, how = n / args.rate, f"估{n / args.rate:.2f}s"
            cap = math.floor(win * args.rate)
            gap = "" if prev_end is None else f"  距上一段解说 {at - prev_end:5.2f}s"
            flag = "✓" if need <= win else "✗ 装不下"
            print(f"  第{i + 1}段 at={at:7.2f}  {hit['label']:24s} 窗口{win:6.2f}s "
                  f"上限{cap:3d}字  实{n:3d}字/{how}  {flag}{gap}")
            prev_end = at + need
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
