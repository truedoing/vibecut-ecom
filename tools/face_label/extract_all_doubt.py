# -*- coding: utf-8 -*-
"""对全部544句存疑抽帧，跳过已有真值的68句。
帧命名用 ep+t 归一化，方便后面和声纹表、人脸识别结果对齐。
"""
import csv
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT.parent / "speaker_label" / "config.json").read_text(encoding="utf-8"))
SRC = Path(CFG["source_dir"])
TABLE = ROOT.parent / "speaker_label" / "output" / "说话人表.csv"
FRAMES = ROOT / "doubt_frames"
FRAMES.mkdir(exist_ok=True)
LINES_DIR = Path(CFG["lines_dir"])
FFMPEG = "/opt/anaconda3/bin/ffmpeg"

def key(ep, t):
    return f"{int(ep):02d}|{float(t):.1f}"

# 已有真值的68句，按 ep+t 跳过
truth_keys = set()
for r in csv.DictReader(open(ROOT / "人工真值_张老师确认.csv", encoding="utf-8-sig")):
    ep = r["ep"].replace("ep", "").split(" ")[0]
    t = r["ep"].split("·")[-1].strip().replace("s", "")
    truth_keys.add(key(ep, t))

def lines(ep):
    p = LINES_DIR / f"lines_ep{ep:02d}.json"
    return json.loads(p.read_text(encoding="utf-8")).get("lines", []) if p.exists() else []

def next_t(ls, t):
    for i, l in enumerate(ls):
        if abs(float(l["t"]) - float(t)) < 0.01 and i + 1 < len(ls):
            return float(ls[i + 1]["t"])
    return None

rows = [r for r in csv.DictReader(open(TABLE, encoding="utf-8-sig")) if r["doubt"].strip()]
cache = {}
jobs = []
skipped = 0
for r in rows:
    ep = int(r["ep"])
    if key(ep, r["t"]) in truth_keys:
        skipped += 1
        continue
    if ep not in cache:
        cache[ep] = lines(ep)
    nt = next_t(cache[ep], r["t"])
    gap = (nt - float(r["t"])) if nt else 2.4
    mid = float(r["t"]) + max(min(gap, 2.4), 0.8) / 2
    name = f"ep{ep:02d}_{float(r['t']):06.1f}s.jpg"
    jobs.append((ep, mid, name, r))

def extract(j):
    ep, mid, name, r = j
    dst = FRAMES / name
    if dst.exists() and dst.stat().st_size > 0:
        return name, True
    src = SRC / f"{ep}.mp4"
    cmd = [FFMPEG, "-y", "-v", "error", "-ss", f"{mid:.3f}", "-i", str(src),
           "-frames:v", "1", "-q:v", "3", str(dst)]
    p = subprocess.run(cmd, capture_output=True)
    return name, p.returncode == 0 and dst.exists()

ok = 0
fails = []
with ThreadPoolExecutor(max_workers=8) as ex:
    futs = {ex.submit(extract, j): j for j in jobs}
    for f in as_completed(futs):
        name, good = f.result()
        ok += good
        if not good:
            fails.append(name)
print(f"存疑总 {len(rows)}，已有真值跳过 {skipped}，需抽帧 {len(jobs)}，成功 {ok}，失败 {len(fails)}")
if fails:
    print("失败样本:", fails[:10])
