# -*- coding: utf-8 -*-
"""不放整片也能看效果：抽几帧原片画面，套上去重的滤镜，出「原图 vs 处理图」对照。

为什么要它：整片装配一次要十几分钟，而「字幕清没清干净、人物有没有被切」这件事
拿单帧看就够。先用这个快速过一遍，再去装整片。

用法：
    python3 preview.py --drama X --task Y                       # 自动抽有代表性的几段
    python3 preview.py --drama X --task Y --seg "S04_那道疤"      # 只看某一段
    python3 preview.py --drama X --task Y --out 目录
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: E402


def frame_chain(W: int, H: int, cfg: dict) -> str:
    """把逐段链压成「单帧版」：去掉 trim / fps / setpts，单帧没有时间轴。

    数值全部从参数表读，跟装配器挂的是同一套，不另抄一份——抄两份迟早对不上。
    """
    a1 = cfg.get("a1") or {}
    fw, fh = int(W * float(a1.get("fg_scale", 0.9))), int(H * float(a1.get("fg_scale", 0.9)))
    bw, bh = int(W / 4), int(H / 4)
    bg_nosub = f"crop={W}:{int(H * 0.88)}:0:0," if a1.get("bg_drop_subtitle", True) else ""

    parts = [
        "split=2[bg][fg]",
        f"[bg]{bg_nosub}scale={bw}:{bh},boxblur={a1.get('bg_blur', '10:1')},"
        f"eq=brightness={a1.get('bg_brightness', -0.08)},scale={W}:{H}[bg2]",
        f"[fg]scale={fw}:{fh},"
        f"drawbox=x=0:y=0:w={fw}:h={fh}:color={a1.get('border', 'white@0.30')}:"
        f"t={a1.get('border_t', 6)},setsar=1[fg2]",
        "[bg2][fg2]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2,setsar=1",
    ]
    tail = plain_chain(W, H, cfg)
    return ";".join(parts) + ("," + tail if tail else "")


def plain_chain(W: int, H: int, cfg: dict) -> str:
    """全局参数包，套框和不套框的段都要过，所以单独拎出来。"""
    g = cfg.get("global") or {}
    if not g:
        return ""
    c = int(g.get("crop", 8))
    out = (f"eq=brightness={g.get('brightness', 0.03)}:contrast={g.get('contrast', 1.03)}"
           + (",vignette" if g.get("vignette", True) else ""))
    if c:
        out += f",crop={W - 2 * c}:{H - 2 * c}:{c}:{c},scale={W}:{H}"
    return out


def grab(ff: str, src: Path, t: float, dest: Path, vf: str | None) -> bool:
    cmd = [ff, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1"]
    if vf:
        cmd += ["-filter_complex" if ";" in vf else "-vf", vf]
    cmd.append(str(dest))
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        print(f"    抽帧失败 {src.name} @{t:.1f}s: {p.stderr.decode()[-160:]}")
        return False
    return True


def merge(a: Path, b: Path, dest: Path, ff: str) -> bool:
    p = subprocess.run(
        [ff, "-y", "-v", "error", "-i", str(a), "-i", str(b),
         "-filter_complex", "[0:v][1:v]hstack=inputs=2",
         "-frames:v", "1", str(dest)], capture_output=True)
    return p.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description="抽帧看去重效果（原图 vs 处理图）")
    ap.add_argument("--drama", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--plan", default=None)
    ap.add_argument("--spec", default=None, help="参数表，默认取清单同目录的 dedup_spec.json")
    ap.add_argument("--seg", default=None, help="只看这一段（段名）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--src-dir", default=None, help="原片目录，默认按 .env 的 SHORT_DRAMA_ASSET_ROOT 找")
    args = ap.parse_args()

    root = _env.data_root(create=True)
    ff = _env.tool("ffmpeg")
    task_dir = root / "runs" / args.drama / args.task
    plan_path = Path(args.plan) if args.plan else None
    if plan_path is None:
        for n in ("plan.json", "plan.silence.json"):
            if (task_dir / n).exists():
                plan_path = task_dir / n
                break
    if plan_path is None:
        sys.exit(f"在 {task_dir} 找不到清单")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    spec_path = Path(args.spec) if args.spec else plan_path.parent / "dedup_spec.json"
    if not spec_path.exists():
        sys.exit(f"没有参数表：{spec_path}\n先跑 make_spec.py")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    a1 = set(spec.get("a1_segments") or [])

    W, H = int(plan.get("w", 720)), int(plan.get("h", 1280))
    src_dir = args.src_dir or plan.get("src_dir")
    env = _env.read_env(root)
    if not src_dir:
        src_dir = env.get("SHORT_DRAMA_ASSET_ROOT")
    if not src_dir:
        sys.exit("不知道原片在哪：清单里没有 src_dir，.env 也没配 SHORT_DRAMA_ASSET_ROOT")
    src_dir = Path(str(src_dir)).expanduser()
    if not src_dir.is_absolute():
        # 清单里的相对路径是相对「清单自己所在目录」写的（装配器也是这么解析的），
        # 不是相对数据目录——这里跟它保持一致，两边不能各按各的算
        src_dir = (plan_path.parent / src_dir).resolve()

    outdir = Path(args.out) if args.out else plan_path.parent / "去重预览"
    outdir.mkdir(parents=True, exist_ok=True)

    # 挑几段看：套框的挑前 3 段、不套的挑前 2 段。用段中点，避开转场。
    segs = [s for s in plan["segs"] if s.get("type") == "ep"]
    if args.seg:
        segs = [s for s in segs if s.get("label") == args.seg] or sys.exit("清单里没有这一段")
    else:
        pick = ([s for s in segs if s.get("label") in a1][:3]
                + [s for s in segs if s.get("label") not in a1][:2])
        segs = sorted(pick, key=lambda s: plan["segs"].index(s))

    if not segs:
        sys.exit("没有可看的原片段")

    ep_file_cache: dict[int, Path | None] = {}
    print(f"参数表：{spec_path}")
    print(f"原片目录：{src_dir}")
    print(f"输出：{outdir}\n")

    made = 0
    for i, s in enumerate(segs, 1):
        ep = int(s["ep"])
        if ep not in ep_file_cache:
            hits = sorted(src_dir.glob(f"*{ep:02d}*.mp4")) or sorted(src_dir.glob(f"*{ep}*.mp4"))
            ep_file_cache[ep] = hits[0] if hits else None
        src = ep_file_cache[ep]
        if src is None:
            print(f"  · ep{ep:02d} 找不到原片文件，跳过（{s['label']}）")
            continue
        a, b = float(s["a"]), float(s["b"])
        mid = a + (b - a) / 2
        is_a1 = s.get("label") in a1
        tag = "套框" if is_a1 else "满屏"
        stem = f"{i:02d}_{tag}_{s['label']}".replace("/", "_")

        raw = outdir / f"{stem}_原图.png"
        eff = outdir / f"{stem}_处理后.png"
        if not grab(ff, src, mid, raw, f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                                       f"crop={W}:{H},setsar=1"):
            continue
        vf = (frame_chain(W, H, spec) if is_a1 else plain_chain(W, H, spec)) or None
        if not grab(ff, src, mid, eff, vf):
            continue
        # 左右拼一张，人一眼看出改了啥
        side = outdir / f"{stem}_对照.png"
        merge(raw, eff, side, ff)
        print(f"  ✓ {stem}  ({side.name})" if side.exists() else f"  ✓ {stem}")
        made += 1

    print(f"\n共 {made} 组。看三件事：字幕清干净没、人物有没有被切、模糊底有没有留字影。")
    print("这一步只验单帧。真要确认动起来没怪相，还得装整片看。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
