#!/usr/bin/env python3
"""豆包语音合成 2.0（Doubao-Seed-TTS 2.0）接入。

为什么换它：MiMo 克隆实测音高跨度（F0 p5–p95）10 条抽样最高 12.5 半音，
而 AI 视频里 Seedance 演出来的语音是 20.0——TTS 与本模型的上限就卡在这。
豆包 2.0 是 LLM 驱动的合成：先理解语义与情绪再决定韵律，支持
文本内括号标记（如【越说越急】）与自然语言音色指令，是我们唯一能追平的候选。

两种鉴权（.env 里二选一）：
  老控制台：DOUBAO_APP_ID + DOUBAO_ACCESS_TOKEN   → X-Api-App-Id / X-Api-Access-Key
  新控制台：DOUBAO_TTS_KEY                        → X-Api-Key
两种 Resource：
  seed-tts-2.0  预设音色（34 个 2.0 音色，支持情绪指令）
  seed-icl-2.0  声音复刻音色（5 秒样本复刻；官方说明自然语言指令不支持，靠文本括号标记）

用法：
  python3 doubao_tts.py --text "台词" --speaker zh_female_shuangkuaisisi_uranus_bigtts -o a.mp3
  python3 doubao_tts.py --text-file vo.txt --instruction "急着替人算账，越说越快" -o b.mp3
  python3 doubao_tts.py --text "【越说越急】五十九块九！" -o c.mp3     # 括号标记直接写在文本里
"""
import argparse
import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
DEFAULT_SPEAKER = "zh_female_shuangkuaisisi_uranus_bigtts"   # 爽快思思 2.0


def load_env():
    for p in (Path(__file__).resolve().parents[3] / ".env", Path.cwd() / ".env"):
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
            break


def auth_headers(resource):
    h = {"Content-Type": "application/json",
         "X-Api-Resource-Id": resource,
         "X-Api-Request-Id": str(uuid.uuid4())}
    # 新控制台的 API Key 优先：实测它的授权是账户级的，不会出现
    # 「App ID + Access Token 走了但资源没 grant」的 45000010
    key = os.environ.get("DOUBAO_TTS_KEY")
    app_id = os.environ.get("DOUBAO_APP_ID")
    token = os.environ.get("DOUBAO_ACCESS_TOKEN")
    if key:
        h["X-Api-Key"] = key
        return h, f"X-Api-Key {key[:8]}…"
    if app_id and token:
        h["X-Api-App-Id"] = app_id
        h["X-Api-Access-Key"] = token
        return h, f"AppId {app_id[:6]}…（旧式鉴权，易报 45000010）"
    sys.exit("错误：缺凭证。.env 里需要 DOUBAO_APP_ID + DOUBAO_ACCESS_TOKEN，"
             "或新控制台的 DOUBAO_TTS_KEY")


def synthesize(text, speaker, resource, instruction, fmt, rate, speed, loudness):
    headers, who = auth_headers(resource)
    req_params = {
        "text": text,
        "speaker": speaker,
        "audio_params": {"format": fmt, "sample_rate": rate,
                         "speech_rate": speed, "loudness_rate": loudness},
    }
    # 自然语言音色指令（2.0 的 context_texts）；seed-icl-2.0 复刻音色传了可能被忽略
    if instruction:
        req_params["context_texts"] = [instruction]
    body = json.dumps({"user": {"uid": "workbuddy"}, "req_params": req_params},
                      ensure_ascii=False).encode()

    req = urllib.request.Request(ENDPOINT, data=body, headers=headers, method="POST")
    print(f"→ {ENDPOINT}\n  鉴权 {who} | resource {resource} | speaker {speaker}")
    chunks, done, t0 = [], False, time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            for raw in r:
                line = raw.decode("utf-8", "ignore").strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    continue
                hd = frame.get("header", {})
                code = hd.get("code", 0)
                msg = hd.get("message", "")
                if code not in (0, 20000000):
                    sys.exit(f"错误 code={code} {msg}（logid {hd.get('logid', '?')}）")
                data = frame.get("data")
                if data:
                    import base64
                    chunks.append(base64.b64decode(data))
                if code == 20000000:
                    done = True
                    break
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:400]
        sys.exit(f"HTTP {e.code}：{detail}\n"
                 f"排查：401/403 多为语音服务未开通或 key 未绑语音资源；"
                 f"404 多为 endpoint 或 resource_id 不对")
    print(f"  收 {len(chunks)} 帧 / {sum(len(c) for c in chunks)} B / {time.time()-t0:.1f}s"
          f"{'（收到结束帧）' if done else '（读到流结束）'}")
    if not chunks:
        sys.exit("错误：没收到音频数据")
    return b"".join(chunks)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--text", help="台词文本（可含【情绪标记】）")
    g.add_argument("--text-file", help="台词文本文件")
    ap.add_argument("--speaker", default=DEFAULT_SPEAKER, help="2.0 音色 ID")
    ap.add_argument("--resource", default="seed-tts-2.0",
                    choices=("seed-tts-2.0", "seed-icl-2.0"))
    ap.add_argument("--instruction", default="", help="自然语言音色/情绪指令")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--format", default="", help="mp3 / wav / pcm（默认按扩展名）")
    ap.add_argument("--sample-rate", type=int, default=24000)
    ap.add_argument("--speech-rate", type=int, default=0, help="-50~100，0 默认，100≈2倍速")
    ap.add_argument("--loudness-rate", type=int, default=0, help="-50~100")
    a = ap.parse_args()

    load_env()
    text = a.text or Path(a.text_file).read_text(encoding="utf-8").strip()
    if not text:
        sys.exit("错误：文本为空")
    fmt = a.format or (Path(a.out).suffix.lstrip(".") or "mp3")
    audio = synthesize(text, a.speaker, a.resource, a.instruction,
                       fmt, a.sample_rate, a.speech_rate, a.loudness_rate)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_bytes(audio)
    print(f"OK {a.out} {len(audio)} bytes  {len(text)} 字")


if __name__ == "__main__":
    main()
