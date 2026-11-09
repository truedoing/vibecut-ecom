#!/usr/bin/env python3
"""成片级旁白一键产出：生成 → 测速 → 自动配速 → 提亮 → 响度对齐 → 质检。

解决的问题：以前每一步都要人肉看指标、手算 atempo、手调 volume，跑一条旁白要
五六轮。这里把「什么样的旁白算合格」写死成阈值，脚本自己调到达标。

三条固化下来的经验（都是实测换来，别改）：
  1. **停顿密度 = 情绪起伏**。同一段文本，style 写「念数字放慢」→ 停顿 18.2%、LRA 1.6；
     改「句间不换气、数字顺势念」→ 停顿 7.4%、LRA 7.2。TTS 断句一散，语气就平。
     所以要约束的是**连贯性**，不是语速。
  2. **先让 TTS 跑快，再用 atempo 降速**。atempo 不改 LRA，降速只损失一点点
     清晰度但保住已经拿到的起伏；反过来（生成时压慢）起伏直接没了。
  3. **响度和峰值一起管**：目标 -18.7 LUFS、峰值 ≤ -1.0 dBFS，取两者更严的那个增益，
     免得混剪时 +3dB 把峰值推爆。

用法：
  python3 narration.py --card ch_001 --script-text vo.txt -o out.mp3
  python3 narration.py --card ch_001 --script-json hjg.json -o out.mp3 --target-rate 4.9
  python3 narration.py --card ch_001 --text "一句话" -o t.mp3 --style-extra "价格像替朋友算账"

句边界不在本脚本：出完交给 ecommerce-mix 的 align_bounds.py。
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# TTS 客户端在兄弟技能 ecommerce-ai-assets 里（音色锚那条生产线）
_AI_ASSETS = HERE.parent.parent / "ecommerce-ai-assets" / "scripts"
if (_AI_ASSETS / "gen_voice.py").exists():
    sys.path.insert(0, str(_AI_ASSETS))
try:
    from gen_voice import call_tts, load_env      # noqa: E402
except ModuleNotFoundError:
    sys.exit("✗ 找不到 gen_voice.py（MiMo TTS 客户端）。\n"
             "  它属于 ecommerce-ai-assets 技能，应位于：\n"
             f"  {_AI_ASSETS}\n"
             "  两个技能必须在同一个 skills/ 目录下。")

# 数字读音与音节数：与 ecommerce-mix 的 align_bounds 同源，刻意各留一份——
# 旁白技能不该依赖混剪技能。**改口径时两边一起改。**
_CN = "零一二三四五六七八九"
_UNIT = ["", "十", "百", "千"]


def _int_to_cn(n):
    s = str(n)
    if len(s) > 4:
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


# 通用口播风格骨架。刻意不含任何"放慢/清晰/抑扬顿挫"字样——那些词是 LRA 杀手。
# 「不要夸张的抑扬顿挫」这种否定写法同样危险：TTS 对词本身敏感，实测会把 LRA 压到 2.9。
STYLE_TEMPLATE = (
    "家常口播，像跟身边姐妹说话，不要播音腔，不要念稿感。"
    "一句话一口气说完，句与句之间不要停顿换气，语速偏快、密，句子连着走，逗号处只轻轻带过。"
    "句尾不要上扬拖长。语气有强弱：讲产品时笃定，说数字顺势带过去、不要刻意放慢，"
    "收尾放平、像随口叮嘱。"
)

# 带货激情档（--energy high）。**只换情绪落点，连贯性约束逐字保留**——
# 曾试过整段重写、加「重音落在X」「报价两句加力」等局部强调指令，结果 raw 从 5.40 掉到
# 4.54 字/秒、停顿 14.5%、LRA 2.9：局部强调会诱发 TTS 加换气，越强调越平。
# 所以激情靠「整体语气 + 收尾往前推」表达，不靠局部重音指令，也不靠提速。
# 必须写「音量有强弱」四个字——写成「语气有强弱」实测 LRA 只有 2.6（停顿已压到 3.6%
# 仍不起伏）。模型只对**声学幅度**指令响应，「精神头足/带劲/不塌」这类抽象形容词无效。
STYLE_ENERGETIC = (
    "家常口播，像跟身边姐妹说话，不要播音腔，不要念稿感。"
    "一句话一口气说完，句与句之间不要停顿换气，语速偏快、密。"
    "音量有强弱：夸产品时笃定带点得意，价格两句像替朋友算账一口气报出来，"
    "数字顺势念过去、不要刻意放慢，收尾笃定有力往前推、别放平、别念完就散。"
)

# 验收线
TARGET_LUFS = -18.7       # 混剪时 +3dB 落到 -15.7，整片约 -15
MAX_PEAK = -1.0
MIN_LRA = 3.5             # 低于此说明起伏平，重生成
MAX_SILENCE_PCT = 15.0    # 高于此说明断句碎
ATEMPO_RANGE = (0.85, 1.18)


def ff(args, timeout=600):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stderr


def dur_of(path):
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60).stdout.strip())


def measure(path):
    d = dur_of(path)
    # 验收口径固定 -38dB/0.28s：只数"真停顿"，不数字与字之间的气口。
    # （对齐才用灵敏阈 -33dB/0.16，两套阈值不能混用，否则同一句结论相反：
    #   实测同一版旁白 -38/0.28 是 8 处 8.2%，-33/0.16 是 24 处 17.7%。）
    err = ff(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
              "-af", "silencedetect=-38dB:d=0.28", "-f", "null", "-"])
    sil = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", err)]
    sil_n, sil_s = len(sil), sum(sil)

    err = ff(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
              "-af", "ebur128=peak=true", "-f", "null", "-"])
    tail = err.split("Summary:")[-1]

    def grab(pat, default=0.0):
        m = re.search(pat, tail)
        return float(m.group(1)) if m else default

    return {
        "sec": round(d, 3),
        "sil_n": sil_n,
        "sil_sec": round(sil_s, 3),
        "sil_pct": round(sil_s * 100 / d, 1) if d else 0.0,
        "lufs": grab(r"I:\s+(-?[\d.]+) LUFS"),
        "lra": grab(r"LRA:\s+([\d.]+) LU"),
        "peak": grab(r"Peak:\s+(-?[\d.]+) dBFS"),
    }


def chars_of(text):
    return char_count(text)


def render(src, out, af):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
                    "-af", af, "-c:a", "libmp3lame", "-b:a", "192k", str(out)], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", required=True, help="角色卡 card.json")
    ap.add_argument("--sample", default="", help="音色锚覆盖路径（片内锚标准链路：从本片过审钩子提取，见 ecommerce-voice-anchor；不传则回退 card.json 的 voice.sample）")
    ap.add_argument("--text", default="", help="台词文本")
    ap.add_argument("--script-text", default="", help="台词文本文件")
    ap.add_argument("--script-json", default="", help="文案 JSON（取 seq>=2 的 content 串联）")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--target-rate", type=float, default=4.9, help="目标字/秒（视频号中老年 4.7–5.0）")
    ap.add_argument("--style-extra", default="", help="追加风格指令，如「价格像替朋友算账」")
    ap.add_argument("--energy", choices=("normal", "high"), default="normal",
                    help="情绪档：high = 带货激情档（不改语速，只换语气落点）")
    ap.add_argument("--style", default="", help="整段覆盖风格（一般别用，优先用 --energy）")
    ap.add_argument("--no-eq", action="store_true", help="跳过 EQ 提亮")
    ap.add_argument("--sentence-split", action="store_true",
                    help="按句号/问号/感叹号切句（默认按 JSON 段切）")
    ap.add_argument("--from-audio", default="",
                    help="跳过 TTS，直接对现成音频做配速/提亮/校准（重调参数、补录用，不花钱）")
    ap.add_argument("--keep-raw", action="store_true")
    ap.add_argument("--tries", type=int, default=0,
                    help="TTS 抽卡数，自动按指标择优（0=自动：high 档 3 条，normal 档 1 条）")
    ap.add_argument("--atempo-min", type=float, default=0.0,
                    help="显式放宽降速下限（默认 0.85）。高起伏抽卡常需 0.72–0.80，"
                         "atempo 不改 LRA，配合 EQ 提亮可保住起伏；须人耳确认不发闷")
    args = ap.parse_args()

    # ── 取文本 ──
    sentences = []
    if args.script_json:
        d = json.loads(Path(args.script_json).read_text(encoding="utf-8"))
        sentences = [s["content"] for s in d["segments"] if s.get("seq", 0) >= 2]
        text = "".join(sentences)
    elif args.script_text:
        text = Path(args.script_text).read_text(encoding="utf-8").strip()
    elif args.text:
        text = args.text
    else:
        ap.error("要给 --text / --script-text / --script-json 之一")
    if not sentences:
        sentences = [x for x in re.split(r"(?<=[。！？])", text) if x.strip()]

    n = chars_of(text)
    card = json.loads(Path(args.card).read_text(encoding="utf-8"))
    char_dir = Path(args.card).resolve().parent
    sample = Path(args.sample) if args.sample else char_dir / card["voice"].get("sample", "voice.mp3")
    if not sample.exists():
        sys.exit(f"错误：找不到音色锚 {sample}" + ("" if args.sample else
                 "——2026-11-06 起片内锚是标准链路：先跑 ecommerce-voice-anchor 从本片过审钩子提取，用 --sample 指进来"))

    base = STYLE_ENERGETIC if args.energy == "high" else STYLE_TEMPLATE
    style = args.style or (base + (("。" + args.style_extra) if args.style_extra else ""))
    atempo_lo = args.atempo_min or ATEMPO_RANGE[0]
    if args.energy == "high" and not args.style:
        print(f"情绪档 high（带货激情档，语速仍锁 {args.target_rate} 字/秒）")
    load_env()
    model = card["voice"].get("clone_model", "mimo-v2.5-tts-voiceclone")

    out = Path(args.out)
    raw = out.with_name(out.stem + "_raw.mp3")

    # ── 生成 ──
    if args.from_audio:
        raw = Path(args.from_audio)
        m0 = measure(raw)
        r0 = n / m0["sec"]
        print(f"沿用现成音频 {raw.name}")
        print(f"  raw {m0['sec']:.2f}s  {r0:.2f} 字/秒  停顿 {m0['sil_pct']}%  LRA {m0['lra']}")
    else:
        raw = out.with_name(out.stem + "_raw.mp3")
        tries = args.tries or (3 if args.energy == "high" else 1)
        print(f"生成中… {n} 字，音色锚 {sample.name}，抽 {tries} 条择优")
        # 数字转中文读法再送 TTS：'150' 可能被念成'一五零'，'9.2' 可能念成'九二'
        spoken = speech_text(text)
        voice = ("data:audio/mpeg;base64," +
                 __import__("base64").b64encode(sample.read_bytes()).decode())
        payload = {"format": "mp3", "voice": voice}
        cands = []
        for k in range(1, tries + 1):
            p = out.with_name(out.stem + f"_try{k}.mp3")
            audio = call_tts(model,
                             [{"role": "user", "content": style},
                              {"role": "assistant", "content": spoken}], payload)
            p.write_bytes(audio)
            m = measure(p)
            r = n / m["sec"]
            atempo_needed = args.target_rate / r
            ok = (m["lra"] >= MIN_LRA and m["sil_pct"] <= MAX_SILENCE_PCT
                  and atempo_lo <= atempo_needed <= ATEMPO_RANGE[1])
            # 达标优先 → LRA 高 → 停顿少 → 语速离目标近
            rank = (1 if ok else 0, m["lra"], -m["sil_pct"], -abs(r - args.target_rate))
            cands.append((rank, k, p, m, r))
            print(f"  候选{k}: {m['sec']:.2f}s {r:.2f}字/秒 "
                  f"停顿{m['sil_pct']}% LRA {m['lra']} {'✓达标' if ok else '✗'}")
        cands.sort(key=lambda x: x[0], reverse=True)
        _, k, p, m0, r0 = cands[0]
        p.replace(raw)
        for _rank, _k, _p, _m, _r in cands[1:]:
            _p.unlink(missing_ok=True)
        print(f"  采用候选{k} — {m0['sec']:.2f}s  {r0:.2f} 字/秒  "
              f"停顿 {m0['sil_pct']}%  LRA {m0['lra']}")
        if not cands[0][0][0]:
            print("  ⚠️ 这批没有一条全达标，已取指标最好的一条；"
                  "若听感仍平，问题在音色锚或文本，不是 style——别再改措辞碰运气")
    t0 = time.time()

    # ── 配速（只降不猛升，atempo 不改 LRA）──
    # r0 是 raw 的字/秒；要落到 target，时长需 ×(r0/target)，atempo=target/r0
    atempo = max(atempo_lo, min(ATEMPO_RANGE[1], args.target_rate / r0))
    if abs(atempo - 1) > 0.02:
        print(f"  atempo {atempo:.3f} → 目标 {args.target_rate} 字/秒")
    if atempo < 0.85:
        print("  ⚠️ atempo 低于常规下限 0.85（已配合 EQ 3kHz 提亮），人耳确认是否发闷")
    if args.target_rate / r0 < atempo_lo - 1e-6:
        print(f"  ⚠️ raw 太快，放到降速下限 {atempo_lo:.2f} 仍追不上——抬 --target-rate 或重生成")
    elif args.target_rate / r0 > ATEMPO_RANGE[1]:
        print("  ⚠️ raw 比目标慢太多，加速会发飘——降 --target-rate 或重生成")

    eq = "" if args.no_eq else ",highpass=f=85,equalizer=f=3000:t=q:w=1.2:g=2.5"
    tmp = out.with_name(out.stem + "_tmp.mp3")
    render(raw, tmp, f"atempo={atempo:.4f}" + eq)

    # ── 响度/峰值：取更严的那个增益 ──
    m1 = measure(tmp)
    gain = min(TARGET_LUFS - m1["lufs"], MAX_PEAK - m1["peak"])
    render(raw, tmp, f"atempo={atempo:.4f}" + eq + f",volume={gain:.2f}dB")
    m2 = measure(tmp)

    tmp.replace(out)
    if not args.keep_raw and not args.from_audio:   # from-audio 时 raw 是用户的源文件，绝不能删
        raw.unlink(missing_ok=True)

    # ── 质检 ──
    r2 = n / m2["sec"]
    issues = []
    if m2["sil_pct"] > MAX_SILENCE_PCT:
        issues.append(f"停顿 {m2['sil_pct']}% > {MAX_SILENCE_PCT}%：断句碎，语气会平——重生成")
    if m2["lra"] < MIN_LRA:
        issues.append(f"LRA {m2['lra']} < {MIN_LRA}：起伏不够，检查 style 是否写了「放慢/清晰」")
    if abs(r2 - args.target_rate) / args.target_rate > 0.06:
        issues.append(f"语速 {r2:.2f} 偏离目标 {args.target_rate} 超 6%")
    if m2["peak"] > -0.5:
        issues.append(f"峰值 {m2['peak']} dBFS 过高")

    print(f"\n旁白 {out}")
    print(f"  {m2['sec']:.2f}s｜{n} 字｜{r2:.2f} 字/秒（目标 {args.target_rate}）｜atempo {atempo:.3f}")
    print(f"  停顿 {m2['sil_n']} 处 {m2['sil_sec']:.2f}s（{m2['sil_pct']}%）｜"
          f"LRA {m2['lra']}｜{m2['lufs']} LUFS｜峰值 {m2['peak']} dBFS")
    print(f"  {'✓ 通过' if not issues else '⚠️ ' + '；'.join(issues)}")
    print(f"  用时 {time.time() - t0:.0f}s")

    meta = out.with_suffix(".metrics.json")
    meta.write_text(json.dumps({"file": str(out), "chars": n, "atempo": round(atempo, 4),
                                "gain_db": round(gain, 2), "rate": round(r2, 2),
                                "metrics": m2, "issues": issues},
                               ensure_ascii=False, indent=1), encoding="utf-8")

    if issues:
        sys.exit(1)


if __name__ == "__main__":
    main()
