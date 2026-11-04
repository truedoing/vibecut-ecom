#!/usr/bin/env python3
"""音色锚客观验收：LRA / 停顿 / 响度 / 峰值，一条命令出表并判合格。

为什么需要它（11-04 实测）：旧林岚锚 LRA 只有 1.5、停顿 17.4%，
voice_ref_5s 更是 LRA 0.9；在这个锚上做 voiceclone，14 次抽卡的 LRA 在
1.3–10.7 之间乱跳，唯一一条高起伏的还伴随「虎坚果→福坚果」发音变形。
**靠人耳选锚、靠抽卡碰起伏，都是因为锚没有客观验收线。**

阈值：
  LRA >= 5.0       锚自身质量的代理：太平的锚通常也碎停顿、糊咬字。
                   ⚠️ 锚 LRA 与 clone LRA **弱相关**（实测 7.3→2.9、1.9→2.3），
                   不要拿这条预测配音起伏，起伏归 narration.py 的自动择优。
  停顿占比 <= 10%  旧锚 17.4% / 郑海涛 32.7%，句间碎停顿会把语气拉平
  响度 -18.7 LUFS  与 narration.py 的成片级目标一致
  峰值 <= -1.0     clone 之后还有 EQ 与混剪 +3dB 的余量

⚠️ 声学指标过了**不等于咬字没问题**。高起伏抽卡可能把辅音吞掉，
必须再跑一次 ASR（含「虎坚果 / 咸甜口 / 配料表 / 拍一发四」等易混词）
确认识别率。这一维脚本判不了，交给人 + ASR。

用法：
  python3 voice_audit.py voice.mp3 [voice_cand_b.mp3 ...]
  python3 voice_audit.py --json voice.mp3
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MIN_LRA = 5.0
MAX_SIL_PCT = 10.0
TARGET_LUFS = -18.7
MAX_PEAK = -1.0
SIL_FILTER = "silencedetect=-38dB:d=0.28"   # 验收口径，别用对齐的 -33dB/0.16


def _ff(args, timeout=300):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stderr


def dur_of(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60).stdout.strip()
    return float(out)


def measure(path):
    d = dur_of(path)
    err = _ff(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
               "-af", SIL_FILTER, "-f", "null", "-"])
    sil = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", err)]

    err = _ff(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
               "-af", "ebur128=peak=true", "-f", "null", "-"])
    tail = err.split("Summary:")[-1]

    def grab(key, default=0.0):
        m = re.search(key + r":\s+(-?[\d.]+)", tail)
        return float(m.group(1)) if m else default

    return {
        "file": str(path),
        "sec": round(d, 3),
        "sil_n": len(sil),
        "sil_sec": round(sum(sil), 3),
        "sil_pct": round(sum(sil) * 100 / d, 1) if d else 0.0,
        "lra": grab("LRA"),
        "lufs": grab("I"),
        "peak": grab("Peak"),
    }


def judge(m):
    issues = []
    if m["lra"] < MIN_LRA:
        issues.append(f"LRA {m['lra']} < {MIN_LRA}（锚自身太平，通常伴随碎停顿与糊咬字）")
    if m["sil_pct"] > MAX_SIL_PCT:
        issues.append(f"停顿 {m['sil_pct']}% > {MAX_SIL_PCT}%（句间碎停顿会把语气拉平）")
    if abs(m["lufs"] - TARGET_LUFS) > 2.0:
        issues.append(f"响度 {m['lufs']} 偏离 {TARGET_LUFS} 超 2 LU")
    if m["peak"] > MAX_PEAK:
        issues.append(f"峰值 {m['peak']} > {MAX_PEAK} dBFS")
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="待验收的 mp3")
    ap.add_argument("--json", action="store_true", help="输出 JSON（供脚本调用）")
    args = ap.parse_args()

    rows = []
    for f in args.files:
        p = Path(f)
        if not p.exists():
            print(f"✗ 找不到 {p}", file=sys.stderr)
            sys.exit(2)
        m = measure(p)
        m["issues"] = judge(m)
        rows.append(m)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        sys.exit(0 if all(not r["issues"] for r in rows) else 1)

    print(f"{'文件':<38} {'时长':>7} {'停顿':>16} {'LRA':>6} {'响度':>9} {'峰值':>8}  判定")
    print("-" * 108)
    for m in rows:
        name = Path(m["file"]).name
        sil = f"{m['sil_n']}处 {m['sil_sec']:.2f}s ({m['sil_pct']}%)"
        ok = "✓ 通过" if not m["issues"] else "✗ " + "；".join(m["issues"])
        print(f"{name:<38} {m['sec']:6.2f}s {sil:>16} {m['lra']:6.1f} "
              f"{m['lufs']:7.1f}LUFS {m['peak']:7.1f}dB  {ok}")
    print("\n⚠️ 声学达标后仍需 ASR 咬字验收（含易混词），指标判不了发音变形。")
    sys.exit(0 if all(not r["issues"] for r in rows) else 1)


if __name__ == "__main__":
    main()
