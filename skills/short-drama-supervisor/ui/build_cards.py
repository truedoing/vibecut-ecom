#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给分镜台预先抽好每段的首帧、末帧，网页只负责显示（服务保持纯只读）。

为什么并排看首尾帧：一段和下一段接不接得上，看文字和秒数看不出来，
看「上段最后一帧 / 本段第一帧」两幅图一眼就知道。项目里「收尾切点必看末帧」
本来就是剪辑铁律，这个页面只是把它摆到眼前。

用法：
    python3 build_cards.py --drama 以真心守护至亲 --task T9023
    python3 build_cards.py --all          # 给所有带清单的片子抽
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent
import sys as _sys  # noqa: E402

_sys.path.insert(0, str(UI_DIR.parent / "scripts"))
try:  # 数据目录只认 scripts/_env.py 一个出口（11-16 收敛，别再手数 parents）
    from _env import data_root as _data_root

    ROOT = _data_root()
except Exception:  # 极端情况兜底：至少别崩，也别悄悄指到当前目录
    ROOT = UI_DIR.parents[3]

RUNS = ROOT / "runs"


def ffmpeg_exe() -> str:
    """本机 PATH 里没有 ffmpeg，用 imageio-ffmpeg 自带的那个。"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def env_root() -> Path:
    env = dict(os.environ)
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return Path(env.get("SHORT_DRAMA_ASSET_ROOT", str(Path.home() / "短剧")))


def one_frame(ff: str, video: Path, sec: float, dst: Path) -> bool:
    sec = max(0.0, float(sec))
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [ff, "-loglevel", "error", "-ss", f"{sec:.3f}", "-i", str(video),
         "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", "-y", str(dst)],
        capture_output=True,
    )
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0


def build(drama: str, task: str) -> tuple[int, int]:
    plan = json.loads((RUNS / drama / task / "plan.json").read_text(encoding="utf-8"))
    src = env_root() / (plan.get("src_dir") or drama)
    cards = RUNS / drama / task / "cards"
    ff = ffmpeg_exe()
    ok, miss = 0, 0
    for s in plan.get("segs") or []:
        ep = s.get("ep")
        if ep is None or s.get("type") != "ep":
            continue
        v = src / f"{int(ep)}.mp4"
        label = str(s.get("label") or f"ep{int(ep):02d}")
        a, b = float(s.get("a", 0)), float(s.get("b", 0))
        if not v.exists():
            print(f"  缺视频 {v.name}，跳过 {label}")
            miss += 1
            continue
        # 末帧往回收一点点，避开切点后的下一镜；出点上限按视频流时长留余量
        for suffix, sec in (("a", a + 0.08), ("b", max(a, b - 0.25))):
            dst = cards / f"{label}_{suffix}.jpg"
            if one_frame(ff, v, sec, dst):
                ok += 1
            else:
                miss += 1
                print(f"  抽帧失败 {label}_{suffix}  {v.name} @{sec:.2f}s")
    return ok, miss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drama")
    ap.add_argument("--task")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()

    targets = []
    if a.all:
        targets = [(p.parent.parent.name, p.parent.name)
                   for p in sorted(RUNS.glob("*/*/plan.json"))]
    elif a.drama and a.task:
        targets = [(a.drama, a.task)]
    else:
        ap.error("要么给 --drama 和 --task，要么给 --all")

    total_ok = total_miss = 0
    for drama, task in targets:
        print(f"{drama}/{task}", flush=True)
        ok, miss = build(drama, task)
        total_ok += ok
        total_miss += miss
        print(f"  抽好 {ok} 张，缺 {miss} 张", flush=True)
    print(f"合计 {total_ok} 张，缺 {total_miss} 张")


if __name__ == "__main__":
    main()
