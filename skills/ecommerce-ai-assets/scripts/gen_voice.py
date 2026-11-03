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

DEFAULT_TEXT = (
    "哎，你看这个啊……就这么一小罐。我一开始也没当回事，后来一尝，还真行。"
    "配料表我看了，就三样，没别的。你要是也吃腻了那些甜的，可以先试试。"
)


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
    ap.add_argument("--no-write", action="store_true", help="造候选时不回写 card.json（选定后才回写）")
    ap.add_argument("--model", default="", help="覆盖模型（默认读 .env MIMO_TTS_MODEL）")
    args = ap.parse_args()

    card_path = Path(args.card).resolve()
    card = json.loads(card_path.read_text())
    voice = card["voice"]
    char_dir = card_path.parent

    load_env()
    if args.clone:
        sample = char_dir / voice.get("sample", "voice.mp3")
        if not sample.exists():
            sys.exit(f"错误：先造锚样本（跑一次不带 --clone），找不到 {sample}")
        model = args.model or voice.get("clone_model", "mimo-v2.5-tts-voiceclone")
        b64 = base64.b64encode(sample.read_bytes()).decode()
        data_url = f"data:audio/mpeg;base64,{b64}"  # 官方要求 DataURL，裸 base64 会 400
        out = Path(args.out) if args.out else char_dir / f"clone_{int(time.time())}.mp3"
        audio = call_tts(model, [{"role": "assistant", "content": args.text}],
                         {"format": "mp3", "voice": data_url})
        out.write_bytes(audio)
        print(f"OK(clone) {out} {len(audio)} bytes, model={model}, anchor={sample.name}")
        return

    model = args.model or os.environ.get("MIMO_TTS_MODEL", "mimo-v2.5-tts-voicedesign")
    desc = voice.get("desc", "")
    if not desc:
        sys.exit("错误：card.json 的 voice.desc 为空——音色描述必须先写全")
    audio = call_tts(model, [
        {"role": "user", "content": desc},
        {"role": "assistant", "content": args.text},
    ], {"format": "mp3"})
    out = Path(args.out) if args.out else char_dir / "voice.mp3"
    out.write_bytes(audio)
    if args.no_write:
        print(f"OK(candidate) {out} {len(audio)} bytes, model={model}, 未回写 card.json")
        return
    voice.update({"platform": "mimo", "model": model,
                  "clone_model": voice.get("clone_model", "mimo-v2.5-tts-voiceclone"),
                  "sample": out.name,
                  "sample_note": "sample 即音色锚：正式配音用 voiceclone 传这份样本"})
    card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n")
    print(f"OK {out} {len(audio)} bytes, model={model}, desc已回写 card.json")


if __name__ == "__main__":
    main()
