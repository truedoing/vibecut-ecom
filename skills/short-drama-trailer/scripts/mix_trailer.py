#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯剪预告装配器 —— short-drama-trailer 技能脚本。

plan.json 驱动的 ffmpeg concat 装配。铁律内置：
  1) 出点上限 = 视频流时长（不是 format 时长）——装配前自动校验，超限报错不装配；
  2) 分段 loudnorm 后全局 alimiter；setsar=1 放 scale 之后；48k/fltp 统一；
  3) 黑场/字幕卡先落地成图片文件再进 concat（lavfi 源不能直接进 concat）。

段类型（segs 数组，每段一个 dict）：
  ep    原剧集片段   {"label","type":"ep","ep":1,"a":117.8,"b":130.8,"beats":"挑起..交锋..落点"}
                     集文件按「第N集_」匹配 src_dir 下文件；也可用 "path" 直接指定。
  video 整段外部视频 {"label","type":"video","path":"wangshen_v1.mp4"}（a/b 可选，不给则整段）
  black 黑场         {"label","type":"black","d":0.5}
  card  字幕卡       {"label","type":"card","d":4.0,"path":"build/card.png"}
                     path 可省，用 plan 级 "card"。

plan 级字段：src_dir（剧集目录）、card（默认字幕卡图）、w/h/fps（默认 720/1280/24）。
相对路径一律相对 plan.json 所在目录解析。

用法：
  python3 mix_trailer.py --plan plan.json --out 成片.mp4 [--ff /path/ffmpeg] [--dry]

--dry 只跑校验和段落表打印，不执行 ffmpeg。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile


def find_ff(override):
    if override:
        return override
    p = shutil.which("ffmpeg")
    if p:
        return p
    for cand in ("/opt/anaconda3/bin/ffmpeg",):
        if os.path.exists(cand):
            return cand
    sys.exit("找不到 ffmpeg：请用 --ff 指定绝对路径")


def find_ffprobe(ff):
    cand = os.path.join(os.path.dirname(ff), "ffprobe")
    if os.path.exists(cand):
        return cand
    p = shutil.which("ffprobe")
    if p:
        return p
    sys.exit("找不到 ffprobe（与 ffmpeg 同目录应有）")


def stream_dur(fp, path, sel):
    r = subprocess.run(
        [fp, "-v", "error", "-select_streams", sel,
         "-show_entries", "stream=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return float(r.stdout.strip().splitlines()[0])


def ep_file(src_dir, n):
    for f in sorted(os.listdir(src_dir)):
        if f"第{n}集_" in f:
            return os.path.join(src_dir, f)
    raise FileNotFoundError(f"src_dir 下找不到 第{n}集_ 文件：{src_dir}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ff", default=None)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    plan_path = os.path.abspath(args.plan)
    base = os.path.dirname(plan_path)
    plan = json.load(open(plan_path, encoding="utf-8"))
    out = args.out if os.path.isabs(args.out) else os.path.join(base, args.out)

    def resolve(p):
        return p if os.path.isabs(p) else os.path.join(base, p)

    W = plan.get("w", 720)
    H = plan.get("h", 1280)
    FPS = plan.get("fps", 24)
    src_dir = plan.get("src_dir")
    if src_dir:
        src_dir = resolve(src_dir)
    card_default = plan.get("card")

    ff = find_ff(args.ff)
    fp = find_ffprobe(ff)

    # ---------- 预检：出点上限 = 视频流时长（铁律） ----------
    ep_cache = {}
    print("预检（出点上限 = 视频流时长）：")
    problems = []
    for s in plan["segs"]:
        t = s["type"]
        if t == "ep":
            path = resolve(s["path"]) if s.get("path") else ep_file(src_dir, s["ep"])
            if path not in ep_cache:
                vd = stream_dur(fp, path, "v")
                ad = stream_dur(fp, path, "a")
                ep_cache[path] = (vd, ad)
            vd, ad = ep_cache[path]
            flag = ""
            if vd is not None and s["b"] > vd + 0.001:
                problems.append(
                    f"{s['label']}: 出点 {s['b']} > 视频流 {vd:.2f}（音频流 {ad:.2f}）——段尾会带黑帧")
                flag = "  <-- 超限"
            elif vd is not None and vd - s["b"] < 0.1:
                flag = "  （贴视频流尾，注意帧级复核）"
            print(f"  {s['label']:<14} ep{s['ep']}  video={vd}  audio={ad}{flag}")
        elif t == "video":
            path = resolve(s["path"])
            vd = stream_dur(fp, path, "v")
            print(f"  {s['label']:<14} video  video={vd}")
        elif t in ("black", "card"):
            print(f"  {s['label']:<14} {t}  d={s['d']}")
    if problems:
        print("\n出点超限，拒绝装配：")
        for p in problems:
            print("  " + p)
        sys.exit(1)
    if args.dry:
        print("--dry：校验通过，未装配。")
        return

    # ---------- 黑场落地成文件（lavfi 不能直接进 concat） ----------
    black_png = os.path.join(base, "build_black_720x1280.png")
    if not os.path.exists(black_png):
        os.makedirs(os.path.dirname(black_png), exist_ok=True)
        subprocess.run(
            [ff, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=black:s={W}x{H}",
             "-frames:v", "1", black_png], check=True)

    # ---------- 组滤镜图 ----------
    inputs, parts = [], []
    special = {}  # input idx -> 展示时长（图片类）

    def add_input(p):
        inputs.append(p)
        return len(inputs) - 1

    durations = []
    for i, s in enumerate(plan["segs"]):
        t = s["type"]
        if t == "ep":
            path = resolve(s["path"]) if s.get("path") else ep_file(src_dir, s["ep"])
            a, b = s["a"], s["b"]
            durations.append(round(b - a, 3))
            idx = add_input(path)
            parts.append(
                f"[{idx}:v]trim=start={a}:end={b},setpts=PTS-STARTPTS,"
                f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"setsar=1,fps={FPS},format=yuv420p[v{i}]")
            parts.append(
                f"[{idx}:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS,"
                f"aresample=48000,aformat=channel_layouts=stereo,"
                f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        elif t == "video":
            path = resolve(s["path"])
            if "a" in s and "b" in s:
                a, b = s["a"], s["b"]
                durations.append(round(b - a, 3))
                vtrim = f"trim=start={a}:end={b},setpts=PTS-STARTPTS,"
                atrim = f"atrim=start={a}:end={b},asetpts=PTS-STARTPTS,"
            else:
                d = stream_dur(fp, path, "v")
                durations.append(round(d, 3) if d else 0.0)
                vtrim = atrim = ""
            idx = add_input(path)
            parts.append(
                f"[{idx}:v]{vtrim}scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},setsar=1,fps={FPS},format=yuv420p[v{i}]")
            parts.append(
                f"[{idx}:a]{atrim}aresample=48000,aformat=channel_layouts=stereo,"
                f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        elif t in ("black", "card"):
            d = s["d"]
            durations.append(round(d, 3))
            img = black_png if t == "black" else resolve(s.get("path") or card_default)
            if not os.path.exists(img):
                sys.exit(f"图片不存在：{img}")
            idx = add_input(img)
            special[idx] = d
            parts.append(
                f"[{idx}:v]scale={W}:{H},setsar=1,fps={FPS},format=yuv420p[v{i}]")
            parts.append(
                f"anullsrc=r=48000:cl=stereo:d={d},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        else:
            sys.exit(f"未知段类型 {t}")

    n = len(plan["segs"])
    vlist = "".join(f"[v{i}]" for i in range(n))
    alist = "".join(f"[a{i}]" for i in range(n))
    parts.append(f"{vlist}concat=n={n}:v=1:a=0[vcat]")
    parts.append(f"{alist}concat=n={n}:v=0:a=1[acat]")
    parts.append("[acat]alimiter=limit=0.891:attack=5:release=80[aout]")

    total = sum(durations)
    cmd = [ff, "-y", "-v", "warning", "-stats"]
    for j, p in enumerate(inputs):
        if j in special:
            cmd += ["-loop", "1", "-t", f"{special[j]:.3f}", "-i", p]
        else:
            cmd += ["-i", p]
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vcat]", "-map", "[aout]",
            "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", out]

    print("\n段落表：")
    t = 0.0
    for s, d in zip(plan["segs"], durations):
        beats = s.get("beats", "")
        print(f"  {s['label']:<14} {t:>8.2f} +{d:>6.2f}  {beats}")
        t += d
    print(f"  合计 {t:.2f}s")

    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit("ffmpeg 失败")
    print(f"完成 {out}  {total:.2f}s")


if __name__ == "__main__":
    main()
