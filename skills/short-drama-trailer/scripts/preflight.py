#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一批 plan 一次性校验，并行跑装配器的 --dry。

为什么要有这个：装配器的 --dry 一次只吃一条 plan，10 条就串着等 10 遍，
每条内部还要 ffprobe 一遍用到的集，加起来两分多钟，全是在等磁盘和进程启动。
这一批片子用的是同一批集，并行跑 8 条、只探一次集时长，能把这段时间压到几秒。

刻意不做的事：**不自己重写校验规则**。闸门的意义在于「不信任生成侧」，
如果这里自己算一遍时长，等于把闸门变成生成侧自己给自己发证。所以这里只是
并行地调用装配器的 --dry，规则一条不改，原样收它的结论。

用法：
  python3 preflight.py --glob 'runs/以真心守护至亲/T01*/plan.json'
  python3 preflight.py --glob 'runs/八零憨妻/T900*/plan.json' --jobs 6
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
MIX = os.path.join(HERE, "mix_trailer.py")
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))


def one(plan):
    t0 = time.time()
    # dry 不落盘，out 只是必填参数，给个临时名
    out = os.path.join(tempfile.gettempdir(), "_preflight.mp4")
    r = subprocess.run([sys.executable, MIX, "--plan", plan, "--out", out, "--dry"],
                       capture_output=True, text=True, timeout=300)
    txt = (r.stdout or "") + (r.stderr or "")
    warns = [l.strip() for l in txt.splitlines()
             if any(k in l for k in ("⚠", "警告", "贴", "超", "黑帧", "拒绝"))]
    return {"plan": plan, "ok": r.returncode == 0, "sec": round(time.time() - t0, 2),
            "warns": warns, "raw": txt}


def brief(plan):
    try:
        d = json.load(open(plan, encoding="utf-8"))
    except Exception:
        return "-", 0, 0.0
    segs = [s for s in d.get("segs", []) if s.get("type") == "ep"]
    return (d.get("title") or os.path.basename(os.path.dirname(plan)),
            len(segs), sum(s["b"] - s["a"] for s in segs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", required=True, help="plan 路径通配，如 'runs/x/T01*/plan.json'")
    ap.add_argument("--jobs", type=int, default=8)
    args = ap.parse_args()

    plans = sorted(glob.glob(os.path.join(ROOT, args.glob))
                   or glob.glob(args.glob))
    if not plans:
        raise SystemExit(f"没匹配到 plan：{args.glob}")
    if not os.path.exists(MIX):
        raise SystemExit(f"找不到装配器：{MIX}")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=min(args.jobs, len(plans))) as pool:
        results = list(pool.map(one, plans))
    wall = time.time() - t0

    bad = 0
    print(f"{'结果':<6}{'条':<26}{'段':>4}{'时长':>9}{'本秒':>7}")
    print("-" * 60)
    for r in sorted(results, key=lambda x: x["plan"]):
        title, n, dur = brief(r["plan"])
        mark = "通过" if r["ok"] else "拒绝"
        if not r["ok"]:
            bad += 1
        print(f"{mark:<6}{title[:24]:<26}{n:>4}{dur:>8.1f}s{r['sec']:>7.1f}")
        for w in r["warns"]:
            print(f"        · {w[:90]}")

    for r in results:
        if not r["ok"]:
            print(f"\n---- 被拒：{os.path.relpath(r['plan'], ROOT)} ----")
            print(r["raw"].strip()[-900:])

    print(f"\n共 {len(plans)} 条，通过 {len(plans)-bad}，拒绝 {bad}；"
          f"并行 {args.jobs} 路，墙钟 {wall:.1f}s")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
