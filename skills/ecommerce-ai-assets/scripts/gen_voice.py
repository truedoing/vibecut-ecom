#!/usr/bin/env python3
"""MiMo TTS 音色生成：voicedesign 造音色锚样本 + voiceclone 合成正式配音。

用法：
  1) 造锚样本（按 card.json 的 voice.desc 现造音色，存为角色目录下 voice.mp3）：
     python gen_voice.py --card assets/ai_assets/characters/ch_001_林岚/card.json

  2) 用锚样本合成正式台词（voiceclone，保证与 sample 同音色）：
     python gen_voice.py --card .../card.json --clone "今天给大家看一个东西。" -o out.mp3

key 从 .env 读：MIMO_API_KEY / MIMO_BASE_URL / MIMO_TTS_MODEL(默认 mimo-v2.5-tts-voicedesign)
注意：voicedesign 不返回固定 voice_id，每次现造；一致性靠锚样本 + voiceclone。
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from voice_audit import measure, judge          # noqa: E402  同目录的锚验收器

# 锚文本不是产品文案，是**韵律模板**：它决定 clone 能学到什么样的动态。
# 旧版是低能量闲聊（省略号 / 无数字 / 全平叙），造出的锚 LRA 1.5、停顿 17.4%，
# clone 只能靠抽卡碰起伏。新版要求：① 开头生活感 ② 中段有高能量转折
# ③ 含数字（正式旁白数字多，锚里要练过）④ 短促有力的收尾 ⑤ 不写省略号
# （会诱导停顿）⑥ 不指向任何品类，跨角色跨品类可复用。
DEFAULT_TEXT = (
    "哎，你先别急着收，听我说完。这个我真试过，跟想的不一样，好得很！"
    "一盒十二袋，一袋八克，一天两袋就够了。别嫌我啰嗦，算下来真不贵。"
    "要拿就现在拿，别再搁着了。"
)

# 「松弛」≠「含糊」。当年为躲播音腔把两者绑在一起写，是本次返工的根因。
DESC_FLAWS = ("连读吞音", "含糊感", "尾音往下掉",
              "自然换气和小停顿", "句子之间有自然换气")


def load_env():
    for p in (Path(__file__).resolve().parents[3] / ".env", Path.cwd() / ".env"):
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
            break


def call_tts(model, messages, audio_cfg, timeout=180):
    base = os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1").rstrip("/")
    key = os.environ.get("MIMO_API_KEY")
    if not key:
        sys.exit("错误：缺 MIMO_API_KEY（.env）")
    body = json.dumps({"model": model, "messages": messages, "audio": audio_cfg}).encode()
    req = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"api-key": key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read())
    data = resp["choices"][0]["message"].get("audio", {}).get("data")
    if not data:
        sys.exit(f"错误：响应里没有音频 {json.dumps(resp, ensure_ascii=False)[:500]}")
    return base64.b64decode(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", required=True, help="角色卡 card.json 路径")
    ap.add_argument("--text", default=DEFAULT_TEXT, help="合成文本（造锚样本时也用它）")
    ap.add_argument("--clone", action="store_true",
                    help="voiceclone 模式：用 sample.mp3 做音色锚合成 --text")
    ap.add_argument("-o", "--out", default="", help="输出音频路径（clone 模式用；造锚时可指定候选名）")
    ap.add_argument("--style", default="",
                    help="风格指令（放 role=user，不出声）：控制语速/情绪/口吻，如「语速略快，像跟熟人聊天，不赶但利落」")
    ap.add_argument("--no-write", action="store_true", help="造候选时不回写 card.json（选定后才回写）")
    ap.add_argument("--model", default="", help="覆盖模型（默认读 .env MIMO_TTS_MODEL）")
    ap.add_argument("--desc", default="",
                    help="覆盖 card.json 的 voice.desc（造候选试验用，配合 --no-write，不回写卡）")
    ap.add_argument("--tries", type=int, default=1,
                    help="造锚抽 N 条，按 LRA/停顿/响度自动择优（voicedesign 每次现造，方差极大）")
    ap.add_argument("--keep-cands", action="store_true",
                    help="保留全部候选供人耳对比（默认只留最优）")
    ap.add_argument("--ref-audio", default="",
                    help="clone 时覆盖 card 的 sample（试新锚用，不回写卡）")
    args = ap.parse_args()

    card_path = Path(args.card).resolve()
    card = json.loads(card_path.read_text())
    voice = card["voice"]
    char_dir = card_path.parent

    load_env()
    if args.clone:
        sample = Path(args.ref_audio) if args.ref_audio else char_dir / voice.get("sample", "voice.mp3")
        if not sample.exists():
            sys.exit(f"错误：先造锚样本（跑一次不带 --clone），找不到 {sample}")
        model = args.model or voice.get("clone_model", "mimo-v2.5-tts-voiceclone")
        b64 = base64.b64encode(sample.read_bytes()).decode()
        data_url = f"data:audio/mpeg;base64,{b64}"  # 官方要求 DataURL，裸 base64 会 400
        out = Path(args.out) if args.out else char_dir / f"clone_{int(time.time())}.mp3"
        msgs = []
        if args.style:
            # 官方：风格指令放 user，不进音频；文本必须放 assistant
            msgs.append({"role": "user", "content": args.style})
        msgs.append({"role": "assistant", "content": args.text})
        audio = call_tts(model, msgs, {"format": "mp3", "voice": data_url})
        out.write_bytes(audio)
        print(f"OK(clone) {out} {len(audio)} bytes, model={model}, anchor={sample.name}")
        return

    model = args.model or os.environ.get("MIMO_TTS_MODEL", "mimo-v2.5-tts-voicedesign")
    desc = args.desc or voice.get("desc", "")
    if not desc:
        sys.exit("错误：card.json 的 voice.desc 为空——音色描述必须先写全")
    flaws = [w for w in DESC_FLAWS if w in desc]
    if flaws:
        print(f"  ⚠️ desc 含发音缺陷词 {flaws} —— 松弛≠含糊，这类写法会把锚造成平调糊音")

    out = Path(args.out) if args.out else char_dir / "voice.mp3"
    msgs = [{"role": "user", "content": desc},
            {"role": "assistant", "content": args.text}]
    cfg = {"format": "mp3"}
    tries = max(1, args.tries)

    if tries == 1:
        out.write_bytes(call_tts(model, msgs, cfg))
    else:
        import shutil
        print(f"造锚抽 {tries} 条候选…")
        cands = []
        for k in range(1, tries + 1):
            p = out.with_name(out.stem + f"_cand{k}" + out.suffix)
            p.write_bytes(call_tts(model, msgs, cfg))
            m = measure(p)
            rank = (m["lra"], -m["sil_pct"], -abs(m["lufs"] + 18.7))
            cands.append((rank, k, p, m))
            print(f"  候选{k}: {m['sec']:.2f}s  LRA {m['lra']}  停顿 {m['sil_pct']}%  "
                  f"{'✓' if not judge(m) else '✗'}")
        cands.sort(key=lambda x: x[0], reverse=True)
        _rank, k, p, m = cands[0]
        shutil.copyfile(p, out)
        for _r, _k, _p, _m in cands[1:]:
            if not args.keep_cands:
                _p.unlink(missing_ok=True)
        print(f"  采用候选{k} → {out.name}"
              f"（{'全部候选已保留' if args.keep_cands else '其余候选已删'}）")
        if judge(m):
            print("  ⚠️ 最优候选仍未达标：" + "；".join(judge(m)))

    if args.no_write:
        print(f"OK(candidate) {out} {out.stat().st_size} bytes, model={model}, 未回写 card.json")
        return
    voice.update({"platform": "mimo", "model": model,
                  "clone_model": voice.get("clone_model", "mimo-v2.5-tts-voiceclone"),
                  "sample": out.name,
                  "sample_note": "sample 即音色锚：正式配音用 voiceclone 传这份样本"})
    card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n")
    print(f"OK {out} {len(audio)} bytes, model={model}, desc已回写 card.json")


if __name__ == "__main__":
    main()
