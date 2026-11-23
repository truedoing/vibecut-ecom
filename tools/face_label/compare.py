# -*- coding: utf-8 -*-
"""交叉比对：对每张存疑帧跑人脸识别，与声纹表原判比较。
输出：一致/打架/认不出 三类统计，以及明细 CSV。
关键——人脸只认"这张脸是谁"，不等于"这句话是谁说的"（画外音、镜头切到听者都会错配），
所以一致只算"旁证支持"，打架才提示这句要重点人工听。
"""
import csv
import face_lib as F
from pathlib import Path

ROOT = Path(__file__).resolve().parent
centers, _ = F.build_centers()
frames = sorted((ROOT / "frames").glob("*.jpg"))

# 帧名格式：ep06_0054.0s_许晴_9930.jpg -> 反推声纹判的 who
rows = []
stat = {"一致": 0, "打架": 0, "认不出": 0, "无脸": 0}
for p in frames:
    parts = p.stem.rsplit("_", 3)
    who_sv = parts[2]
    face, d, verdict = F.recognize(p, centers)
    if verdict == "no_face":
        cls = "无脸"
    elif verdict == "unknown":
        cls = "认不出"
    elif face == who_sv:
        cls = "一致"
    else:
        cls = "打架"
    stat[cls] += 1
    rows.append({"frame": p.name, "声纹判": who_sv, "人脸判": face or "",
                 "距离": f"{d:.3f}" if d is not None else "", "结果": cls})

out = ROOT / "比对结果.csv"
with open(out, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["frame", "声纹判", "人脸判", "距离", "结果"])
    w.writeheader()
    w.writerows(rows)

print("前6集存疑帧交叉比对：")
for k, v in stat.items():
    print(f"  {k}: {v}  ({v/len(frames)*100:.1f}%)")
print(f"  合计 {len(frames)} 帧")
print("\n打架的（人脸和声纹不一致，重点人工听）：")
for r in rows:
    if r["结果"] == "打架":
        print(f"  {r['frame'][:34]:<34} 声纹={r['声纹判']:<4} 人脸={r['人脸判']:<4} 距{r['距离']}")
