# -*- coding: utf-8 -*-
"""对抽好的存疑帧分批跑人脸识别，每批写一个结果文件，避免连续跑被环境杀。
用法: python face_doubt_batch.py <批号>   批号 0,1,2...
每批 60 帧，结果写 face_doubt/result_{批号}.csv
"""
import csv, sys
import face_lib as F
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRAMES = ROOT / "doubt_frames"
OUT = ROOT / "face_doubt"
OUT.mkdir(exist_ok=True)
BATCH = int(sys.argv[1]) if len(sys.argv) > 1 else 0
SIZE = 60

frames = sorted(FRAMES.glob("*.jpg"))
batch = frames[BATCH * SIZE:(BATCH + 1) * SIZE]
centers, _ = F.build_centers()

out = []
for p in batch:
    who, dist, verdict = F.recognize(p, centers)
    out.append({"frame": p.name, "who": who or "", "dist": f"{dist:.3f}" if dist is not None else "", "verdict": verdict})

with open(OUT / f"result_{BATCH}.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["frame", "who", "dist", "verdict"])
    w.writeheader()
    w.writerows(out)
print(f"批{BATCH}: {len(batch)}帧 -> {OUT/'result_'}{BATCH}.csv")
