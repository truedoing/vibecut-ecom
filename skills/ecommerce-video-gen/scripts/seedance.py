#!/usr/bin/env python3
"""Seedance 出片（apiyi 网关）：参考图 + 参考音频 + 异步轮询 + 自动落盘。

apiyi 官方的 seedance_video.py 只暴露了图片参数，本脚本补上 `audio_url`
（原生 API 支持，最多 3 个音频、总时长 ≤15 秒、必须搭配图片），并加了三件实用能力：

  --prompt-file   从文件读提示词（长提示词带 {} 台词，走 shell 转义必翻车）
  --dry-run       只打印请求体不提交（先核对参考图顺序、音频时长，不花一分钱）
  --task-id       查/续传已提交的任务（轮询中断不用重新出片，重新出片要重复计费）

用法：
  python3 seedance.py --prompt-file p.txt --ref-image 妻.jpg --ref-image 夫.jpg \\
      --ref-image 环境.jpg --ref-audio 夫.mp3 --ref-audio 妻.mp3 \\
      --duration 15 --ratio 9:16 -o out.mp4
"""
import argparse
import base64
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TASKS_URL = "https://api.apiyi.com/seedance/api/v3/contents/generations/tasks"

MODELS = {
    "sd25": "doubao-seedance-2-5-260628",
    "mini": "doubao-seedance-2-0-mini-260615",
    "fast": "doubao-seedance-2-0-fast-260128",
    "std": "doubao-seedance-2-0-260128",
}
MODEL_CAPS = {
    "sd25": ("480p", "720p", "1080p"),
    "mini": ("480p", "720p"),
    "fast": ("480p", "720p"),
    "std": ("480p", "720p", "1080p"),
}
MAX_DURATION = {"sd25": 30, "mini": 15, "fast": 15, "std": 15}
MAX_REF_IMAGES = {"sd25": 30, "mini": 9, "fast": 9, "std": 9}
MAX_REF_AUDIO = 3
AUDIO_BUDGET_SEC = 15.0  # 所有参考音频合计上限
RATIOS = ("adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9")

POLL_FIRST_DELAY = 25
POLL_INTERVAL = 15
POLL_TIMEOUT = 20 * 60


def load_env():
    """向上找 .env（技能目录 → 仓库根），补齐 MIMO/APIYI 之类的 key。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        env = parent / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            break


def api_request(url, api_key, body=None):
    # 网关会标 content-encoding: gzip 但实际未压缩，必须 identity
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
    }
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}：{e.read().decode(errors='replace')[:800]}")


def as_source(src, default_mime):
    """URL / asset:// / data: 原样透传；本地文件转 base64 data URI。"""
    if src.startswith(("http://", "https://", "asset://", "data:")):
        return src
    p = Path(src)
    if not p.exists():
        sys.exit(f"文件不存在：{src}")
    ext = p.suffix.lower().lstrip(".")
    mime = {
        "png": "image/png", "webp": "image/webp", "jpg": "image/jpeg",
        "jpeg": "image/jpeg", "mp3": "audio/mpeg", "wav": "audio/wav",
        "m4a": "audio/mp4", "aac": "audio/aac",
    }.get(ext, default_mime)
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def audio_seconds(path):
    """读时长（只用于预算校验，读不到就跳过，不阻塞出片）。"""
    try:
        import subprocess
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path], capture_output=True, text=True, timeout=20)
        return float(out.stdout.strip())
    except Exception:
        return None


def build_content(args):
    content = [{"type": "text", "text": args.prompt}]
    for src in args.ref_image:
        content.append({"type": "image_url",
                        "image_url": {"url": as_source(src, "image/jpeg")},
                        "role": "reference_image"})
    for src in args.ref_audio:
        content.append({"type": "audio_url",
                        "audio_url": {"url": as_source(src, "audio/mpeg")},
                        "role": "reference_audio"})
    return content


def download(url, path):
    # 签名直链，不能带 Authorization
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=600) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)
    return os.path.getsize(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-file", required=True, help="提示词文件（UTF-8）")
    ap.add_argument("--model", default="mini", choices=sorted(MODELS))
    ap.add_argument("--resolution", default="720p", choices=("480p", "720p", "1080p"))
    ap.add_argument("--ratio", default="9:16", choices=RATIOS)
    ap.add_argument("--duration", type=int, default=15)
    ap.add_argument("--ref-image", action="append", default=[], help="参考图，可重复（顺序对应 @Image1/2/3）")
    ap.add_argument("--ref-audio", action="append", default=[], help="参考音频，可重复（顺序对应 @Audio1/2）")
    ap.add_argument("--no-audio", action="store_true", help="关闭模型生成的音频")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("-o", "--out", default="out.mp4")
    ap.add_argument("--dry-run", action="store_true", help="只打印请求体，不提交")
    ap.add_argument("--task-id", default="", help="跳过提交，直接查询已有任务")
    args = ap.parse_args()

    args.prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if not args.prompt:
        sys.exit("提示词为空")

    if len(args.ref_image) > MAX_REF_IMAGES[args.model]:
        sys.exit(f"{args.model} 参考图最多 {MAX_REF_IMAGES[args.model]} 张")
    if len(args.ref_audio) > MAX_REF_AUDIO:
        sys.exit(f"参考音频最多 {MAX_REF_AUDIO} 个")
    if args.ref_audio and not args.ref_image:
        sys.exit("参考音频必须搭配至少一张参考图（无音频单独模式）")
    if args.duration != -1 and not 4 <= args.duration <= MAX_DURATION[args.model]:
        sys.exit(f"{args.model} 时长只支持 4-{MAX_DURATION[args.model]} 秒")
    if args.resolution not in MODEL_CAPS[args.model]:
        sys.exit(f"{args.model} 最高 {MODEL_CAPS[args.model][-1]}")

    total_audio = 0.0
    for a in args.ref_audio:
        d = audio_seconds(a)
        if d:
            total_audio += d
    if total_audio > AUDIO_BUDGET_SEC:
        sys.exit(f"参考音频合计 {total_audio:.1f}s 超上限 {AUDIO_BUDGET_SEC}s")

    body = {
        "model": MODELS[args.model],
        "content": build_content(args),
        "resolution": args.resolution,
        "ratio": args.ratio,
        "duration": args.duration,
    }
    if args.no_audio:
        body["generate_audio"] = False
    if args.seed is not None:
        body["seed"] = args.seed

    load_env()
    api_key = os.environ.get("APIYI_API_KEY")

    if args.dry_run:
        preview = json.loads(json.dumps(body))
        for item in preview["content"]:
            for k in ("image_url", "audio_url"):
                if k in item:
                    u = item[k]["url"]
                    item[k]["url"] = u[:40] + f"...(len={len(u)})" if u.startswith("data:") else u
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        print(f"\n[dry-run] 参考图 {len(args.ref_image)} 张 / 参考音频 {len(args.ref_audio)} 个 "
              f"（合计 {total_audio:.1f}s）/ {args.duration}s {args.resolution} {args.ratio} "
              f"/ model={MODELS[args.model]} / 提示词 {len(args.prompt)} 字")
        print("未提交，未计费。")
        return

    if not api_key:
        sys.exit("缺 APIYI_API_KEY（写进仓库 .env）")

    task_id = args.task_id
    if not task_id:
        try:
            task = api_request(TASKS_URL, api_key, body)
        except RuntimeError as e:
            sys.exit(f"提交失败：{e}")
        task_id = task.get("id")
        if not task_id:
            sys.exit(f"提交失败，响应：{json.dumps(task, ensure_ascii=False)[:600]}")
        print(f"已提交 task_id={task_id}（中断可用 --task-id {task_id} 续查，不会重复计费）")
    else:
        print(f"续查既有任务 task_id={task_id}")

    t0 = time.time()
    if not args.task_id:
        time.sleep(POLL_FIRST_DELAY)
    status = "unknown"
    while True:
        try:
            task = api_request(f"{TASKS_URL}/{task_id}", api_key)
        except RuntimeError as e:
            print(f"  轮询异常（继续）：{e}")
            time.sleep(POLL_INTERVAL)
            continue
        status = task.get("status", "unknown")
        print(f"  [{round(time.time() - t0):>4}s] status={status}")
        if status in ("succeeded", "failed", "expired"):
            break
        if time.time() - t0 > POLL_TIMEOUT:
            sys.exit(f"轮询超时。任务仍在服务端，稍后：--task-id {task_id}")
        time.sleep(POLL_INTERVAL)

    if status != "succeeded":
        sys.exit(f"生成失败（{status}）：{json.dumps(task.get('error') or task, ensure_ascii=False)[:600]}")

    url = (task.get("content") or {}).get("video_url")
    if not url:
        sys.exit(f"成功但无视频地址：{json.dumps(task, ensure_ascii=False)[:600]}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    size = download(url, out)
    tokens = (task.get("usage") or {}).get("completion_tokens", "?")
    print(f"已保存 {out.resolve()}（{size / 1e6:.1f} MB，耗时 {round(time.time() - t0)}s，"
          f"计费 {tokens} tokens）")


if __name__ == "__main__":
    main()
