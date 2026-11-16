#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条 plan → 一份「清单过目」。

为什么要有这个：清单过目是给人看的那一页——不是 json，是把「这条片子怎么排的」
摊成一张表，让人能一口气读完、指出哪段是凑数的。以前每轮都是现写一段脚本拼，
10 条就要拼 10 次，纯重复。

它只做搬运，不做判断：每段的起止都用台词库里那一段的真实首句/末句，
不重新概括——概括一旦失真，人就是照着一张假表在审。

用法：
  python3 reviewdoc.py --glob 'runs/以真心守护至亲/T01*/plan.json'
  python3 reviewdoc.py --plan runs/以真心守护至亲/T9010/plan.json
"""
import argparse
import glob
import json
import os

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "..", ".."))

TEMPLATE = """# {tid}「{title}」

**角度**：{angle}

**总长** {total:.0f} 秒（{mm} 分 {ss:.0f} 秒）｜{n} 段｜{w}×{h} / {fps} 帧

## 一句话结构

{chain}

## 逐段

| # | 段 | 集 | 原片起止 | 时长 | 从哪句开始 | 到哪句结束 |
|---|---|---|---|---|---|---|
{rows}

## 机器已经验过的（不用你看）

- 出点全部在**视频流**时长以内，不是容器时长
- 切点全部落在两句之间的空隙，物理上切不半句
- 同一集里没有一段被另一段整段包含（不会同一段播两遍）
- 同一集没有连着拼三段以上（平台禁大段拼接原片）
- 每段都带了三拍说明（挑起／交锋／落点）

## 要你看的三件（机器判不了）

1. **顺着读下来通不通**：不是看片，是读提纲。一口气读完，能不能说出这条在讲什么
2. **哪一段是凑数的**：去掉它故事还完整，那段就该去掉
3. **落点够不够狠**：最后一句停住，有没有想往下看的劲

意见说编号就行（{tid}）。
"""


def lines_of(drama, ep):
    p = os.path.join(ROOT, "assets", "short-drama", drama, "lines", f"lines_ep{ep:02d}.json")
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))["lines"]


def one(plan_path):
    d = json.load(open(plan_path, encoding="utf-8"))
    drama = d.get("drama", "")
    segs = [s for s in d.get("segs", []) if s.get("type") == "ep"]
    if not segs:
        return None
    tid = os.path.basename(os.path.dirname(os.path.abspath(plan_path)))
    total = sum(s["b"] - s["a"] for s in segs)

    cache, rows = {}, []
    for k, s in enumerate(segs, 1):
        ep = s["ep"]
        if ep not in cache:
            cache[ep] = lines_of(drama, ep) or []
        ls = cache[ep]
        inside = [l for l in ls if s["a"] <= l["t"] <= s["b"]]
        first = inside[0]["text"] if inside else "（这一段没有人说话）"
        last = inside[-1]["text"] if inside else "—"
        rows.append(f"| {k} | {s.get('label','')} | ep{ep} | {s['a']:.1f}–{s['b']:.1f} | "
                    f"{s['b']-s['a']:.0f}s | {first} | {last} |")

    chain = " → ".join((s.get("label") or f"ep{s['ep']}").split("_", 1)[-1] for s in segs)
    md = TEMPLATE.format(
        tid=tid, title=d.get("title", ""), angle=d.get("angle") or "（未写）",
        total=total, mm=int(total // 60), ss=total % 60, n=len(segs),
        w=d.get("w", 1080), h=d.get("h", 1920), fps=d.get("fps", 30),
        chain=chain, rows="\n".join(rows))
    out = os.path.join(os.path.dirname(os.path.abspath(plan_path)), "清单过目.md")
    open(out, "w", encoding="utf-8").write(md)
    return out, len(segs), total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="append")
    ap.add_argument("--glob")
    args = ap.parse_args()
    plans = list(args.plan or [])
    if args.glob:
        plans += glob.glob(os.path.join(ROOT, args.glob)) or glob.glob(args.glob)
    if not plans:
        raise SystemExit("要 --plan 或 --glob")
    for p in sorted(plans):
        got = one(p)
        if got:
            out, n, tot = got
            print(f"{os.path.relpath(out, ROOT)}  {n} 段 {tot:.0f}s")


if __name__ == "__main__":
    main()
