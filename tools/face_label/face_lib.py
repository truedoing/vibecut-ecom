# -*- coding: utf-8 -*-
"""本地人脸识别：用系统 Anaconda 里的 face_recognition（dlib），不用装新库。
样板 = 每人几张已知身份的正脸，算 128 维编码取平均当"脸尺子"。
待认帧算编码，跟四把尺子比欧氏距离，最近的当归属，距离过大判"认不出"。
"""
import face_recognition as fr
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "samples"
NAMES = ["林秀兰", "许宁", "周承", "许晴"]  # 与声纹表 who 列同名，避免别名造成假打架
# dlib 人脸距离经验阈值：0.5 以内基本同一人，0.6 放宽，超过判不认识
DIST_OK = 0.55
DIST_MAYBE = 0.68


def encodings(path):
    img = fr.load_image_file(str(path))
    locs = fr.face_locations(img, model="hog")
    if not locs:
        return []
    return fr.face_encodings(img, locs)


def build_centers():
    centers = {}
    detail = {}
    for name in NAMES:
        vecs = []
        for p in sorted((SAMPLES / name).glob("*.jpg")):
            es = encodings(p)
            if es:
                vecs.append(es[0])
            else:
                detail.setdefault(name, []).append(f"无脸:{p.name}")
        if vecs:
            centers[name] = np.mean(vecs, axis=0)
            detail[name] = detail.get(name, []) + [f"有效{len(vecs)}张"]
        else:
            detail[name] = detail.get(name, []) + ["全部无脸"]
    return centers, detail


def recognize(path, centers):
    es = encodings(path)
    if not es:
        return None, None, "no_face"
    e = es[0]
    dists = {n: float(np.linalg.norm(e - c)) for n, c in centers.items()}
    best = min(dists, key=dists.get)
    d = dists[best]
    if d <= DIST_OK:
        verdict = "ok"
    elif d <= DIST_MAYBE:
        verdict = "maybe"
    else:
        verdict = "unknown"
    return best, d, verdict
