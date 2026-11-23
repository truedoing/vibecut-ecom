# -*- coding: utf-8 -*-
"""把声纹表544句存疑，套定稿规则重判，产出修正表。
- 已有真值的68句：直接用张老师真值（最高优先，不动）
- 其余：声纹+人脸叠用定稿规则
  定稿规则：多数同意听多数；打平按 人脸>大模型>声纹（本轮无大模型，即打平听人脸）
  人脸认不出（no_face/unknown 或无帧）→ 退回声纹判断
- 原表非存疑的745句：完全不动
"""
import csv, re
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent
SPEAKER = ROOT.parent / "speaker_label" / "output" / "说话人表.csv"

def ep_t(ep, t):
    return f"{int(ep):02d}|{float(t):.1f}"

# 1. 人脸结果（分批文件合并）
face = {}
fd = ROOT / "face_doubt"
for p in sorted(fd.glob("result_*.csv")):
    for r in csv.DictReader(open(p, encoding="utf-8-sig")):
        m = re.match(r"ep(\d+)_([\d.]+)s\.jpg", r["frame"])
        if not m: continue
        k = ep_t(m.group(1), m.group(2))
        # 只采纳确定认出(ok)的；maybe/unknown/no_face 一律当认不出，退回声纹
        if r["verdict"] == "ok" and r["who"]:
            face[k] = (r["who"], r["verdict"], r["dist"])

# 2. 已有真值
truth = {}
for r in csv.DictReader(open(ROOT / "人工真值_张老师确认.csv", encoding="utf-8-sig")):
    ep = r["ep"].replace("ep", "").split(" ")[0]
    t = r["ep"].split("·")[-1].strip().replace("s", "")
    truth[ep_t(ep, t)] = r["人工真值"].strip()

MAIN = {"林秀兰", "许宁", "周承", "许晴"}
rows = list(csv.DictReader(open(SPEAKER, encoding="utf-8-sig")))

out = []
changed = 0
for r in rows:
    o = dict(r)
    if not r["doubt"].strip():
        o["最终判断"] = r["who"]
        o["依据"] = "原判(非存疑)"
        out.append(o)
        continue
    k = ep_t(r["ep"], r["t"])
    sv = r["who"]
    if k in truth and truth[k]:
        final = truth[k]
        basis = "人工真值"
    elif k in face:
        fa = face[k][0]
        # 定稿规则（无大模型层）：声纹=人脸直接定；打架听人脸
        final = fa
        basis = f"人脸优先(声纹{sv}→人脸{fa})"
    else:
        final = sv
        basis = "人脸认不出→退回声纹"
    o["最终判断"] = final
    o["依据"] = basis
    if final != sv:
        changed += 1
    out.append(o)

# 统计
dub = [o for o in out if o["依据"] != "原判(非存疑)"]
print(f"总 {len(out)} 句，存疑 {len(dub)} 句，其中改判 {changed} 句")
print("存疑句最终判断分布:", Counter(o["最终判断"] for o in dub))
print("依据分布:", Counter(o["依据"].split("(")[0] for o in dub))

with open(ROOT / "说话人表_修正后.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
    w.writeheader()
    w.writerows(out)
print("已写 说话人表_修正后.csv")
