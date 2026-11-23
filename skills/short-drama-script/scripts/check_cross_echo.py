#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解说词跨段回声检测（2026-11-22 加，补 check_repetition.py 的盲区）

**为什么要有这个**
`check_repetition.py` 只查「这段解说配的这块画面上正在说的原话」。
但观众听的是**整部片子**：一句解说词如果跟原剧任何一句台词撞了字，
哪怕不在同一块画面上，听感上也是「同一句话说两遍」。

实测踩过的坑：
- 解说写「别看我的面子」，原剧 ep43 说「不用看介绍人的面子」→ 撞「的面子」
- 解说写「长辈那边的推荐不作数」，原剧 ep49 说「父亲的推荐意见暂不细入」→ 撞「的推荐」

**怎么判**
把整部剧的台词池（`assets/short-drama/<剧名>/lines/lines_ep*.json`）摊平，
解说的每句话跟池里每句逐字比，连续 **4 字**相同算真复述（判不过），
**3 字**相同算巧合级（提个醒，但不算错）。

用法：
    python3 check_cross_echo.py --narration runs/.../解说配置.json --drama 以真心守护至亲

参数：
    --drama      剧名（对应 assets/short-drama/<剧名>/lines/），也可用 --lines 直接给目录
    --lines      台词库目录（给了就优先用）
    --min-len    判定阈值，默认 4（3 字只警告）
    --cut-eps    本片实际用到的集号（逗号分隔）。这些集里的台词观众一定会听到，
                 所以落在这些集里的 3 字撞词也算硬错——观众等于是同一句听了两遍。
    --quiet      只报要改的（≥4 字或落在本片集里），不报巧合级

退出码：0 = 没有要改的；1 = 有真复述，得改词。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.resolve().parents[2]

try:
    sys.path.insert(0, str(HERE))
    from _env import data_root as _data_root  # type: ignore
except Exception:  # pragma: no cover
    def _data_root() -> Path:
        return ROOT


def norm_text(s: str) -> str:
    """只留字，标点空白不算——比对时标点差异不该算撞词。"""
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s or "")


def best_hit(frag: str, pool_line: str, min_len: int) -> str:
    """frag 与 pool_line 的最长连续公共片段。"""
    best = ""
    for i in range(len(frag)):
        for j in range(len(frag), i + min_len - 1, -1):
            piece = frag[i:j]
            if len(piece) < min_len:
                break
            if piece in pool_line and len(piece) > len(best):
                best = piece
    return best


def load_lines(lines_dir: Path) -> list[tuple[int, float, str]]:
    """读台词库 → [(集号, 秒, 台词)]。"""
    out: list[tuple[int, float, str]] = []
    for f in sorted(lines_dir.glob("lines_ep*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        ep = int(d.get("ep") or 0)
        for ln in d.get("lines", []) or []:
            t = ln.get("t", 0)
            txt = norm_text(ln.get("text", ""))
            if txt:
                out.append((ep, float(t), txt))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="解说词跨段回声检测")
    ap.add_argument("--narration", required=True, help="解说配置.json")
    ap.add_argument("--drama", default="", help="剧名")
    ap.add_argument("--lines", default="", help="台词库目录（优先于 --drama）")
    ap.add_argument("--min-len", type=int, default=4, help="真复述判定阈值（默认 4）")
    ap.add_argument("--cut-eps", default="", help="本片用到的集号，逗号分隔；落在这些集里的撞词算硬错")
    ap.add_argument("--quiet", action="store_true", help="只报 ≥min-len 的，隐藏巧合级")
    args = ap.parse_args()

    cut_eps: set[int] = set()
    for part in (args.cut_eps or "").replace("，", ",").split(","):
        part = part.strip()
        if part.isdigit():
            cut_eps.add(int(part))

    root = _data_root()
    if args.lines:
        lines_dir = Path(args.lines)
    elif args.drama:
        lines_dir = root / "assets" / "short-drama" / args.drama / "lines"
    else:
        print("要给 --drama 或 --lines", file=sys.stderr)
        return 2

    if not lines_dir.is_dir():
        print(f"台词库目录不存在：{lines_dir}", file=sys.stderr)
        return 2

    pool = load_lines(lines_dir)
    if not pool:
        print(f"台词库是空的：{lines_dir}", file=sys.stderr)
        return 2

    cfg = json.loads(Path(args.narration).read_text(encoding="utf-8"))
    texts = cfg.get("texts") or []
    cues = cfg.get("cues") or []

    warn_len = min(args.min_len, 3)
    fails = 0
    warns = 0

    print("=== 解说词跨段回声检测 ===")
    print(f"台词库：{lines_dir.name}（{len(pool)} 句）　解说：{len(texts)} 段")
    if cut_eps:
        print(f"本片集号：{','.join(str(e) for e in sorted(cut_eps))}（落在这些集里的撞词算硬错）")
    print()

    for i, text in enumerate(texts):
        norm = norm_text(text)
        label = cues[i].get("label") or cues[i].get("seg_label") or f"第{i + 1}段" if i < len(cues) else f"第{i + 1}段"
        hits: list[tuple[int, float, str, str]] = []
        for ep, t, line in pool:
            piece = best_hit(norm, line, warn_len)
            if piece:
                hits.append((ep, t, line, piece))
        if not hits:
            print(f"  第 {i + 1}段 ✓  {label}")
            continue

        # 硬错：≥min_len；或者落在本片集里（观众一定听两遍）
        hard = [h for h in hits if len(h[3]) >= args.min_len or h[0] in cut_eps]
        soft = [h for h in hits if h not in hard]
        if hard:
            fails += len(hard)
            print(f"  第 {i + 1}段 ✗  {label}")
            for ep, t, line, piece in hard:
                mark = "〔本片集〕" if ep in cut_eps and len(piece) < args.min_len else ""
                print(f"      撞「{piece}」{mark}→ ep{ep} {t:.0f}s 原话：{line}")
        elif soft and not args.quiet:
            warns += len(soft)
            print(f"  第 {i + 1}段 △  {label}（巧合级 {warn_len} 字，不算错）")
            for ep, t, line, piece in soft:
                print(f"      疑似「{piece}」→ ep{ep} {t:.0f}s 原话：{line}")
        else:
            print(f"  第 {i + 1}段 ✓  {label}")

    print()
    if fails:
        print(f"✗ 有 {fails} 处要改的撞词（≥{args.min_len} 字，或落在本片集里），得改词。")
        return 1
    print(f"✓ 没有要改的撞词。" + (f"另有 {warns} 处巧合级 3 字命中。" if warns else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
