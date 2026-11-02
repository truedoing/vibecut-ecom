"""自动质检：OCR 复检字幕残留 + 包装文字保留。"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

import cv2

from . import ocr

_PUNCT = re.compile(r"[\s，。！？、,.!?：:;；\"'“”‘’（）()\[\]【】—\-~·]+")


def _norm(t: str) -> str:
    return _PUNCT.sub("", t or "")


def _sim(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _read_at(video: str, times: list[float]) -> list[dict]:
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    out = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(t * fps))))
        ok, frame = cap.read()
        if not ok:
            continue
        out.append({"t": t, "texts": [d.get("text", "") for d in ocr.detect(frame)]})
    cap.release()
    return out


def check(result_video: str, times: list[float],
          removed_texts: list[str], packaging_texts: list[str]) -> dict:
    frames = _read_at(result_video, times)
    all_text = [t for f in frames for t in f["texts"]]

    residual = []
    for orig in removed_texts:
        if len(_norm(orig)) < 3:
            continue
        best = max((_sim(orig, t), t) for t in all_text) if all_text else (0.0, "")
        if best[0] >= 0.62:
            # 只匹配到原文的一小部分（如包装上的 PICC）→ 不是字幕残留
            coverage = len(_norm(best[1])) / max(len(_norm(orig)), 1)
            if coverage < 0.60:
                continue
            # 包装上本来就印着同样文字（例如产品名）：残留是正常的
            if any(_sim(orig, p) >= 0.80 for p in packaging_texts):
                continue
            residual.append({"original": orig, "matched": best[1],
                             "similarity": round(best[0], 3),
                             "coverage": round(coverage, 2)})

    missing_pkg = []
    pkg_checked = 0
    for pkg in packaging_texts:
        if len(_norm(pkg)) < 3:
            continue
        pkg_checked += 1
        best = max((_sim(pkg, t), t) for t in all_text) if all_text else (0.0, "")
        if best[0] < 0.45:
            missing_pkg.append({"original": pkg, "best_match": best[1],
                                "similarity": round(best[0], 3)})

    retention = (pkg_checked - len(missing_pkg)) / pkg_checked if pkg_checked else 1.0
    return {
        "checked_times": [f["t"] for f in frames],
        "result_text_count": len(all_text),
        "subtitle_residual": residual,
        "subtitle_residual_count": len(residual),
        "packaging_checked": pkg_checked,
        "packaging_missing": missing_pkg,
        "packaging_missing_count": len(missing_pkg),
        "packaging_retention": round(retention, 3),
        "pass": len(residual) == 0 and retention >= 0.70,
    }
