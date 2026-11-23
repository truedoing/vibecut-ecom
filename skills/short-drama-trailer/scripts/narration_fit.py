#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解说的落点排布 + 关键台词保护校验。

为什么要有这个（2026-11-23）：
  解说叠上去的时候，原剧是完全静音的（duck=0）。所以解说的覆盖区间
  等于「这段原声在成片里被删掉了」。
  D11 第二轮实测：8 块解说盖掉了 22 句原声，其中两句是命门——
  「你进弘业的事也别想了」和「刚才你要他办不成就带我走」。

**第三轮改版（2026-11-24）：落点只落在「垫画面段」上**

  用户提出的解法是根除，不是补丁：
    「至于盖住原剧，能否剪辑几个相关的场景片段播放，反正也是静音」

  也就是说——不再让解说压在主线画面上。改成：
    · 主线段（ep）：原声一句不丢，**不放解说**
    · 垫画面段（broll）：剪几个相关镜头静音放着，**解说配在这里**

  这样两件事同时成立：
    ① 原剧完整——主线段没有解说压着，刺点原声全在
    ② 解说能写长——垫画面段的长度是自己定的，写多长就配多长

  所以本脚本的落点规则从「在主线段里找空档」改成
  「只落在垫画面段上」，落在线上（主线段）直接报错。

  关键台词保护区也跟着改版：旧格式记的是成片绝对秒数，
  时间轴一改（加了垫画面段）就全部错位。新格式记「段标签+段内偏移」，
  由脚本按当前时间轴解析成绝对秒数——时间轴变了保护区自动跟着走。

用法：
  # 排布：给每块解说找落点
  python3 narration_fit.py --plan plan.json --narration 解说配置.json \\
      --keys 关键台词.json --fit

  # 只校验
  python3 narration_fit.py --plan plan.json --narration 解说配置.json \\
      --keys 关键台词.json --check
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from timeline import build_timeline, chars, load_rate  # noqa: E402


def load_keys(path: Path, tl: list[dict] | None = None) -> list[dict]:
    """读关键台词保护区。

    **新格式（按段定位，时间轴变了不会错位）**：
        {"seg": "S01_赌局钩子", "a": 18.35, "b": 20.35, "text": "两位办理人是我"}
    a/b 是**段内偏移秒数**（相对该段起点），由脚本按当前时间轴解析成绝对秒数。

    **旧格式（绝对秒数）**：仍然认，但时间轴一改就错位——只在没有 tl 时用。
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else data.get("keys", [])

    seg_start: dict[str, float] = {}
    if tl:
        for it in tl:
            if it["kind"] == "seg":
                seg_start[it["label"]] = it["start"]

    out = []
    for r in rows:
        seg = r.get("seg")
        if seg and tl:
            if seg not in seg_start:
                raise SystemExit(
                    f"关键台词.json 里引用了不存在的段：{seg}\n"
                    f"  现有段名：{sorted(seg_start)}")
            base = seg_start[seg]
            a = base + float(r.get("a", 0))
            b = base + float(r.get("b", r.get("a", 0)))
        else:
            a = float(r["a"])
            b = float(r["b"])
        out.append({"a": a, "b": b, "label": r.get("label") or seg or "",
                    "text": r.get("text", "")})
    return sorted(out, key=lambda r: r["a"])


def free_windows(keys: list[dict], total: float) -> list[tuple[float, float]]:
    """保护区之间的空档。"""
    out, cur = [], 0.0
    for k in keys:
        if k["a"] > cur:
            out.append((cur, k["a"]))
        cur = max(cur, k["b"])
    if cur < total:
        out.append((cur, total))
    return out


def overlaps(a0: float, a1: float, keys: list[dict]) -> list[dict]:
    return [k for k in keys if a0 < k["b"] - 1e-6 and a1 > k["a"] + 1e-6]


def fit(cues: list[dict], texts: list[str], keys: list[dict],
        total: float, rate: float,
        targets: list[float] | None = None,
        segs: list[tuple[float, float, str]] | None = None,
        man: dict[int, float] | None = None) -> list[float]:
    """给每块解说找落点。

    落点必须同时满足三条：
      ① 整块落在**同一个垫画面段内**（不许跨段——跨段就得压到主线上去）
      ② 不碰到关键台词保护区（原声在解说期间是静音的，碰了＝删掉）
      ③ 尽量贴住目标画面时刻（解说得跟着故事走）

    时长用**实测值**（文案没变且配过音的段），没有才按语速估。
    （2026-11-23 第三次踩同一个坑：预估 2.64s、实测 4.06s——情绪指令
      「短，硬，把这句压实」让它念慢了 54%，按预估排落点就撞进保护区。
      所以排落点和校验必须用同一套时长。）

    为什么必须贴着画面：11-23 试过「顺序贪心塞进第一个大空档」，
    结果 11 块全挤在前 64 秒，后半片一句解说都没有——
    而后半片才是加码、点破、认账。解说得跟着故事走。
    """
    wins = free_windows(keys, total)
    man = man or {}
    ats: list[float] = []
    cursor = 0.0

    def need_of(i: int, text: str) -> float:
        real = man.get(i + 1)
        return real if real else chars(text) / rate

    def candidates(need: float, want: float) -> list[float]:
        """所有能装下这块解说的落点，按「离目标近」排序。

        两个约束必须**同时**满足，不能先满足一个再调另一个（11-23 踩过：
        先按空档找起点、再按画面段往后推，推完可能落回保护区里）：
          ① [start, start+need] 完全落在某个画面段内
          ② [start, start+need] 完全落在某个保护区之间的空档内
        """
        out: list[float] = []
        for s0, s1, _ in (segs or []):
            for a, b in wins:
                lo, hi = max(s0, a), min(s1, b)
                if hi - lo < need + 0.05:
                    continue
                start = max(lo, want)
                if start + need > hi + 1e-6:
                    continue
                out.append(start)
        return sorted(set(round(x, 2) for x in out), key=lambda x: (abs(x - want), x))

    for i, text in enumerate(texts):
        need = need_of(i, text)
        want = targets[i] if targets and i < len(targets) else cursor
        cands = candidates(need, want)
        placed = None
        for c in cands:
            if c >= cursor - 1e-6:
                placed = c
                break
        if placed is None and cands:
            # 没有装得下的位置：取离目标最近的，校验环节会报出来让人改文案
            placed = cands[0]
        if placed is None:
            placed = cursor
        ats.append(round(placed, 2))
        cursor = placed + need
    return ats


def main() -> int:
    ap = argparse.ArgumentParser(description="解说落点排布 + 关键台词保护校验")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--narration", required=True)
    ap.add_argument("--keys", required=True, help="关键台词.json（保护区）")
    ap.add_argument("--fit", action="store_true", help="算出推荐落点并写回解说配置")
    ap.add_argument("--check", action="store_true", help="只校验不修改")
    ap.add_argument("--rate", type=float, default=0.0, help="成品语速，默认读基准文件")
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    narr_path = Path(args.narration)
    cfg = json.loads(narr_path.read_text(encoding="utf-8"))
    rate = args.rate or load_rate()[0]

    tl = build_timeline(plan)
    total = tl[-1]["end"] if tl else 0.0
    texts = cfg.get("texts") or []
    cues = cfg.get("cues") or []

    keys = load_keys(Path(args.keys), tl)

    # 已有配音的用实测时长——**但只在文案一字不差时才用**。
    # （2026-11-23 踩过：改了文案，时长还按旧配音算，校验就成了摆设。
    #   同一个洞在 build_trailer.py 里也犯过一次，两处都补上。）
    man: dict[int, float] = {}
    mp = narr_path.parent / "配音输出" / "manifest.json"
    if mp.is_file():
        for row in json.loads(mp.read_text(encoding="utf-8")):
            idx = int(row.get("idx", 0))
            if idx <= len(texts) and (row.get("text") or "").strip() == (texts[idx - 1] or "").strip():
                man[idx] = float(row.get("duration") or 0)

    # 垫画面段：解说只能落在这里。落在主线段上就等于盖住原声。
    broll_segs = [(it["start"], it["end"], it["label"]) for it in tl
                  if it["kind"] == "seg" and it["seg"].get("type") == "broll"]

    if args.fit:
        targets = cfg.get("targets") or []
        if not targets:
            print("⚠ 解说配置里没有 targets（每块解说的目标画面时刻），"
                  "只能按顺序排——会全挤在前面几个区。")
        ats = fit(cues, texts, keys, total, rate, targets, broll_segs, man)
        for i, (cue, at) in enumerate(zip(cues, ats), 1):
            cue["at"] = at
        cfg["cues"] = cues
        narr_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写回落点 → {narr_path}")

    print(f"\n=== 关键台词保护校验 ===")
    print(f"保护区 {len(keys)} 处　垫画面段 {len(broll_segs)} 个\n")
    bad = 0
    for i, (cue, text) in enumerate(zip(cues, texts), 1):
        at = float(cue["at"])
        need = man.get(i) or chars(text) / rate
        end = at + need
        hit = overlaps(at, end, keys)
        host = next((it for it in tl
                     if it["kind"] == "seg" and it["start"] <= at < it["end"]), None)
        if host is None:
            bad += 1
            print(f"  第{i:02d}段 ✗ @{at:7.2f}–{end:7.2f} 不在任何画面段里")
            continue
        hlabel = host["label"]
        htype = host["seg"].get("type")
        if htype != "broll":
            # 落在主线段上：解说期间主线原声是静音的＝原声被删了。
            # 新规则是不许这样——解说该配在垫画面段上。
            bad += 1
            print(f"  第{i:02d}段 ✗ @{at:7.2f}–{end:7.2f} [{hlabel}] "
                  f"落在主线段上——这里有原声，解说一盖就等于删了原声")
            continue
        if hit:
            bad += 1
            print(f"  第{i:02d}段 ✗ @{at:7.2f}–{end:7.2f} [{hlabel}] 盖住了：")
            for k in hit:
                print(f"        「{k['text'][:46]}」({k['a']:.2f}–{k['b']:.2f})")
        else:
            print(f"  第{i:02d}段 ✓ @{at:7.2f}–{end:7.2f} [{hlabel}] {chars(text):3d}字"
                  f"（垫画面，原声不受影响）")

    total_words = sum(chars(t) for t in texts)
    print(f"\n解说合计 {total_words} 字 ≈ {total_words / rate:.1f}s / 全片 {total:.2f}s"
          f" = {total_words / rate / total * 100:.0f}%")
    if bad:
        print(f"\n✗ {bad} 块有问题。")
        print("  改法：把落点挪到垫画面段（--fit），或给垫画面段加长。")
        return 1
    print("\n✓ 解说全部落在垫画面段，主线原声一句不丢。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
