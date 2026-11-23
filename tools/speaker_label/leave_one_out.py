# -*- coding: utf-8 -*-
"""留一法验证：轮流把每条样本当"未知句"，看能否被归到正确说话人。
这是最贴近实际使用的检验——比对平均相似度更能反映真会不会判错。"""
import json
import numpy as np
import common as C
from speechbrain.inference.speaker import EncoderClassifier

meta = json.loads((C.DATA_DIR / "sample_meta.json").read_text(encoding="utf-8"))
clf = C.load_classifier()
E = []
for i, m in enumerate(meta):
    w = C.SAMPLES_DIR / f"sample_{i:02d}_{m['who']}.wav"
    E.append(np.asarray(C.emb_of_wav(clf, w)))

names = sorted(set(m["who"] for m in meta))
print(f"\n===== 留一法验证（共 {len(meta)} 条样本）=====")
correct = 0
confusions = {}
for i, m in enumerate(meta):
    # 用其余样本给每个说话人算中心（不含自己这条）
    centers = {}
    for w in names:
        idx = [k for k, mm in enumerate(meta) if mm["who"] == w and k != i]
        if idx:
            centers[w] = np.mean([E[k] for k in idx], axis=0)
    sims = {w: C.cosine(E[i], c) for w, c in centers.items()}
    pred = max(sims, key=sims.get)
    ok = pred == m["who"]
    correct += ok
    if not ok:
        confusions[(m["who"], pred)] = confusions.get((m["who"], pred), 0) + 1
    flag = "✓" if ok else "✗"
    print(f"  {flag} 实为{m['who']:<5} 判为{pred:<5} ep{m['ep']:02d} {m['text'][:22]}")

print(f"\n准确率 {correct}/{len(meta)} = {correct/len(meta)*100:.0f}%")
if confusions:
    print("混淆：", {f"{a}→{b}": n for (a, b), n in confusions.items()})
else:
    print("无混淆。")
