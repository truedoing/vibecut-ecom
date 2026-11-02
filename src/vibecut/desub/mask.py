"""字形级 mask：只擦字幕的笔画 + 描边，不整框抹掉。

整框 mask 在字幕压在包装上时会把包装字一起擦掉；字形 mask 只覆盖
白色字芯和紧贴字芯的深色描边，再向外膨胀 3–5px 覆盖抗锯齿。
"""
from __future__ import annotations

import cv2
import numpy as np


def _bbox(b: dict, W: int, H: int, pad: int = 2):
    x1 = max(0, int(b["x1"]) - pad)
    y1 = max(0, int(b["y1"]) - pad)
    x2 = min(W, int(b["x2"]) + pad + 1)
    y2 = min(H, int(b["y2"]) + pad + 1)
    return x1, y1, x2, y2


def glyph_mask(frame: np.ndarray, b: dict, pad: int = 4) -> np.ndarray:
    """返回整帧大小的 uint8 mask（255=要修复）。"""
    H, W = frame.shape[:2]
    x1, y1, x2, y2 = _bbox(b, W, H)
    out = np.zeros((H, W), np.uint8)
    if x2 <= x1 or y2 <= y1:
        return out

    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    v, s = hsv[:, :, 2], hsv[:, :, 1]
    white = ((v >= 165) & (s <= 100)).astype(np.uint8)
    dark = (v <= 85).astype(np.uint8)

    box_area = (x2 - x1) * (y2 - y1)
    white_ratio = float(white.sum()) / max(box_area, 1)

    if white.sum() >= 8:
        # 深色描边：紧贴白色字芯的暗像素
        ring = cv2.dilate(white, np.ones((5, 5), np.uint8)) & (1 - white)
        glyph = (white | (dark & ring)).astype(np.uint8)
        glyph = cv2.dilate(glyph, np.ones((2 * pad + 1, 2 * pad + 1), np.uint8))
    else:
        glyph = np.zeros_like(white)

    ratio = float(glyph.sum()) / max(box_area, 1)
    # 字形过少（黄字/花字）或过多（背景就是黑/白）→ 退回整框，保证擦干净
    if white_ratio < 0.02 or ratio < 0.05 or ratio > 0.90:
        glyph = np.ones((y2 - y1, x2 - x1), np.uint8)
        glyph = cv2.dilate(glyph, np.ones((2 * pad + 1, 2 * pad + 1), np.uint8))

    out[y1:y2, x1:x2] = glyph * 255
    return out


def combine(frame: np.ndarray, boxes: list[dict], pad: int = 4) -> np.ndarray:
    H, W = frame.shape[:2]
    m = np.zeros((H, W), np.uint8)
    for b in boxes:
        m = cv2.bitwise_or(m, glyph_mask(frame, b, pad=pad))
    return m


def draw_overlay(frame: np.ndarray, mask: np.ndarray, keep_boxes: list[dict],
                 subtitle_boxes: list[dict]) -> np.ndarray:
    """红=将被擦除的字幕像素，绿=识别为包装/UI 而保留的文本框。"""
    out = frame.copy()
    red = np.zeros_like(out)
    red[:, :, 2] = 255
    sel = mask > 0
    out[sel] = cv2.addWeighted(out, 0.25, red, 0.75, 0)[sel]

    for b in keep_boxes:
        cv2.rectangle(out, (int(b["x1"]), int(b["y1"])),
                      (int(b["x2"]), int(b["y2"])), (0, 220, 0), 2)
    for b in subtitle_boxes:
        cv2.rectangle(out, (int(b["x1"]), int(b["y1"])),
                      (int(b["x2"]), int(b["y2"])), (0, 255, 255), 1)
    return out
