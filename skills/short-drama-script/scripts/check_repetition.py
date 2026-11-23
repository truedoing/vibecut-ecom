#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解说稿同步复述率检测（2026-11-22 加，防机械感）

**查什么**：解说的字句里，有多少比例在重复**画面同期正在说的原话**。
观众等于把同一句话听了两遍——这就是「太机械感」的病根。

**怎么判**：把该段解说的文字，与该段画面同期原台词逐句比对，
**连续 3 个字以上相同**即算命中（3 字是「他妈的」「我知道」这种巧合级，4 字以上基本是复述）。

**时间轴怎么对**：按 plan 的段序累加出每段在成片里的 [起, 止]，
再用解说配置的 cue 起点 `at` 落到哪一段，决定这段解说配的是哪段画面——不靠人工对号。

用法：
    python3 check_repetition.py --plan runs/.../plan_v2.json --narration runs/.../解说配置.json

参数：
    --limit   全片平均上限（默认 0.08＝8%）
    --seg-limit 单段上限（默认 0.15＝15%）

退出码：0 = 全过；1 = 有超标段（供流水线卡关用）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.resolve().parents[2]

# 句子表 / 镜头表所在的数据根（跟其它技能一致：环境变量 → 往上找工作台 → 默认目录）
try:
    sys.path.insert(0, str(HERE))
    from _env import data_root as _data_root  # type: ignore
except Exception:
    def _data_root() -> Path:
        return ROOT

# 时间轴算法在 trailer 技能里有一份唯一真源，本脚本 import 它，不自己抄一份。
# （2026-11-23 修：以前这里抄了第三份，改装配器忘了改这里，
#   结果检测器把解说对到了错的段上，「复述率」报的是假数据。）
_TL_DIR = ROOT / "skills" / "short-drama-trailer" / "scripts"
if str(_TL_DIR) not in sys.path:
    sys.path.insert(0, str(_TL_DIR))
try:
    from timeline import build_timeline as _build_timeline  # type: ignore
except Exception:  # pragma: no cover
    sys.exit("找不到 timeline.py（short-drama-trailer/scripts/）——时间轴算法必须共用一份")


def build_timeline(plan: dict):
    """成片时间轴——转发到唯一真源，保留旧函数名给外部调用。"""
    return _build_timeline(plan)


def norm_text(s: str) -> str:
    """去掉标点与空白，只留字——比对时标点差异不算复述。"""
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s or "")


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"[。！？；，、\n—\.\!\?;:,\-]+", text or "")
    return [p.strip() for p in parts if len(p.strip()) >= 3]


def longest_hit(frag: str, pool: str, min_len: int = 3) -> str:
    """frag 里出现在 pool 中的最长连续片段。"""
    best = ""
    for i in range(len(frag)):
        for j in range(len(frag), i + min_len - 1, -1):
            piece = frag[i:j]
            if len(piece) < min_len:
                break
            if piece in pool:
                if len(piece) > len(best):
                    best = piece
                break
    return best


def load_orig_lines(drama: str, ep: int, a: float, b: float) -> str:
    """取该集该时间段的原台词（句级表优先，没有就退回台词库）。"""
    sent = _data_root() / "assets" / "short-drama" / drama / "sentences" / f"sentences_ep{ep:02d}.json"
    if sent.exists():
        d = json.loads(sent.read_text(encoding="utf-8"))
        rows = [r.get("text", "") for r in d.get("rows", []) if a <= float(r.get("t", -1)) <= b]
        if rows:
            return " ".join(rows)
    lines = _data_root() / "assets" / "short-drama" / drama / "lines" / f"lines_ep{ep:02d}.json"
    if lines.exists():
        d = json.loads(lines.read_text(encoding="utf-8"))
        rows = [l.get("text", "") for l in d.get("lines", []) if a <= float(l.get("t", -1)) <= b]
        if rows:
            return " ".join(rows)
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, help="剪辑清单 plan.json")
    ap.add_argument("--narration", required=True, help="解说配置 json（需含 cues 与 texts）")
    ap.add_argument("--limit", type=float, default=0.08, help="全片平均上限，默认 0.08")
    ap.add_argument("--seg-limit", type=float, default=0.15, help="单段上限，默认 0.15")
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    narr = json.loads(Path(args.narration).read_text(encoding="utf-8"))
    cues = narr.get("cues") or []
    texts = narr.get("texts") or []
    if not texts:
        sys.exit("解说配置里没有 texts 字段——把每段解说词按 cues 顺序放进去（只测实际念出来的字）")

    drama = plan.get("src_dir") or plan.get("drama") or ""
    tl = build_timeline(plan)

    print(f"=== 同步复述率检测 · {plan.get('title', '')} ===")
    print(f"清单 {len(tl)} 段（含转场），解说 {len(cues)} 段\n")

    rates, bad = [], 0
    for i, cue in enumerate(cues):
        text = texts[i] if i < len(texts) else ""
        if not text:
            continue
        at = float(cue.get("at", 0))
        # 找这条解说落在哪个画面段里（转场黑场不算画面）
        host = None
        for it in tl:
            if it["kind"] == "seg" and it["start"] <= at < it["end"]:
                host = it
                break
        if not host:
            print(f"  第{i + 1}段  ⚠️ 时间 {at:.2f}s 没落在任何画面段里，跳过")
            continue
        seg = host["seg"]
        if seg.get("type") == "broll":
            # 垫画面段：原声是静音的，观众听不到那些原话——
            # 不构成「同一句听两遍」，不算复述。
            # 但画面上角色在说话，解说别复述他嘴上的字（还是查，只是不计入平均）。
            pool = " ".join(
                load_orig_lines(drama, int(c["ep"]), float(c["a"]), float(c["b"]))
                for c in (seg.get("clips") or []))
            hits, covered = [], 0
            clean_all = norm_text(text)
            for sent in split_sentences(text):
                m = longest_hit(norm_text(sent), norm_text(pool))
                if m:
                    hits.append(m)
                    covered += len(m)
            rate = covered / len(clean_all) if clean_all else 0.0
            flag = "✗ 超标" if rate > args.seg_limit else ("△ 偏高" if rate > args.limit else "✓")
            if rate > args.seg_limit:
                bad += 1
            print(f"  第{i + 1:2d}段 {flag}  {str(seg.get('label'))[:22]:24s} "
                  f"垫画面复述率 {rate * 100:5.1f}%  "
                  f"命中：{'｜'.join(hits[:3]) if hits else '（无）'}")
            continue
        origin = load_orig_lines(drama, int(seg["ep"]), float(seg["a"]), float(seg["b"]))
        pool = norm_text(origin)
        hits, covered = [], 0
        clean_all = norm_text(text)
        for sent in split_sentences(text):
            m = longest_hit(norm_text(sent), pool)
            if m:
                hits.append(m)
                covered += len(m)
        rate = covered / len(clean_all) if clean_all else 0.0
        rates.append(rate)
        flag = "✗ 超标" if rate > args.seg_limit else ("△ 偏高" if rate > args.limit else "✓")
        if rate > args.seg_limit:
            bad += 1
        label = seg.get("label", seg.get("ep"))
        print(f"  第{i + 1:2d}段 {flag}  {str(label)[:22]:24s} 复述率 {rate * 100:5.1f}%  "
              f"命中：{'｜'.join(hits[:3]) if hits else '（无）'}")

    avg = sum(rates) / len(rates) if rates else 0.0
    print(f"\n全片平均同步复述率：{avg * 100:.1f}%（上限 {args.limit * 100:.0f}%）")
    if avg > args.limit:
        print("✗ 平均超标——解说在重复画面同期原话，观众会觉得「他在重复我」。")
        print("  改法：把复述换成「说画面」（动作/表情/物件）或「说内心」（动机/言外之意），")
        print("        钉子句改成留白不配解说，让原声自己说。")
        return 1
    if bad:
        print(f"⚠️ 有 {bad} 段单段超标（上限 {args.seg_limit * 100:.0f}%），逐段改。")
        return 1
    print("✓ 过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
