"""文字框特征 + 分类：口播字幕 / 商品包装 / 价格 / UI / 其它。

分类原则：**宁可保留，不可误删**。
只有同时具备「白字黑描边」「水平」「尺寸像字幕」「内容不是包装标签」
才判为 subtitle；包装词库命中的一律保留。
"""
from __future__ import annotations

import re

import cv2
import numpy as np

# ── 包装/标签词库（命中即倾向保留）──
PACKAGING_WORDS = (
    "配料", "配料表", "营养成分", "净含量", "生产日期", "保质期", "储存", "贮存",
    "厂商", "有限公司", "地址", "电话", "执行标准", "生产许可", "固体饮料",
    "膳食纤维", "蛋白质", "脂肪", "碳水化合物", "碳水", "钠", "维生素", "能量",
    "千焦", "毫克", "克/100", "g/100", "mg", "GB", "SC", "保质",
    "KALE", "POWDER", "冲泡", "入料", "搅拌", "饮用",
)

# ── 电商 UI / 价格词库（保留）──
UI_WORDS = (
    "￥", "¥", "实付", "价格", "已选", "数量", "规格", "配送", "送达", "支付",
    "优惠", "满", "减", "券", "月付", "分期", "手续费", "限购", "确认收货",
    "包邮", "运费险", "号码保护", "备注", "下单", "购买", "抖音", "购物车",
    "件", "期", "元",
)

_CJK = re.compile(r"[一-鿿]")
_EN = re.compile(r"[A-Za-z]")
_NUMERIC_UNIT = re.compile(
    r"\d+(\.\d+)?\s*(g|mg|kg|ml|l|kJ|千焦|克|毫克|毫升|袋|条|盒|瓶|%)", re.I)
_PRODUCT_WORD = re.compile(r"(麦谷村|羽衣甘蓝粉|KALE|POWDER)", re.I)
_WATERMARK = re.compile(r"(万嘉时刻|Wan Jia Moment|Moment)", re.I)

# 包装/法务/营养信息：即使白字描边很强也不能当字幕
_PACKAGING_LIKE = re.compile(
    r"(线[:：]|电话|地址|有限公司|生产|保质|净含量|执行标准|GB/?T|SC\d|"
    r"见盒身|喷码|不适宜|贮藏|贮存|营养成分|NRV|每100g|项目|配料[:：]|"
    r"冲泡方法|食用方法|产品类型|固体饮料|阳光直射|阴凉|干燥|通风|密封保存|"
    r"即可享用|搅拌均匀|轻松一刻|温水|牛奶或)"
)
_NUTRIENT_LABEL = re.compile(
    r"^(脂肪|蛋白质|碳水化合物|膳食纤维|能量|钠|维生素C?|钾|钙|铁|碳水|项目|NRV%?|高膳食纤维|高纤)$"
)
_UI_LIKE = re.compile(
    r"(已购买|已售|近\d+天|人已|券后|到手价|限时|包邮|运费险|正在看|人正在|"
    r"\d+[万千]?\+?人|正品保障|旗舰店|旗舰|抖音旗舰)")
_COLLOQUIAL = re.compile(
    r"[的了是就有才吧呢啊我你他她它这那不很都也还要能会把给让到在和跟说看喝吃买试真太]"
)


def packaging_like(text: str) -> bool:
    """文字内容是否明显像包装/法务/营养标签。"""
    t = (text or "").strip()
    compact = re.sub(r"[\s，。！？、,.!?：:;；\"'“”‘’（）()\[\]【】—\-~·]+", "", t)
    if _PACKAGING_LIKE.search(t):
        return True
    if _NUTRIENT_LABEL.match(compact):
        return True
    # 纯数字（标号/条码/被切碎的营养表数字）
    if compact.isdigit() and len(compact) <= 6:
        return True
    if _NUMERIC_UNIT.search(t) and len(t) <= 24 and not _COLLOQUIAL.search(t):
        return True
    # 长数字（条码/标准号/电话）且不含口播虚词
    if re.search(r"\d{4,}", t) and len(t) <= 18 and not _COLLOQUIAL.search(t):
        return True
    # 短产品名（没有口播虚词）→ 包装上的品名
    if len(t) <= 10 and _PRODUCT_WORD.search(t) and not _COLLOQUIAL.search(t):
        return True
    return False


def is_watermark(text: str) -> bool:
    return bool(_WATERMARK.search(text or ""))


def ui_like(text: str) -> bool:
    return bool(_UI_LIKE.search(text or ""))


def _hit(text: str, words) -> list[str]:
    return [w for w in words if w in text]


def _crop(frame: np.ndarray, d: dict, pad: int = 3):
    H, W = frame.shape[:2]
    x1 = max(0, int(d["x1"]) - pad)
    y1 = max(0, int(d["y1"]) - pad)
    x2 = min(W, int(d["x2"]) + pad + 1)
    y2 = min(H, int(d["y2"]) + pad + 1)
    if x2 <= x1 or y2 <= y1:
        return None, None
    return frame[y1:y2, x1:x2], (x1, y1, x2, y2)


def stroke_features(frame: np.ndarray, d: dict) -> dict:
    """检测「白字 + 深色描边」。描边 = 白色字形的邻域里出现大量深色像素。

    注意：白字黑底（UI）也会得到高分，所以还要结合位置/内容判断。
    """
    crop, box = _crop(frame, d)
    if crop is None:
        return {"stroke": 0.0, "white_ratio": 0.0, "dark_ratio": 0.0, "bg_std": 0.0}
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    v, s = hsv[:, :, 2], hsv[:, :, 1]
    white = ((v >= 170) & (s <= 95)).astype(np.uint8)
    dark = (v <= 80).astype(np.uint8)
    n = white.size
    if white.sum() < 8:
        return {"stroke": 0.0, "white_ratio": float(white.sum() / n),
                "dark_ratio": float(dark.sum() / n), "bg_std": 0.0}

    white_ratio = float(white.sum() / n)
    if white.sum() < 8 or white_ratio < 0.02:
        # 深色字/浅色底（包装上的黑字）不应被当成「白字描边」
        return {"stroke": 0.0, "white_ratio": round(white_ratio, 4),
                "dark_ratio": round(float(dark.sum() / n), 4), "bg_std": 0.0}

    # 白字外圈 2px 内有多少深色像素
    ring = cv2.dilate(white, np.ones((5, 5), np.uint8)) & (1 - white)
    adj_dark = int((dark & ring).sum())
    stroke = min(3.0, adj_dark / max(int(white.sum()), 1))

    # 背景（排除白/深色字形）亮度标准差：字幕背景通常比包装更杂
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    bg = gray[(white == 0) & (dark == 0)]
    bg_std = float(bg.std()) if bg.size > 20 else 0.0
    return {
        "stroke": round(float(stroke), 4),
        "white_ratio": round(float(white.sum() / n), 4),
        "dark_ratio": round(float(dark.sum() / n), 4),
        "bg_std": round(bg_std, 2),
    }


def box_label(frame: np.ndarray, d: dict, W: int, H: int) -> dict:
    """单框的初始分类（track 级还会再投票一次）。"""
    text = d.get("text") or ""
    conf = d.get("conf") or 0.0
    w, h = max(d["w"], 1.0), max(d["h"], 1.0)
    feat = stroke_features(frame, d)
    d.update(feat)

    vertical = h > 1.25 * w or d.get("angle", 0) > 45
    horizontal = d.get("angle", 0) <= 10 and w > h
    h_ratio = h / max(H, 1)
    centered = abs(d["cx"] / max(W, 1) - 0.5) < 0.22
    edge_fragment = (
        (d["x1"] <= 0.02 * W and d["x2"] <= 0.35 * W)
        or (d["x2"] >= 0.98 * W and d["x1"] >= 0.65 * W)
    ) and len(text) <= 8

    p_hits = _hit(text, PACKAGING_WORDS)
    u_hits = _hit(text, UI_WORDS)
    cjk_n = len(_CJK.findall(text))
    en_n = len(_EN.findall(text))
    label, reason = "unknown", ""

    if vertical:
        label, reason = "packaging", "竖排/大角度"
    elif is_watermark(text):
        label, reason = "other", "水印/logo（保留）"
    elif en_n >= 6 and cjk_n == 0:
        label, reason = "other", "纯英文（歌曲/屏幕）"
    elif packaging_like(text):
        label, reason = "packaging", "包装/法务/营养文本"
    elif ui_like(text):
        label, reason = "ui", "电商浮层文字"
    elif edge_fragment:
        label, reason = "packaging", "画面边缘截断文字（保留）"
    elif (
        horizontal
        and feat["stroke"] >= 0.30
        and feat["white_ratio"] >= 0.04
        and 0.012 <= h_ratio <= 0.16
        and conf >= 0.45
    ):
        # 样式强证据优先于词库：字幕本身可能含「膳食纤维」「数量」等词
        label = "subtitle"
        extra = f" 词:{p_hits[:2]}" if p_hits else ""
        reason = (f"白字描边 stroke={feat['stroke']:.2f} h={h_ratio:.3f} "
                  f"centered={centered}{extra}")
    elif p_hits:
        label, reason = "packaging", f"包装词:{p_hits[:3]}"
    elif u_hits:
        label, reason = "ui", f"UI词:{u_hits[:3]}"
    else:
        reason = (f"未命中: stroke={feat['stroke']:.2f} "
                  f"white={feat['white_ratio']:.2f} h={h_ratio:.3f} "
                  f"conf={conf:.2f}")

    d["label"] = label
    d["label_reason"] = reason
    return d


def merge_subtitle_lines(blocks: list[dict], frame: np.ndarray, W: int, H: int) -> list[dict]:
    """把同一句字幕的两行合并成一个 block（保留其它框不动）。"""
    subs = [b for b in blocks if b.get("label") == "subtitle"]
    others = [b for b in blocks if b.get("label") != "subtitle"]
    subs = sorted(subs, key=lambda b: b["y1"])

    merged = True
    while merged and len(subs) > 1:
        merged = False
        for i in range(len(subs)):
            for j in range(i + 1, len(subs)):
                a, b = subs[i], subs[j]
                ha, hb = a["h"], b["h"]
                gap = max(0.0, b["y1"] - a["y2"])
                x_overlap = max(0.0, min(a["x2"], b["x2"]) - max(a["x1"], b["x1"]))
                min_w = max(1.0, min(a["w"], b["w"]))
                if (
                    gap <= 1.1 * max(ha, hb)
                    and x_overlap / min_w >= 0.25
                    and 0.6 <= ha / max(hb, 1e-3) <= 1.7
                    and abs(a["cx"] - b["cx"]) <= 0.35 * W
                ):
                    x1, y1 = min(a["x1"], b["x1"]), min(a["y1"], b["y1"])
                    x2, y2 = max(a["x2"], b["x2"]), max(a["y2"], b["y2"])
                    m = dict(a)
                    m.update({"x1": x1, "y1": y1, "x2": x2, "y2": y2,
                              "w": x2 - x1, "h": y2 - y1,
                              "cx": (x1 + x2) / 2, "cy": (y1 + y2) / 2,
                              "text": (a.get("text", "") + b.get("text", "")).strip(),
                              "conf": min(a.get("conf", 0), b.get("conf", 0)),
                              "lines": a.get("lines", [a]) + b.get("lines", [b])})
                    subs[i] = m
                    subs.pop(j)
                    merged = True
                    break
                if merged:
                    break
            if merged:
                break

    # 合并后的 block 用整体框重算特征
    for m in subs:
        feat = stroke_features(frame, m)
        m.update(feat)
        m["label"] = "subtitle"
        m["label_reason"] = (f"合并{subtitle_line_count(m)}行 "
                             f"stroke={feat['stroke']:.2f}")
    return subs + others


def subtitle_line_count(b: dict) -> int:
    return len(b.get("lines") or [b])


def annotate_frame(frame: np.ndarray, dets: list[dict]) -> list[dict]:
    """对一帧所有文字框做初始分类 + 字幕行合并。"""
    H, W = frame.shape[:2]
    blocks = [box_label(frame, d, W, H) for d in dets]
    return merge_subtitle_lines(blocks, frame, W, H)


def subtitle_texts(blocks: list[dict]) -> list[str]:
    out = []
    for b in blocks:
        if b.get("label") != "subtitle":
            continue
        for ln in (b.get("lines") or [b]):
            t = (ln.get("text") or "").strip()
            if t:
                out.append(t)
    return out
