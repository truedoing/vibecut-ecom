#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测一段原剧素材里「有话」和「没话」的时间分布

**为什么要有这个**
解说要压在画面上，原声会被压低（duck）。所以解说的落点不能只按**段边界**算——
段里的话说完之后往往还有一段静音（换气、走位、镜头空拍），那才是解说该待的地方。
反过来，段里正在说的那句要是关键台词（钩子、刺点、落点），压上去就废了。

**实测出来的样子**（11-23，《以真心守护至亲》D11 钩子段）：
段是 ep28 19.65–45.70（成片 0–26.05 秒），按段边界算，解说只能挤在最后 5.4 秒；
但按话/静分布看，**台词在成片 21.9 秒就说完了，后面 4 秒是静音**——
解说真正的落点是 21.9 秒往后，不是 20.4 秒。

**怎么测**
取音频窗口做 100ms 帧的 RMS，低于「峰值 × 阈值比例」算静音段；
静音段连成 0.2s 以上的才报出来（短的是换气，不算）。

用法：
    python3 speech_runs.py --plan runs/.../plan.json --drama 以真心守护至亲
    python3 speech_runs.py --plan runs/.../plan.json --drama 以真心守护至亲 --seg S07
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    from _env import data_root, tool  # type: ignore
except Exception:  # pragma: no cover
    sys.exit("找不到 _env.py")

from timeline import build_timeline  # noqa: E402  —— 时间轴唯一真源


def _asset_root_from_env() -> Path | None:
    """素材根：环境变量优先，其次仓库根 .env（路径去本机化）。"""
    v = os.environ.get("SHORT_DRAMA_ASSET_ROOT")
    if v and Path(v).expanduser().is_dir():
        return Path(v).expanduser()
    envf = data_root() / ".env"
    if envf.is_file():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("SHORT_DRAMA_ASSET_ROOT") and "=" in line:
                p = line.split("=", 1)[1].strip().strip('"').strip("'")
                if p and Path(p).expanduser().is_dir():
                    return Path(p).expanduser()
    return None


def find_episode(drama: str, ep: int) -> Path:
    """按 plan 的 src_dir 找第 N 集的文件。"""
    root = data_root()
    asset_root = _asset_root_from_env()
    cands = [root / "assets" / "short-drama" / drama]
    if asset_root is not None:
        cands.append(asset_root / drama)
    for base in cands:
        if not base.is_dir():
            continue
        for f in base.glob(f"{ep}.mp4"):
            return f
        for f in base.glob(f"第{ep}集*"):
            return f
        for f in base.glob(f"*{ep}集*"):
            if f.suffix.lower() == ".mp4":
                return f
    raise SystemExit(f"找不到第 {ep} 集的素材文件（剧：{drama}）")


def rms_profile(path: Path, ss: float, dur: float, win: float = 0.1) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-ss", str(ss), "-t", str(dur), "-i", str(path),
           "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    raw = subprocess.check_output(cmd)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    n = int(8000 * win)
    m = max(len(a) // n, 1)
    a = a[:m * n]
    return np.sqrt((a.reshape(m, n) ** 2).mean(axis=1))


def runs_of_silence(r: np.ndarray, win: float, thr_ratio: float, min_len: float):
    """低能量段 → [(起, 止)]（秒，相对本段开头）。"""
    if r.size == 0 or r.max() <= 0:
        return []
    thr = r.max() * thr_ratio
    low = r < thr
    out, i = [], 0
    while i < len(low):
        if low[i]:
            j = i
            while j < len(low) and low[j]:
                j += 1
            if (j - i) * win >= min_len:
                out.append((i * win, j * win))
            i = j
        else:
            i += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="测原剧段里的话/静分布")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--drama", required=True)
    ap.add_argument("--seg", default="", help="只看某一段的 label")
    ap.add_argument("--thr", type=float, default=0.18, help="静音阈值 = 峰值 × 此值（默认 0.18）")
    ap.add_argument("--min-silence", type=float, default=0.2, help="静音多长才算一段（秒）")
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    # 时间轴从 plan_timeline 取（跟装配器一致，含转场黑场）——别手算，11-23 漏过 0.45s
    tl = build_timeline(plan)
    for item in tl:
        seg = item.get("seg") or {}
        if item["kind"] != "seg" or seg.get("type") != "ep":
            continue
        t = item["start"]
        dur = item["dur"]
        label = seg.get("label", "")
        if args.seg and args.seg not in label:
            continue
        f = find_episode(args.drama, int(seg["ep"]))
        r = rms_profile(f, float(seg["a"]), dur)
        sil = runs_of_silence(r, 0.1, args.thr, args.min_silence)
        print(f"\n=== {label}（ep{seg['ep']} {seg['a']:.2f}–{seg['b']:.2f}，成片 {t:.2f}–{t+dur:.2f}）===")
        if not sil:
            print("  整段都在说话，没有 0.2s 以上的停顿")
        for a, b in sil:
            print(f"  静音 {a:6.2f}–{b:6.2f}（{b-a:.2f}s）→ 成片 {t+a:6.2f}–{t+b:6.2f}")
        # 段尾静音 = 解说的好落点
        last_speech = dur
        for a, b in sil:
            if b >= dur - 0.05:
                last_speech = a
        print(f"  → 最后一句台词结束在成片 {t+last_speech:.2f}s；这段尾巴到 {t+dur:.2f}s 都是静的")
        t += dur
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
