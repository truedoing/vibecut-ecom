# -*- coding: utf-8 -*-
"""诊断：逐条样本两两相似度，找出是哪段样本拖累了区分度。"""
import json
import numpy as np
import common as C
from speechbrain.inference.speaker import EncoderClassifier

meta = json.loads((C.DATA_DIR / "sample_meta.json").read_text(encoding="utf-8"))
clf = C.load_classifier()
embs = []
for i, m in enumerate(meta):
    wav = C.SAMPLES_DIR / f"sample_{i:02d}_{m['who']}.wav"
    embs.append(np.asarray(C.emb_of_wav(clf, wav)))

print("\n===== 样本两两相似度矩阵（越接近1越像同一个人）=====")
hdr = "     " + " ".join(f"{i:>5}" for i in range(len(meta)))
print(hdr)
for i in range(len(meta)):
    row = " ".join(f"{C.cosine(embs[i], embs[j]):>5.2f}" for j in range(len(meta)))
    print(f"{i:>3} {row}  {meta[i]['who']:<5} ep{meta[i]['ep']:02d} {meta[i]['text'][:18]}")

print("\n===== 每段样本跟4个说话人均值的距离 =====")
names = sorted(set(m["who"] for m in meta))
centers = {w: np.mean([embs[i] for i, m in enumerate(meta) if m["who"] == w], axis=0) for w in names}
for i, m in enumerate(meta):
    sims = " ".join(f"{w}:{C.cosine(embs[i], centers[w]):.2f}" for w in names)
    print(f"{i:>3} {m['who']:<5} ep{m['ep']:02d} | {sims}")
