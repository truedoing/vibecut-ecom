"""抽「边界帧」——短剧素材索引的第 3 道工序（L3）。

为什么是「头尾各一帧」而不是整段：
  exit_type 问的是「这段最后给观众留下什么画面、能不能从这儿切出去」，
  那是**边界**属性，不是整段属性。中间几帧对「切得出去吗」没有信息量。

为什么现在做（而不是等写执行单）：
  出口类型是整部剧的**常量** —— 第 1 条片子和第 20 条片子判的是同一个「玉米地结尾」。
  一次算完，后面每条片子直接引用；精确截断才是每条片子的变量，留到 L4。

只读原片，零修改。增量：已抽过的段自动跳过（--force 重跑）。

用法：
  python edge_frames.py --all
  python edge_frames.py --ep 1 6 --force

输出：
  <out>/frames/epNN/p01_in.png  段首（start + 0.5s）
  <out>/frames/epNN/p01_out.png 段尾（end - 0.5s）
  <out>/grid/epNN_g1.jpg        每张 4 段成对排（8 格 + 1 空），供一次看完打标
  <out>/manifest.json           段 → 帧 → 拼图 的映射，打标脚本按它回填
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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
BASE = ROOT / "assets" / "short-drama" / DRAMA
SKELETON = BASE / "segments_skeleton.json"
OUT = BASE / "edge_frames"
FRAMES = OUT / "frames"
GRID = OUT / "grid"

PAD_IN = 0.5   # 段首内缩，避开上一段的尾巴
PAD_OUT = 0.5  # 段尾内缩，避开"切走以后"的黑场


def tool(name: str) -> str:
    """可移植地找 ffmpeg/ffprobe：PATH → 当前解释器同目录 → 常见包管理目录。
    不写死本机路径（技能红线），也不假定 PATH 里有它。"""
    p = shutil.which(name)
    if p:
        return p
    for d in (Path(sys.executable).parent, "/opt/homebrew/bin", "/usr/local/bin"):
        c = d / name
        if c.exists():
            return str(c)
    return name


FFMPEG = tool("ffmpeg")

CELL_W, CELL_H = 200, 356
LABEL_H = 22
PER_GRID = 3   # 每张拼图放 3 段（每段一行：IN 左 / OUT 右）


def find_videos() -> dict[int, Path]:
    out = {}
    for p in sorted(SRC.glob("*.mp4")):
        m = re.search(r"第(\d+)集", p.name)
        if m:
            out[int(m.group(1))] = p
    return out


def shot(video: Path, t: float, dst: Path) -> bool:
    if dst.exists():
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video),
         "-frames:v", "1", str(dst)],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and dst.exists()


def font(size: int):
    for p in ("/System/Library/Fonts/Helvetica.ttc",
              "/System/Library/Fonts/Supplemental/Arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


F = font(15)


def build_grid(items: list[dict], dst: Path) -> None:
    """items: 每项 {label, in_img, out_img}，按 2 列 × 4 行排（IN 左 OUT 右）。"""
    rows = len(items)
    W = CELL_W * 2
    H = (CELL_H + LABEL_H) * rows
    canvas = Image.new("RGB", (W, H), "#101010")
    d = ImageDraw.Draw(canvas)
    for i, it in enumerate(items):
        y = i * (CELL_H + LABEL_H)
        for j, key in enumerate(("in_img", "out_img")):
            x = j * CELL_W
            try:
                im = Image.open(it[key]).convert("RGB").resize((CELL_W, CELL_H))
                canvas.paste(im, (x, y + LABEL_H))
            except Exception:
                d.rectangle([x, y + LABEL_H, x + CELL_W, y + LABEL_H + CELL_H], fill="#303030")
        tag = "IN " + it["label"] if True else ""
        d.text((4, y + 3), f"{it['seg']}  IN / OUT", fill="#D8D8D8", font=F)
    dst.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dst, quality=88)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--ep", nargs="*", type=int)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    sk = json.loads(SKELETON.read_text(encoding="utf-8"))
    eps = sk["episodes"]
    want = (sorted(eps.keys(), key=int) if a.all
            else [k for k in eps.keys() if int(k) in set(a.ep or [])])
    vids = {int(k): v for k, v in find_videos().items()}

    manifest_path = OUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    if a.force:
        for e in want:
            manifest.pop(e, None)

    total = 0
    for ep in want:
        if int(ep) not in vids:
            print(f"EP{ep:0>2} 缺视频，跳过")
            continue
        segs = eps[ep]["segments"]
        if ep in manifest and not a.force:
            print(f"EP{ep:0>2} 已有，跳过（{len(manifest[ep])} 段）")
            continue
        recs = []
        for s in segs:
            dur = eps[ep]["duration"]
            tin = min(s["start"] + PAD_IN, max(s["start"], s["end"] - 0.4), dur - 0.3)
            tout = max(s["end"] - PAD_OUT, s["start"] + 0.4)
            tout = max(tin + 0.2, min(tout, dur - 0.2))
            fi = FRAMES / f"ep{int(ep):02d}" / f"{s['id']}_in.png"
            fo = FRAMES / f"ep{int(ep):02d}" / f"{s['id']}_out.png"
            ok1 = shot(vids[int(ep)], tin, fi)
            ok2 = False
            for back in (0.0, 0.6, 1.4, 2.4):   # 末段常卡在真实末尾之外，回退重试
                if shot(vids[int(ep)], max(0.0, tout - back), fo):
                    ok2 = True
                    break
            if not (ok1 and ok2):
                print(f"  EP{ep} {s['id']} 抽帧失败")
                continue
            recs.append({"seg": s["id"], "start": s["start"], "end": s["end"],
                         "in_t": round(tin, 2), "out_t": round(tout, 2),
                         "in_img": str(fi), "out_img": str(fo)})
        # 拼图：每张 4 段
        for gi, i in enumerate(range(0, len(recs), PER_GRID), 1):
            chunk = [{"seg": r["seg"], "in_img": r["in_img"], "out_img": r["out_img"],
                      "label": f"{r['in_t']:.1f}s / {r['out_t']:.1f}s"} for r in recs[i:i + PER_GRID]]
            g = GRID / f"ep{int(ep):02d}_g{gi}.jpg"
            build_grid(chunk, g)
            for c in chunk:
                for r in recs:
                    if r["seg"] == c["seg"]:
                        r["grid"] = str(g)
        manifest[ep] = recs
        total += len(recs)
        print(f"EP{ep:0>2} {len(recs)} 段 × 2 帧")

    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    segs_total = sum(len(v) for v in manifest.values())
    grids = len(list(GRID.glob("*.jpg"))) if GRID.exists() else 0
    print(f"\n累计 {segs_total} 段 / {segs_total * 2} 帧 / {grids} 张拼图")


if __name__ == "__main__":
    main()
