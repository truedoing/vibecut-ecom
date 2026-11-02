"""RapidOCR 封装：完整识别 + 仅检测两种模式。

RapidOCR 返回的坐标是原图像素四边形 [tl, tr, br, bl]。
检测-only 模式用于 OCR 采样帧之间的过渡帧，保证 mask 跟得上字幕移动。
"""
from __future__ import annotations

import numpy as np

_engine = None


def engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    return _engine


def _normalize(box, text: str, conf: float, rec: bool) -> dict:
    quad = np.asarray(box, dtype=np.float32).reshape(-1, 2)
    x1, y1 = quad.min(axis=0)
    x2, y2 = quad.max(axis=0)
    w = float(x2 - x1)
    h = float(y2 - y1)
    # 水平度：上边与水平线的夹角
    if len(quad) >= 2:
        top = quad[:2]
        dx, dy = float(top[1][0] - top[0][0]), float(top[1][1] - top[0][1])
        angle = abs(float(np.degrees(np.arctan2(dy, dx)))) if (dx or dy) else 0.0
        if angle > 90:
            angle = 180 - angle
    else:
        angle = 0.0
    return {
        "quad": quad.tolist(),
        "text": (text or "").strip(),
        "conf": float(conf or 0.0),
        "x1": float(x1), "y1": float(y1), "x2": float(x2), "y2": float(y2),
        "w": w, "h": h,
        "cx": float((x1 + x2) / 2), "cy": float((y1 + y2) / 2),
        "angle": angle,
        "rec": rec,
    }


def _run(img_bgr, rec: bool) -> list[dict]:
    kw = {} if rec else {"use_det": True, "use_cls": False, "use_rec": False}
    res, _ = engine()(img_bgr, **kw)
    out = []
    for item in res or []:
        if rec:
            box, txt, conf = item[0], item[1], item[2]
        else:
            # 仅检测模式直接返回四边形 [[x,y], ...]
            box, txt, conf = item, "", 0.0
        d = _normalize(box, txt, conf, rec)
        if d["w"] <= 1 or d["h"] <= 1:
            continue
        out.append(d)
    return out


def detect(img_bgr) -> list[dict]:
    """完整 OCR（检测 + 识别）。"""
    return _run(img_bgr, rec=True)


def detect_boxes(img_bgr) -> list[dict]:
    """仅检测（快），用于采样帧之间的过渡帧。"""
    return _run(img_bgr, rec=False)
