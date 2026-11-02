"""跨帧文本轨迹：把逐帧文字框连成 track，再在 track 级别判定字幕。

字幕特征（相对包装文字）：
  · 白字 + 深色描边，且描边得分稳定
  · 水平、居中、高度占画面 1.5%–17%
  · 同一位置带内文字会变化（口播一句接一句）
包装文字通常无描边、随物体移动、竖排或命中标签词。
"""
from __future__ import annotations

import re
from collections import Counter
from statistics import median

from . import classify

_CJK = re.compile(r"[一-鿿]")


def _match_score(tr: dict, b: dict, W: int, H: int) -> float | None:
    dy = abs(b["cy"] - tr["cy"])
    dx = abs(b["cx"] - tr["cx"])
    if dy > 0.35 * max(b["h"], tr["h"]):
        return None
    if dx > 0.20 * W:
        return None
    if not (0.60 <= b["h"] / max(tr["h"], 1e-3) <= 1.8):
        return None
    return dy / max(b["h"], 1.0) + 0.5 * dx / max(W, 1) + abs(b["h"] / max(tr["h"], 1e-3) - 1)


def build_tracks(samples: list[dict], W: int, H: int, max_gap: int = 2) -> list[dict]:
    """samples: [{"si": 采样序号, "t": 秒, "blocks": [...]}]"""
    tracks: list[dict] = []
    active: list[dict] = []

    for s in samples:
        si = s["si"]
        active = [t for t in active if si - t["last_si"] <= max_gap]
        used = set()
        for b in s["blocks"]:
            best, best_score = None, 1e9
            for t in active:
                if id(t) in used:
                    continue
                sc = _match_score(t, b, W, H)
                if sc is not None and sc < best_score:
                    best, best_score = t, sc
            bid = f"S{si}_{int(b['cx'])}_{int(b['cy'])}"
            b["bid"] = bid
            if best is None:
                t = {"tid": f"T{len(tracks):03d}", "blocks": [], "last_si": si,
                     "cx": b["cx"], "cy": b["cy"], "h": b["h"]}
                tracks.append(t)
                active.append(t)
                best = t
            used.add(id(best))
            b["tid"] = best["tid"]
            best["blocks"].append({"si": si, "t": s["t"], **b})
            # 用最近 block 的几何更新轨迹中心（字幕换行时宽度会变，中心更稳）
            best.update({"last_si": si, "cx": b["cx"], "cy": b["cy"], "h": b["h"]})

    for t in tracks:
        t.update(classify_track(t, W, H))
    return tracks


def classify_track(tr: dict, W: int, H: int) -> dict:
    blocks = tr["blocks"]
    n = len(blocks)
    labels = Counter(b.get("label", "unknown") for b in blocks)
    # 几何/样式特征只用「单独判定为字幕」的框，避免包装框污染轨迹
    sub_blocks = [b for b in blocks if b.get("label") == "subtitle"]
    feat_blocks = sub_blocks or blocks
    nf = len(feat_blocks)
    strokes = [b.get("stroke", 0.0) for b in feat_blocks]
    hr = [b["h"] / max(H, 1) for b in feat_blocks]
    horiz = sum(1 for b in feat_blocks if b.get("angle", 0) <= 10 and b["w"] > b["h"]) / nf
    centered = sum(1 for b in feat_blocks
                   if abs(b["cx"] / max(W, 1) - 0.5) < 0.24) / nf
    texts = {(b.get("text") or "").strip() for b in feat_blocks if (b.get("text") or "").strip()}
    zh = sum(1 for b in feat_blocks if _CJK.search(b.get("text") or "")) / nf
    stroke_med = float(median(strokes)) if strokes else 0.0
    h_med = float(median(hr)) if hr else 0.0

    packaging_frac = labels.get("packaging", 0) / n
    subtitle_frac = labels.get("subtitle", 0) / n
    ui_frac = labels.get("ui", 0) / n
    pkg_like_frac = sum(
        1 for b in blocks if classify.packaging_like(b.get("text") or "")
    ) / n

    if packaging_frac >= 0.40 or pkg_like_frac >= 0.40:
        label, reason = "packaging", (f"packaging_frac={packaging_frac:.2f} "
                                      f"pkg_like={pkg_like_frac:.2f}")
    elif (
        subtitle_frac >= 0.50
        and pkg_like_frac < 0.25
        and stroke_med >= 0.30
        and horiz >= 0.50
        and 0.010 <= h_med <= 0.18
        and centered >= 0.40
    ):
        label, reason = "subtitle", (
            f"sub_frac={subtitle_frac:.2f} stroke={stroke_med:.2f} "
            f"horiz={horiz:.2f} centered={centered:.2f} h={h_med:.3f}")
    elif subtitle_frac >= 0.25 and len(texts) >= 2 and centered >= 0.50 and zh >= 0.50:
        label, reason = "subtitle", f"字幕换行/换句 texts={len(texts)} centered={centered:.2f}"
    elif ui_frac >= 0.40:
        label, reason = "ui", f"ui_frac={ui_frac:.2f}"
    else:
        label, reason = "keep", f"labels={dict(labels)}"

    return {
        "label": label,
        "label_reason": reason,
        "n": n,
        "stroke_med": round(stroke_med, 3),
        "h_ratio_med": round(h_med, 4),
        "horizontal_frac": round(horiz, 3),
        "centered_frac": round(centered, 3),
        "unique_texts": len(texts),
        "texts": sorted(texts)[:6],
        "labels": dict(labels),
    }


def subtitle_boxes_at(tracks: list[dict], si: int, tol: int = 2) -> list[dict]:
    """取某个采样时刻（或最近 tol 个采样内）的字幕框。"""
    out = []
    for tr in tracks:
        if tr["label"] != "subtitle":
            continue
        best = None
        for b in tr["blocks"]:
            if b.get("label") != "subtitle":
                continue
            dt = abs(b["si"] - si)
            if dt <= tol and (best is None or dt < abs(best["si"] - si)):
                best = b
        if best is not None:
            out.append(best)
    return out


def subtitle_track_bands(tracks: list[dict], W: int, H: int) -> list[dict]:
    """字幕轨迹的位置带（用于采样帧之间匹配检测框）。"""
    bands = []
    for tr in tracks:
        if tr["label"] != "subtitle":
            continue
        subs = [b for b in tr["blocks"] if b.get("label") == "subtitle"] or tr["blocks"]
        xs = [b["cx"] for b in subs]
        ys = [b["cy"] for b in subs]
        hs = [b["h"] for b in subs]
        ws = [b["w"] for b in subs]
        bands.append({
            "tid": tr["tid"],
            "cx": float(median(xs)), "cy": float(median(ys)),
            "h": float(median(hs)), "w": float(median(ws)),
        })
    return bands


def box_in_band(b: dict, band: dict, W: int, H: int) -> bool:
    if abs(b["cy"] - band["cy"]) > 0.6 * max(b["h"], band["h"]):
        return False
    if abs(b["cx"] - band["cx"]) > max(0.22 * W, 0.75 * band["w"]):
        return False
    if not (0.45 <= b["h"] / max(band["h"], 1e-3) <= 2.2):
        return False
    return True
