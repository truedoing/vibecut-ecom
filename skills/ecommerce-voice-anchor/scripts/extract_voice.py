#!/usr/bin/env python3
"""从 AI 钩子视频里提取角色音标本，作为该角色的声音锚（voiceclone 用）。

背景（2026-11-05 T2_005 实测，别丢）：
  - 造锚流程（voicedesign 现造的 voice.mp3）出来的锚是"标准朗读"，MiMo voiceclone
    被它压在 ≈13 半音/0.5 跳变，怎么写提示词都顶不破——瓶颈在锚不在模型。
  - AI 钩子里角色"演"出来的声音天然带表演性，拿它当锚，克隆输出直接到 14+ 半音，
    且音色与钩子里的角色天然一致（旁白和钩子是同一个人）。
  - 两条硬教训已写死在本脚本行为里：
    1. **必须按台词位置截取说话人**，禁止凭音高猜男女（男声段实测也有 267Hz 中位）。
    2. 句界来自静音检测，**分段数必须与台词句数一致**，对不上就报错并提示手工 --spans，
       绝不静默猜。

用法：
  # 自动：从脚本 JSON 的 hook dialogue 找说话人，静音分段一一对应
  python3 extract_voice.py --video AI1_v1.mp4 --script-json 脚本.json \
      --speaker 妻子 --out 女主锚.mp3

  # 手工：自己给时间跨度（秒）
  python3 extract_voice.py --video AI1_v1.mp4 --spans "3.53-6.67,8.87-10.53" --out 女主锚.mp3

输出：锚 mp3 + 同名 .report.json（每段/整体的 F0 跨度、跳变、时长、来源时间）。
本技能**只提取、不合成**；合成走 ecommerce-narration（标签法，见其 SKILL.md）。
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SR = 16000
FL = int(SR * 0.040)
HOP = int(SR * 0.010)
F0MIN, F0MAX = 70, 420
MIN_ANCHOR_SEC = 10.0   # MiMo 建议；短锚会向模型先验回归（实测 4.8s 锚 → 跨度回落到 13-14）


def sh(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def probe(path):
    """返回 (时长, F0跨度半音, 帧间跳变)。复用 narration 的口径，别改公式。"""
    import numpy as np
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
    x = np.frombuffer(p.stdout, dtype=np.float32)
    dur = len(x) / SR
    n = (len(x) - FL) // HOP
    if n < 5:
        return dur, None, None
    idx = np.arange(FL)[None, :] + HOP * np.arange(n)[:, None]
    frames = x[idx] * np.hanning(FL)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    f = frames[rms > 10 ** (-45 / 20)]
    nfft = 1 << int(np.ceil(np.log2(2 * FL)))
    spec = np.fft.rfft(f, nfft, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, nfft, axis=1)[:, :FL]
    ac = ac / (ac[:, :1] + 1e-12)
    lo, hi = int(SR / F0MAX), int(SR / F0MIN)
    best = ac[:, lo:hi].argmax(axis=1) + lo
    peak = ac[:, lo:hi].max(axis=1)
    f0 = np.where(peak > 0.30, SR / best, np.nan)
    v = f0[~np.isnan(f0)]
    if len(v) < 20:
        return dur, None, None
    st = 12 * np.log2(v / 55.0)
    span = float(np.percentile(st, 95) - np.percentile(st, 5))
    jump = np.abs(np.diff(st))
    jump = float(jump[jump < 6].mean())
    return dur, round(span, 1), round(jump, 3)


def speech_spans(video, silence_db=-30, min_sil=0.22):
    """静音检测 → 语音段列表 [(start,end)]，与台词句对位。"""
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video), "-vn",
         "-af", f"silencedetect=noise={silence_db}dB:d={min_sil}", "-f", "null", "-"],
        capture_output=True, text=True)
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", p.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", p.stderr)]
    dur = float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "csv=p=0", str(video)]).stdout.strip())
    spans, cur = [], 0.0
    for s in starts:
        if s - cur > 0.2:
            spans.append([round(cur, 2), round(s, 2)])
        cur = ends.pop(0) if ends else s
    if dur - cur > 0.2:
        spans.append([round(cur, 2), round(dur, 2)])
    # 片头/片尾的短碎段是引导静音/收尾杂音，不是台词，丢掉（中间的短段保留——可能是抢拍对白）
    while spans and spans[0][1] - spans[0][0] < 0.45:
        spans.pop(0)
    while spans and spans[-1][1] - spans[-1][0] < 0.45:
        spans.pop()
    return [tuple(s) for s in spans]


def hook_dialogue(script_json):
    d = json.loads(Path(script_json).read_text())
    seg = next(s for s in d["segments"] if s.get("section_role") == "hook"
               and s.get("shot_source") == "AI")
    return [(x["speaker"], x["line"]) for x in seg["dialogue"]]


def cut(src, a, b, dst):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(a), "-to", str(b),
                    "-i", str(src), "-c:a", "libmp3lame", "-b:a", "128k", str(dst)],
                   check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="已过人审的 AI 钩子视频")
    ap.add_argument("--script-json", default="", help="脚本 JSON（自动模式：取 hook dialogue）")
    ap.add_argument("--speaker", default="", help="要提取的角色名，如 妻子")
    ap.add_argument("--lines", default="", help="只取第几句（逗号分隔，1 起），默认取该角色全部")
    ap.add_argument("--spans", default="", help="手工模式：'3.53-6.67,8.87-10.53'（秒）")
    ap.add_argument("--out", required=True, help="输出锚 mp3 路径")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / f"_{out.stem}_part"
    if tmp.exists():
        for p in tmp.iterdir():
            p.unlink()
    else:
        tmp.mkdir(parents=True)

    if args.spans:
        spans = [tuple(map(float, s.split("-"))) for s in args.spans.split(",")]
        labels = [f"手工段{i+1} {a}-{b}s" for i, (a, b) in enumerate(spans)]
    else:
        if not (args.script_json and args.speaker):
            sys.exit("✗ 自动模式需要 --script-json 和 --speaker（或改用 --spans 手工给段）")
        dialogue = hook_dialogue(args.script_json)
        want = [i for i, (sp, _) in enumerate(dialogue) if sp == args.speaker]
        if args.lines:
            keep = {int(x) - 1 for x in args.lines.split(",")}
            want = [i for i in want if i in keep]
        if not want:
            sys.exit(f"✗ 台词里没找到说话人「{args.speaker}」：{[sp for sp,_ in dialogue]}")
        spans = speech_spans(args.video)
        if len(spans) != len(dialogue):
            sys.exit(f"✗ 静音分段 {len(spans)} 段 ≠ 台词 {len(dialogue)} 句，不能自动对位。\n"
                     f"  段：{spans}\n  台词：{[f'{i+1}.{sp}:{ln[:12]}' for i,(sp,ln) in enumerate(dialogue)]}\n"
                     f"  → 先人工对时间轴，再用 --spans 手工给段。绝不静默猜。")
        spans = [spans[i] for i in want]
        labels = [f"第{i+1}句[{dialogue[i][0]}] {dialogue[i][1][:14]}…" for i in want]

    print(f"候选 {len(spans)} 段：")
    parts, report = [], []
    for k, ((a, b), lab) in enumerate(zip(spans, labels), 1):
        p = tmp / f"part{k}.mp3"
        cut(args.video, a, b, p)
        dur, span, jump = probe(p)
        print(f"  {lab}  {dur:.2f}s  F0跨度={span}  跳变={jump}")
        parts.append(p)
        report.append({"label": lab, "start": a, "end": b, "dur": round(dur, 2),
                       "f0_span": span, "f0_jump": jump})

    total = sum(b - a for a, b in spans)
    if total < MIN_ANCHOR_SEC:
        print(f"⚠️ 锚总长 {total:.1f}s < {MIN_ANCHOR_SEC:.0f}s：克隆会向模型先验回归，"
              f"表现力可能回落（实测 4.8s 锚 → 13-14 半音）。能找到同一角色更多台词就加上。")

    lst = tmp / "order.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c:a", "libmp3lame", "-b:a", "128k", str(out)],
                   check=True)
    dur, span, jump = probe(out)
    print(f"锚整体：{dur:.2f}s  F0跨度={span}  跳变={jump}")
    print(f"⚠️ 锚是『剧情情绪』里录的（本例是抬杠），克隆输出会带这个情绪底色，\n"
          f"   合成时用标签法往回调（见 ecommerce-narration SKILL.md）。\n"
          f"⚠️ 本脚本只提取不合成，也不动 card.json；要不要换掉 voice.mp3 是显式决策\n"
          f"   （换锚=本地+云端所有依赖点一次换完，见工作约定）。")
    (out.parent / (out.stem + ".report.json")).write_text(json.dumps(
        {"anchor": str(out), "source_video": str(args.video), "total_sec": round(total, 2),
         "overall": {"dur": round(dur, 2), "f0_span": span, "f0_jump": jump},
         "parts": report}, ensure_ascii=False, indent=1))
    # 沙箱 shim 下 Path.rmdir() 会被映射成 rm（对目录报 EISDIR），用 shutil 更稳
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"OK {out} + .report.json")


if __name__ == "__main__":
    main()
