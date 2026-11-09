"""从官方素材抽「台词时间轴」——短剧素材索引的第 1 道工序。

只读原片，零修改。硬字幕 OCR 取台词，省掉 ASR。
增量续跑：按集级断点，已产出的 ep 自动跳过（--force 可强制重跑）。

用法：
  python extract_lines.py --all
  python extract_lines.py --ep 6 7 8
  python extract_lines.py --all --force

素材根一律走 .env 的 <LINE>_ASSET_ROOT，不写本机绝对路径。
输出：<out>/lines_epNN.json，每行 {"t": 秒, "text": 台词}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ASSET_ROOT = None
for _line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if _line.startswith("SHORT_DRAMA_ASSET_ROOT="):
        ASSET_ROOT = Path(_line.split("=", 1)[1].strip())
        break
if ASSET_ROOT is None:
    sys.exit("缺少 .env 的 SHORT_DRAMA_ASSET_ROOT")

DRAMA = "八零憨妻的甜蜜日常"
SRC = ASSET_ROOT / DRAMA
OUT = ROOT / "assets" / "short-drama" / DRAMA / "lines"

# 竖屏硬字幕在画面 60%–82% 处（实测 720x1280，字幕基线约 y=900）
CROP_TOP, CROP_BOTTOM = 0.58, 0.84
STEP = 2  # 秒/帧，与既有台词稿同一粒度

NOISE = re.compile(r"[\|\•\.…。、,，\s0-9oOJlI`'\"]+")


def norm(s: str) -> str:
    return NOISE.sub("", s)


def find_videos() -> dict[int, Path]:
    out = {}
    for p in sorted(SRC.glob("*.mp4")):
        m = re.search(r"第(\d+)集", p.name)
        if m:
            out[int(m.group(1))] = p
    return out


def duration(video: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def ocr_one(engine, frame: Path) -> str:
    import cv2
    img = cv2.imread(str(frame))
    if img is None:
        return ""
    h, w = img.shape[:2]
    crop = img[int(h * CROP_TOP):int(h * CROP_BOTTOM), :]
    crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    res, _ = engine(crop)
    if not res:
        return ""
    parts = []
    for box, txt, conf in res:
        if conf is not None and float(conf) < 0.45:
            continue
        t = (txt or "").strip()
        if len(norm(t)) >= 2:
            parts.append(t)
    return " ".join(parts)


def extract_ep(ep: int, video: Path, engine) -> dict:
    dur = duration(video)
    with tempfile.TemporaryDirectory(prefix=f"sd_ep{ep:02d}_") as td:
        pattern = str(Path(td) / "f_%04d.jpg")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(video),
             "-vf", f"fps=1/{STEP}", "-q:v", "3", pattern],
            check=True,
        )
        frames = sorted(Path(td).glob("f_*.jpg"))
        raw = []
        for i, f in enumerate(frames):
            txt = ocr_one(engine, f)
            raw.append({"t": i * STEP, "text": txt})

    # 相邻帧同一句字幕去重：保留首次出现，时间取起点
    lines = []
    prev = ""
    for row in raw:
        cur = norm(row["text"])
        if not cur:
            prev = ""
            continue
        if prev and SequenceMatcher(None, prev, cur).ratio() >= 0.72:
            continue
        lines.append({"t": row["t"], "text": row["text"]})
        prev = cur

    return {
        "ep": ep,
        "file": video.name,
        "duration": round(dur, 2),
        "frame_count": len(frames),
        "line_count": len(lines),
        "lines": lines,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--ep", type=int, nargs="*", default=[])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--exclude", type=int, nargs="*", default=[20],
                    help="默认排除大结局第 20 集（任务要求第 3 条）")
    args = ap.parse_args()

    from rapidocr_onnxruntime import RapidOCR
    engine = RapidOCR()

    videos = find_videos()
    eps = sorted(videos) if args.all else sorted(set(args.ep))
    eps = [e for e in eps if e not in args.exclude]
    if not eps:
        sys.exit("没有指定集数")

    OUT.mkdir(parents=True, exist_ok=True)
    done, skipped = [], []
    for ep in eps:
        dst = OUT / f"lines_ep{ep:02d}.json"
        if dst.exists() and not args.force:
            skipped.append(ep)
            continue
        data = extract_ep(ep, videos[ep], engine)
        dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        done.append(ep)
        print(f"ep{ep:02d}  {data['duration']:>7.1f}s  {data['line_count']:>3d} 句  -> {dst.name}",
              flush=True)
    print(f"完成 {len(done)} 集，跳过 {len(skipped)} 集（已存在）")


if __name__ == "__main__":
    main()
