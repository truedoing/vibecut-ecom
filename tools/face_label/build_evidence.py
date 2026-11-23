# -*- coding: utf-8 -*-
"""为真值样本准备三类证据：台词上下文、发声窗口音频、发声窗口画面帧。
音频/帧供人工核对；上下文供大模型推理层使用。
"""
import csv
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT.parent / "speaker_label" / "config.json").read_text(encoding="utf-8"))
SRC = Path(CFG["source_dir"])
LINES = Path(CFG["lines_dir"])
FFMPEG = "/opt/anaconda3/bin/ffmpeg"
AUD = ROOT / "truth_audio"
FRM = ROOT / "truth_frames"
AUD.mkdir(exist_ok=True)
FRM.mkdir(exist_ok=True)
TRUTH_CSV = ROOT / "真值样本.csv"
with open(TRUTH_CSV, encoding="utf-8-sig") as f:
    _rd = csv.DictReader(f)
    _fields = list(_rd.fieldnames)
    sample = list(_rd)

lines_cache = {}
def lines_of(ep):
    if ep not in lines_cache:
        p = LINES / f"lines_ep{ep:02d}.json"
        lines_cache[ep] = json.loads(p.read_text(encoding="utf-8")).get("lines", []) if p.exists() else []
    return lines_cache[ep]


def context(ep, t, k=4):
    ls = lines_of(ep)
    for i, l in enumerate(ls):
        if abs(float(l["t"]) - float(t)) < 0.01:
            lo, hi = max(0, i - k), min(len(ls), i + k + 1)
            return [(float(ls[j]["t"]), ls[j]["text"]) for j in range(lo, hi)]
    return []


def window(ep, t):
    ls = lines_of(ep)
    t = float(t)
    for i, l in enumerate(ls):
        if abs(float(l["t"]) - t) < 0.01 and i + 1 < len(ls):
            gap = min(max(float(ls[i + 1]["t"]) - t, 0.8), 2.4)
            return t + 0.1, t + gap - 0.1
    return t + 0.1, t + 1.5


def prepare(r):
    i, ep, t = r["id"], int(r["ep"]), float(r["t"])
    s, e = window(ep, t)
    mid = (s + e) / 2
    wav = AUD / f"{int(i):03d}_ep{ep:02d}_{t:.1f}s.wav"
    jpg = FRM / f"{int(i):03d}_ep{ep:02d}_{t:.1f}s.jpg"
    cmd_a = [FFMPEG, "-y", "-v", "error", "-ss", f"{s:.3f}", "-i", str(SRC / f"{ep}.mp4"),
             "-t", f"{e-s:.3f}", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)]
    cmd_v = [FFMPEG, "-y", "-v", "error", "-ss", f"{mid:.3f}", "-i", str(SRC / f"{ep}.mp4"),
             "-frames:v", "1", "-q:v", "3", str(jpg)]
    pa = subprocess.run(cmd_a, capture_output=True)
    pv = subprocess.run(cmd_v, capture_output=True)
    return i, pa.returncode == 0 and wav.exists(), pv.returncode == 0 and jpg.exists()


# 上下文写入样本表（sample 已在开头读入内存，这里整表重写）
fields = _fields + ["context"]
for r in sample:
    ctx = context(int(r["ep"]), r["t"])
    r["context"] = " || ".join(f"{tt:.0f}s:{tx}" for tt, tx in ctx)
with open(TRUTH_CSV, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(sample)

ok_a = ok_v = 0
with ThreadPoolExecutor(max_workers=8) as ex:
    futs = {ex.submit(prepare, r): r for r in sample}
    for f in as_completed(futs):
        i, a, v = f.result()
        ok_a += a
        ok_v += v
        if not (a and v):
            print("缺证据 id", i, "audio", a, "frame", v)
print(f"证据就绪：音频 {ok_a}/{len(sample)}，画面帧 {ok_v}/{len(sample)}，上下文 {len(sample)}/{len(sample)}")
