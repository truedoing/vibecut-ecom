# -*- coding: utf-8 -*-
"""对存疑句按发声窗口中点抽一帧画面，供本地人脸识别比对。
源：output/说话人表.csv 的存疑句；视频：config.json 的 source_dir。
抽帧按每句 t 到下一句起点之间的中点，尽量落在本人说话的镜头。
"""
import csv
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT.parent / "speaker_label" / "config.json").read_text(encoding="utf-8"))
SRC = Path(CFG["source_dir"])
TABLE = ROOT.parent / "speaker_label" / "output" / "说话人表.csv"
FRAMES = ROOT / "frames"
LINES_DIR = Path(CFG["lines_dir"])
FFMPEG = "/opt/anaconda3/bin/ffmpeg"
MAX_EP = int(sys.argv[1]) if len(sys.argv) > 1 else 6  # 默认试点前6集


def lines(ep):
    p = LINES_DIR / f"lines_ep{ep:02d}.json"
    return json.loads(p.read_text(encoding="utf-8")).get("lines", []) if p.exists() else []


def next_t(ls, t):
    for i, l in enumerate(ls):
        if abs(float(l["t"]) - float(t)) < 0.01 and i + 1 < len(ls):
            return float(ls[i + 1]["t"])
    return None


rows = [r for r in csv.DictReader(open(TABLE, encoding="utf-8-sig"))
        if r["doubt"].strip() and int(r["ep"]) <= MAX_EP]
cache = {}
jobs = []
for r in rows:
    ep = int(r["ep"])
    if ep not in cache:
        cache[ep] = lines(ep)
    nt = next_t(cache[ep], r["t"])
    gap = (nt - float(r["t"])) if nt else 2.4
    mid = float(r["t"]) + max(min(gap, 2.4), 0.8) / 2
    name = f"ep{ep:02d}_{float(r['t']):06.1f}s_{r['who']}_{abs(hash(r['text']))%10000:04d}.jpg"
    jobs.append((ep, mid, name, r))


def extract(j):
    ep, mid, name, r = j
    dst = FRAMES / name
    src = SRC / f"{ep}.mp4"
    cmd = [FFMPEG, "-y", "-v", "error", "-ss", f"{mid:.3f}", "-i", str(src),
           "-frames:v", "1", "-q:v", "3", str(dst)]
    p = subprocess.run(cmd, capture_output=True)
    return name, p.returncode == 0 and dst.exists()


ok = 0
with ThreadPoolExecutor(max_workers=8) as ex:
    futs = {ex.submit(extract, j): j for j in jobs}
    for f in as_completed(futs):
        name, good = f.result()
        ok += good
        if not good:
            print("失败", name)
print(f"试点前{MAX_EP}集：存疑句 {len(jobs)}，抽帧成功 {ok}")
