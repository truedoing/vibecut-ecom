# -*- coding: utf-8 -*-
"""分层抽样100句建真值表样本。
70存疑 + 30非存疑；按60集铺开；四类说话人尽量均衡（按声纹判断分层，避免全是许宁）。
固定种子，可复现。真值不是这里定的——只抽样，人工确认列留空。
"""
import csv
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TABLE = ROOT.parent / "speaker_label" / "output" / "说话人表.csv"
random.seed(20261122)

rows = list(csv.DictReader(open(TABLE, encoding="utf-8-sig")))
dub = [r for r in rows if r["doubt"].strip()]
sure = [r for r in rows if not r["doubt"].strip()]
NAMES = ["林秀兰", "许宁", "周承", "许晴"]


def stratified(pool, n):
    """按 who 四人均分，每人内部按集号铺开。"""
    by = defaultdict(list)
    for r in pool:
        by[r["who"]].append(r)
    picked = []
    per = n // 4
    for name in NAMES:
        group = by.get(name, [])
        if not group:
            continue
        # 每人取 per 句，优先覆盖不同集
        group.sort(key=lambda r: (int(r["ep"]), float(r["t"])))
        step = max(1, len(group) // per)
        sel = group[::step][:per]
        while len(sel) < per and len(sel) < len(group):
            for r in group:
                if r not in sel:
                    sel.append(r)
                    break
        picked.extend(sel)
    return picked


sample = stratified(dub, 70) + stratified(sure, 30)
sample.sort(key=lambda r: (int(r["ep"]), float(r["t"])))

out = ROOT / "真值样本.csv"
fields = ["id", "ep", "t", "who_声纹", "doubt", "text", "conf", "gap", "runner_up"]
with open(out, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    for i, r in enumerate(sample):
        w.writerow({"id": i, "ep": r["ep"], "t": r["t"], "who_声纹": r["who"],
                    "doubt": r["doubt"], "text": r["text"], "conf": r["conf"],
                    "gap": r["gap"], "runner_up": r["runner_up"]})

from collections import Counter
print("抽样", len(sample), "句")
print("存疑/非存疑", sum(1 for r in sample if r["doubt"].strip()), "/", sum(1 for r in sample if not r["doubt"].strip()))
print("说话人分布", Counter(r["who"] for r in sample))
print("覆盖集数", len(set(r["ep"] for r in sample)))
