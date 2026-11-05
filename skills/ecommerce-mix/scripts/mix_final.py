#!/usr/bin/env python3
"""混剪合成：AI 钩子 + 素材段 + 旁白 → 一条成片。

核心设计：**段切换点 = 旁白句边界**——每句说完立刻切画面。
段长来自旁白句边界（silencedetect 找停顿 + 文本对应），不是拍脑袋。
步骤：
  1. 旁白 silencedetect 拿停顿 → 按文本对应出句边界
  2. 每段 = 对应句时长（段内多镜头按比例分），末段 = 句长 + 片尾停留 2s
  3. 逐段 4K 横版 → crop 608x1080 → scale 720x1280@24fps（与 AI 钩子同参）
  4. concat 拼接 → 旁白 adelay 到钩子尾 + gain 对齐响度 → 整片淡出

用法：
  python3 mix_final.py --hook AI1.mp4 --segments segs.json --vo vo.mp3 -o final.mp4
segs.json 格式：[{"vid":"C5306","src":"/path/C5306.MP4","dur":3.2,"pre":0.0}]
  dur = 取用秒数；pre = 从镜头起点偏移（默认 0）。
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# 按高度算竖切宽度，兼容 1080p 与 4K：1080p→607，4K→1215。写死 608 会让 4K 素材裁错区域。
VF = "crop=ih*9/16:ih,scale=720:1280,fps=24,setsar=1"
# 钩子是 480p（实测 496x864），与素材段尺寸不同，concat 前必须统一到 720x1280。
# 用 increase+crop 而不是直接 scale，避免 496/864 与 9:16 的 2% 差被拉变形。
HOOK_VF = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24,setsar=1"
HOOK_FPS = 24  # AI 钩子的帧率，全部段与之对齐


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=30)
    return float(out.stdout.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hook", required=True, help="AI 钩子成片（自带对白音轨）")
    ap.add_argument("--segments", required=True, help="段清单 JSON")
    ap.add_argument("--vo", required=True, help="旁白 mp3")
    ap.add_argument("--vo-gain", type=float, default=1.41,
                    help="旁白增益（默认 +3dB，对齐钩子 -14 LUFS）")
    ap.add_argument("--tail-hold", type=float, default=0.0,
                    help="额外片尾停留（默认 0：停留已写进末段 dur，两处相加会重复）")
    ap.add_argument("--tail", default="",
                    help="AI 副视频（hook_plus_convert 转化段），接在素材段之后；"
                         "素材段清单只留到副视频起点，秒数在 segs 里截好")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    segs = json.loads(Path(args.segments).read_text(encoding="utf-8"))
    hook_len = probe_duration(args.hook)
    vo_len = probe_duration(args.vo)
    tail_len = probe_duration(args.tail) if args.tail else 0.0
    mix_len = sum(s["dur"] for s in segs) + args.tail_hold
    total = hook_len + mix_len + tail_len
    print(f"钩子 {hook_len:.2f}s + 混剪 {mix_len:.2f}s"
          + (f" + AI尾段 {tail_len:.2f}s" if tail_len else "")
          + f" = {total:.2f}s；旁白 {vo_len:.2f}s")
    if vo_len > mix_len + tail_len:
        sys.exit(f"⚠️ 旁白 {vo_len:.2f}s 超过混剪+尾段 {mix_len + tail_len:.2f}s，"
                 f"最后一句会被截——加长末段或尾段")

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # 1. 逐段转码（统一参数才能 concat）
        seg_files = []
        for i, s in enumerate(segs):
            f = td / f"seg_{i:02d}.mp4"
            cmd = ["ffmpeg", "-y", "-loglevel", "error",
                   "-ss", str(s.get("pre", 0.0)), "-t", str(s["dur"]), "-i", s["src"],
                   "-vf", VF, "-an", "-c:v", "libx264", "-crf", str(args.crf),
                   "-preset", "medium", str(f)]
            subprocess.run(cmd, check=True)
            seg_files.append(f)
            print(f"  段{i:02d} {s['vid']} {s['dur']}s ok")
        # 2. concat（钩子 + 素材混剪 [+ AI 尾段]）
        lst = td / "list.txt"
        lst.write_text("".join(f"file '{f}'\n" for f in seg_files))
        mix = td / "mix.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat",
                        "-safe", "0", "-i", str(lst), "-c", "copy", str(mix)], check=True)
        # 3. 合成：钩子 + 混剪 [+ 尾段]；旁白 delay 到钩子尾；片尾淡出
        fade_v = total - 0.7
        fade_a = hook_len + vo_len - 0.5
        tail_v = ""
        n_parts = 2
        if tail_len:
            tail_v = f"[3:v]{HOOK_VF},setpts=PTS-STARTPTS[v2];"
            n_parts = 3
        fc = (
            f"[0:v]{HOOK_VF},setpts=PTS-STARTPTS[v0];"
            f"[1:v]{HOOK_VF}"
            + (f",tpad=stop_mode=clone:stop_duration={args.tail_hold}" if args.tail_hold else "")
            + ",setpts=PTS-STARTPTS[v1];"
            + tail_v
            + (f"[v0][v1][v2]concat=n=3:v=1:a=0" if tail_len else f"[v0][v1]concat=n=2:v=1:a=0")
            + f",fade=t=out:st={fade_v:.2f}:d=0.6[vout];"
            f"[0:a]aresample=48000[a0];"
            f"[2:a]volume={args.vo_gain},aresample=48000,"
            f"adelay={int(hook_len*1000)}|{int(hook_len*1000)}[vo];"
            f"[a0]apad=pad_dur=45[ap];"
            f"[ap][vo]amix=inputs=2:duration=first:normalize=0,"
            f"afade=t=out:st={fade_a:.2f}:d=1.0[aout]"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", args.hook, "-i", str(mix),
             "-i", args.vo] + (["-i", args.tail] if tail_len else []) +
            ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]",
             "-c:v", "libx264", "-crf", str(args.crf), "-preset", "medium",
             "-c:a", "aac", "-b:a", "192k", "-shortest", args.out], check=True)
    final_len = probe_duration(args.out)
    print(f"成片 {args.out}  {final_len:.2f}s")
    print("核对清单：①15s 处钩子→混剪切点 ②旁白句边界=画面切换 ③末段停留+淡出 ④整片响度")


if __name__ == "__main__":
    main()
