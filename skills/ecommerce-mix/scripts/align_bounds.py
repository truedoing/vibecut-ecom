#!/usr/bin/env python3
"""旁白句边界对齐：字数比例预排 → 吸附到最近静音中点。

为什么不用纯 DP：候选静音点里混着字与字之间的小停顿（0.17–0.25s），
DP 的平方代价除以句长后长句几乎不受罚，结果会把长句压得过快
（实测 23 字的句子被压到 0.149 s/字，比全局 0.205 快 27%）。
比例预排 + 吸附把误差锁在 ±0.3s 内，观众无感，且不会出现某句飞掉。

用法：
  # 直接给句子（| 分隔），输出边界与各句时长
  python3 align_bounds.py --vo vo.mp3 --sentences "第一句|第二句|第三句"

  # 从文案 JSON 读（seq >= 2 的解说段，钩子段不计）
  python3 align_bounds.py --vo vo.mp3 --script-json hjg_T2_004.json --json-out bounds.json

  # 顺手生成混剪段清单骨架（一句可多镜用 + 连接；同镜头二次使用用 @秒数 指定起点）
  python3 align_bounds.py --vo vo.mp3 --script-json x.json --segs-out segs.json \
    --shots "C5319+DJI_0397,C5388,C0420+C5315,...,C5315@3.7+C5319@5,C5394"
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

SIL_THRESHOLD = "-33dB"   # 灵敏阈，用来抓句内小停顿
SIL_MIN = 0.16
HEAD_PAD = 0.20           # 首字前的静音容差
MAX_DEV = 0.18            # 吸附半径：离理想点超过句长 18% 就不吸附（宁可不落静音点）

_CN = "零一二三四五六七八九"
_UNIT = ["", "十", "百", "千"]


def _int_to_cn(n):
    s = str(n)
    if len(s) > 4:                      # 年份/长数字：逐位读
        return "".join(_CN[int(c)] for c in s)
    out, zero = "", False
    for i, c in enumerate(s):
        d, pos = int(c), len(s) - i - 1
        if d == 0:
            zero = True
        else:
            if zero and out:
                out += "零"
            zero = False
            out += _CN[d] + _UNIT[pos]
    return out[1:] if out.startswith("一十") else out


def speech_text(t):
    """数字转中文读法。TTS 对阿拉伯数字的读法不可控（'150' 可能念'一五零'），
    且字数统计必须按**读出来的音节**算，否则句边界会偏。"""
    def rep(m):
        whole, _, frac = m.group(0).partition(".")
        s = _int_to_cn(int(whole))
        if frac:
            s += "点" + "".join(_CN[int(c)] for c in frac)
        return s
    t = re.sub(r"(\d+)%", lambda m: "百分之" + _int_to_cn(int(m.group(1))), t)
    return re.sub(r"\d+(?:\.\d+)?", rep, t)


def char_count(text):
    """音节数：先去标点，再把数字按读法展开。"""
    return len(re.sub(r"[，。；：！？—…、,.\s\"'“”‘’]", "", speech_text(text)))


def ff_dur(path):
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60).stdout.strip())


def silence_points(path):
    """返回静音中点列表（含时长），供吸附用。"""
    err = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", f"silencedetect={SIL_THRESHOLD}:d={SIL_MIN}", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300).stderr
    out = []
    for m in re.finditer(r"silence_start: ([\d.]+).*?silence_end: ([\d.]+)", err, re.S):
        a, b = float(m.group(1)), float(m.group(2))
        out.append({"mid": round((a + b) / 2, 3), "start": a, "end": b, "len": round(b - a, 3)})
    return out


def spoken_span(path, dur, points):
    """有效发声区间：跳过首尾空白。"""
    start = HEAD_PAD
    for p in points:
        if p["start"] <= 0.35:
            start = p["end"]
            break
    end = dur
    for p in reversed(points):
        if p["end"] >= dur - 0.35:
            end = p["start"]
            break
    return start, end


def align(vo, sentences, head_pad=None, verbose=True):
    dur = ff_dur(vo)
    pts = silence_points(vo)
    start, end = spoken_span(vo, dur, pts)
    if head_pad is not None:
        start = head_pad
    counts = [char_count(s) for s in sentences]
    total = sum(counts)
    rate = (end - start) / total                      # 全局 s/字

    # 候选：只保留落在发声区内的静音中点
    cands = [p["mid"] for p in pts if start + 0.3 < p["mid"] < end - 0.2]

    bounds = [round(start, 3)]
    acc = start
    for c in counts[:-1]:
        ideal = acc + c * rate
        span = c * rate
        # 候选必须落在「本句合理长度」的窗口里：太近会切在句头，太远会吞掉下一句
        room = [x for x in cands
                if acc + max(0.45, span * 0.6) < x < ideal + max(0.45, span * MAX_DEV)]
        near = min(room, key=lambda x: abs(x - ideal)) if room else ideal
        # 吸附半径兜底：离理想点太远，宁可不落静音点，也不要让某句飞掉
        if abs(near - ideal) > max(0.5, span * MAX_DEV):
            near = ideal
        bounds.append(round(near, 3))
        acc = near
    bounds.append(round(end, 3))

    rows = []
    for i, (s, c) in enumerate(zip(sentences, counts)):
        d = bounds[i + 1] - bounds[i]
        rows.append({"idx": i + 1, "chars": c, "start": bounds[i], "end": bounds[i + 1],
                     "dur": round(d, 3), "rate_s_per_char": round(d / c, 3),
                     "text": s})
    if verbose:
        print(f"有效区间 {start:.2f}–{end:.2f}s（{end - start:.2f}s）｜{total} 字｜{total / (end - start):.2f} 字/秒")
        print("边界 " + " / ".join(f"{b:.2f}" for b in bounds))
        for r in rows:
            flag = ""
            if r["rate_s_per_char"] > rate * 1.35:
                flag = " ← 偏慢"
            elif r["rate_s_per_char"] < rate * 0.72:
                flag = " ← 偏快"
            print(f"  句{r['idx']:<2} {r['chars']:>3}字 {r['dur']:5.2f}s  "
                  f"{r['rate_s_per_char']:.3f} s/字{flag}  {r['text'][:16]}")
    return {"vo_sec": round(dur, 3), "bounds": bounds, "global_rate": round(rate, 4), "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vo", required=True, help="旁白 mp3")
    ap.add_argument("--sentences", default="", help="句子，用 | 分隔")
    ap.add_argument("--script-json", default="", help="文案 JSON（取 seq>=2 的 content）")
    ap.add_argument("--json-out", default="", help="边界结果写到 JSON")
    ap.add_argument("--segs-out", default="", help="生成混剪段清单骨架")
    ap.add_argument("--shots", default="", help="按句给镜头 id（逗号分隔，可用 + 表示一句多镜）")
    ap.add_argument("--head-pad", type=float, default=None, help="强制指定起点（默认自动探）")
    args = ap.parse_args()

    if args.script_json:
        d = json.loads(Path(args.script_json).read_text(encoding="utf-8"))
        sentences = [s["content"] for s in d["segments"] if s.get("seq", 0) >= 2]
    elif args.sentences:
        sentences = [x.strip() for x in args.sentences.split("|") if x.strip()]
    else:
        ap.error("要给 --sentences 或 --script-json")

    res = align(args.vo, sentences, args.head_pad)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ {args.json_out}")

    if args.segs_out:
        if not args.shots:
            ap.error("--segs-out 需要同时给 --shots")
        shots = [x.strip().split("+") for x in args.shots.split(",") if x.strip()]
        if len(shots) != len(res["rows"]):
            raise SystemExit(f"镜头组数 {len(shots)} ≠ 句数 {len(res['rows'])}")
        segs = []
        for row, group in zip(res["rows"], shots):
            each = round(row["dur"] / len(group), 3)
            for i, sid in enumerate(group):
                d = each if i < len(group) - 1 else round(row["dur"] - each * (len(group) - 1), 3)
                pre = 0.0
                if "@" in sid:                      # C5319@5.0 = 从第 5 秒起取（同镜头二次使用）
                    sid, _, p = sid.partition("@")
                    pre = float(p)
                segs.append({"vid": sid, "src": "", "dur": d, "pre": pre})
        # 末段补余量：旁白总长可能比有效区间多 0.3–0.6s，少了 mix 会拦
        short = round(res["vo_sec"] - sum(s["dur"] for s in segs), 3)
        if short > 0:
            segs[-1]["dur"] = round(segs[-1]["dur"] + short + 0.3, 3)
            print(f"  末段补 {short + 0.3:.2f}s（旁白头尾静音 + 安全余量）")
        Path(args.segs_out).write_text(json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"→ {args.segs_out}（{len(segs)} 段，总 {sum(s['dur'] for s in segs):.2f}s；src 需自行填）")


if __name__ == "__main__":
    main()
