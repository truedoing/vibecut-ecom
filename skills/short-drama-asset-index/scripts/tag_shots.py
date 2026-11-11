#!/usr/bin/env python3
"""短剧镜头打标：场景检测切镜头 → 每镜头抽 1 帧 → VLM 批量描述 → shots_epNN.json

**只描述，不认人。** 短剧演员无名无参考照，让模型认人只会更不准（11-08 定的）。
模型输出「老年女性 深色旧棉袄 坐轮椅」这种特征串，人名映射由 rules 层反推、人审一次。

为啥不共用 ecommerce-asset-analysis：一套行业一套技能，各演进各的（张老师两次纠正）。
本脚本字段全是短剧要的（谁能说话、能不能切出去、有没有水印），与商品字段没有交集。

用法：
  python3 tag_shots.py --drama 以真心守护至亲 --ep 5
  python3 tag_shots.py --drama 以真心守护至亲 --ep 5 6 7 --force
  python3 tag_shots.py --drama 以真心守护至亲 --ep 5 --no-vlm    # 只切镜头不打标，先看切成几段
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = ROOT / ".env"

SCENE_THRESHOLD = 0.25   # 场景切换阈值：低于它会把人物小动作当成切镜头
FRAME_SCALE = 640        # 缩图边长：只付 640px 的钱，原片不出本机
BATCH = 6                # 每次调用带几帧
PROMPT_VERSION = "sd-shot-v1"


def load_env() -> dict:
    env = dict(os.environ)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def tool(name: str) -> str:
    """找可执行文件：PATH 里没有时退回 anaconda / homebrew 常见位。"""
    from shutil import which
    hit = which(name)
    if hit:
        return hit
    for cand in (f"/opt/anaconda3/bin/{name}", f"/usr/local/bin/{name}",
                 f"/opt/homebrew/bin/{name}"):
        if Path(cand).exists():
            return cand
    sys.exit(f"找不到 {name}，请装 ffmpeg 或加进 PATH")


def probe_duration(video: Path) -> float:
    r = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def detect_shots(video: Path, duration: float, threshold: float) -> list[dict]:
    """用场景检测拿镜头边界，返回 [{start, end}]。"""
    r = subprocess.run(
        [tool("ffmpeg"), "-v", "error", "-i", str(video), "-vf",
         f"select='gt(scene,{threshold})',metadata=print:file=-", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    cuts = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", r.stdout)]
    cuts = sorted({round(c, 2) for c in cuts if 0.3 < c < duration - 0.3})
    bounds = [0.0] + cuts + [round(duration, 2)]
    return [{"start": bounds[i], "end": bounds[i + 1]} for i in range(len(bounds) - 1)]


def extract_frame(video: Path, t: float, out: Path) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [tool("ffmpeg"), "-v", "error", "-ss", f"{max(t, 0.05):.2f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale={FRAME_SCALE}:-2", "-q:v", "4", "-y", str(out)],
        capture_output=True,
    )
    return out.exists()


TAG_PROMPT = """你是短剧素材的镜头标注员。素材是竖屏 AI 短剧，正反打为主，一句台词一个镜头。
下面按顺序给你 {n} 张镜头帧，每张是一个镜头的中间帧。请对每张图输出一个 JSON 对象，按顺序组成数组返回，不要输出其他文字。

字段：
- "i": 图片序号（与传入一致）
- "shot_size": 从 特写|近景|中景|全景 四选一
- "people": 画面里能看清的人数（整数，没人写 0）
- "who": 数组，每人一条特征串，格式「年龄感+性别+主要服装+显著特征」，
  例如 "老年女性 深色旧棉袄 坐轮椅"、"年轻女性 白色婚纱 盘发"、"中年男性 深色西装"
- "action": 一句话，画面里的人正在做什么，20 字内
- "place": 场景，如 酒店宴会厅|室内客厅|街边|办公室|走廊，看不出写 "未识别"
- "props": 数组，画面里显眼的具体物件（如 轮椅、麦克风、奖状、行李箱），没有就空数组
- "scene_text": 画面里**除对话字幕以外**的文字（奖状、门牌、横幅、招牌上的字），没有写空字符串
- "watermark": 画面底部或角落的水印/免责小字，有就照抄原文，没有写空字符串

重要约束：
1. **绝对不要猜人名、不要写人物关系**（不写"妈妈""女儿""新郎"），只写看得见的外观特征。
2. **画面下方那行对话字幕不算 scene_text**，那是台词，不要抄进 scene_text。
3. 只描述确实看得见的内容，吃不准写 "未识别"，禁止编造。"""


def b64_url(p: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()


def salvage_objects(txt: str) -> list[dict]:
    """模型吐的 JSON 数组坏掉时，逐个花括号切对象单独解析，能救几个救几个。

    实测踩过：ep03 第三批返回少了个逗号，json.loads 整批报废、脚本直接崩。
    一个坏字符不该让整集白跑——救不出来的那几张后面单独重试。
    """
    out: list[dict] = []
    depth, start, in_str, esc = 0, None, False, False
    for i, ch in enumerate(txt):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(txt[start:i + 1])
                    if isinstance(obj, dict):
                        out.append(obj)
                except json.JSONDecodeError:
                    try:  # 常见毛病：对象末尾多逗号、或少了逗号
                        fixed = re.sub(r",\s*}", "}", txt[start:i + 1])
                        fixed = re.sub(r'"\s+"', '","', fixed)
                        out.append(json.loads(fixed))
                    except json.JSONDecodeError:
                        pass
                start = None
    return out


def call_vlm(env: dict, frames: list[Path]) -> tuple[list[dict], dict]:
    url = env["MIMO_BASE_URL"].rstrip("/") + "/chat/completions"
    headers = {"api-key": env["MIMO_API_KEY"], "Content-Type": "application/json"}
    content = [{"type": "image_url", "image_url": {"url": b64_url(p)}} for p in frames]
    content.append({"type": "text", "text": TAG_PROMPT.format(n=len(frames))})
    payload = {"model": env["MIMO_MODEL"], "messages": [{"role": "user", "content": content}],
               "temperature": 0.2, "max_tokens": 3000}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    txt = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    txt = re.sub(r"```(?:json)?|```", "", txt).strip()
    m = re.search(r"\[.*\]", txt, re.S)
    if not m:
        raise ValueError(f"模型没返回 JSON 数组，原文前 200 字：{txt[:200]}")
    body = m.group(0)
    try:
        got = json.loads(body)
        if not isinstance(got, list):
            raise ValueError("顶层不是数组")
    except (json.JSONDecodeError, ValueError) as e:
        got = salvage_objects(body)
        print(f"    [容错] JSON 不完整（{e.__class__.__name__}），逐个对象救回 {len(got)}/{len(frames)}", flush=True)
        if not got:
            raise
    return got, data.get("usage", {})


def process_ep(env: dict, video: Path, ep: int, shots: list[dict],
               workdir: Path, use_vlm: bool) -> tuple[list[dict], dict]:
    frames: list[Path] = []
    for idx, s in enumerate(shots):
        mid = (s["start"] + s["end"]) / 2
        f = workdir / f"ep{ep:02d}_s{idx:03d}.jpg"
        if extract_frame(video, mid, f):
            frames.append(f)
        else:
            frames.append(None)  # type: ignore[arg-type]

    if not use_vlm:
        for idx, s in enumerate(shots):
            s["frame"] = frames[idx].name if frames[idx] else None
        return shots, {}

    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def apply_tags(chunk: list[tuple[int, Path]], got: list[dict]) -> int:
        """把模型结果贴回镜头，返回贴上几条。"""
        by_i = {int(g.get("i", -1)): g for g in got if isinstance(g, dict)}
        n = 0
        for local_i, (idx, _f) in enumerate(chunk):
            # 模型可能从 1 开始也可能从 0 开始，两种都试
            g = by_i.get(local_i) or by_i.get(local_i + 1) or (got[local_i] if local_i < len(got) else {})
            if not isinstance(g, dict) or not g:
                continue
            shots[idx]["tag"] = {
                "shot_size": g.get("shot_size", "未识别"),
                "people": g.get("people", 0),
                "who": g.get("who", []),
                "action": g.get("action", ""),
                "place": g.get("place", "未识别"),
                "props": g.get("props", []),
                "scene_text": g.get("scene_text", ""),
                "watermark": g.get("watermark", ""),
            }
            n += 1
        return n

    def ask(chunk: list[tuple[int, Path]]) -> None:
        try:
            got, usage = call_vlm(env, [f for _, f in chunk])
        except Exception as e:  # 网络抖动、模型抽风都不该让整集崩掉
            print(f"    [ep{ep:02d}][重试] 本批失败：{e.__class__.__name__} {str(e)[:80]}", flush=True)
            return
        for k in usage_total:
            usage_total[k] += int(usage.get(k, 0) or 0)
        apply_tags(chunk, got)

    for base in range(0, len(frames), BATCH):
        chunk = [(idx, frames[idx]) for idx in range(base, min(base + BATCH, len(frames)))
                 if frames[idx]]
        if not chunk:
            continue
        ask(chunk)
        print(f"  ep{ep:02d} 已打标 {min(base + BATCH, len(frames))}/{len(frames)} 个镜头",
              flush=True)

    # 兜底：没贴上标的镜头逐帧单独重试（一次一张，成本很低，但能保证不漏镜头）
    missing = [(idx, frames[idx]) for idx in range(len(frames))
               if frames[idx] and "tag" not in shots[idx]]
    if missing:
        print(f"  ep{ep:02d} 兜底重试 {len(missing)} 个没打上标的镜头", flush=True)
        for item in missing:
            ask([item])
        still = [idx for idx, _ in missing if "tag" not in shots[idx]]
        if still:
            print(f"  [ep{ep:02d}][警告] 仍有 {len(still)} 个镜头没打上标：{still}", flush=True)

    for idx, s in enumerate(shots):
        s["frame"] = frames[idx].name if frames[idx] else None
    return shots, usage_total


def run_one_ep(env: dict, video: Path, ep: int, threshold: float, use_vlm: bool) -> dict:
    """跑一整集：切镜头 → 抽帧 → 打标。并行时每集一个线程跑这个函数。"""
    dur = probe_duration(video)
    shots = detect_shots(video, dur, threshold)
    print(f"ep{ep:02d}  {dur:.1f}s  切成 {len(shots)} 个镜头", flush=True)

    with tempfile.TemporaryDirectory(prefix=f"sd_shots_ep{ep:02d}_") as td:
        shots, usage = process_ep(env, video, ep, shots, Path(td), use_vlm)

    return {
        "ep": ep,
        "file": video.name,
        "duration": round(dur, 2),
        "shot_count": len(shots),
        "scene_threshold": threshold,
        "prompt_version": PROMPT_VERSION if use_vlm else None,
        "usage": usage,
        "shots": shots,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drama", required=True)
    ap.add_argument("--ep", type=int, nargs="+", required=True)
    ap.add_argument("--force", action="store_true", help="已有结果也重跑")
    ap.add_argument("--no-vlm", action="store_true", help="只切镜头不打标")
    ap.add_argument("--threshold", type=float, default=SCENE_THRESHOLD)
    ap.add_argument("--jobs", type=int, default=4,
                    help="并行集数（默认 4）。集与集之间互不依赖，产物按集落盘，结果与串行一致")
    args = ap.parse_args()

    env = load_env()
    src_root = env.get("SHORT_DRAMA_ASSET_ROOT", "")
    if not src_root:
        sys.exit("未配置 SHORT_DRAMA_ASSET_ROOT（检查 .env）")
    drama_dir = Path(src_root) / args.drama
    if not drama_dir.exists():
        sys.exit(f"素材目录不存在：{drama_dir}")

    out_dir = ROOT / "assets" / "short-drama" / args.drama / "shots"
    out_dir.mkdir(parents=True, exist_ok=True)

    use_vlm = not args.no_vlm
    if use_vlm and not env.get("MIMO_API_KEY"):
        sys.exit("未找到 MIMO_API_KEY（检查 .env）")

    videos = {}
    for p in sorted(drama_dir.glob("*.mp4")):
        m = re.search(r"(?:第)?(\d+)(?:集)?\.mp4$", p.name)
        if m:
            videos[int(m.group(1))] = p

    todo = []
    for ep in args.ep:
        if ep not in videos:
            print(f"ep{ep:02d} 跳过：找不到视频")
            continue
        dst = out_dir / f"shots_ep{ep:02d}.json"
        if dst.exists() and not args.force:
            print(f"ep{ep:02d} 跳过：已存在 {dst.name}")
            continue
        todo.append(ep)
    if not todo:
        print("没有要跑的集")
        return

    jobs = max(1, min(args.jobs, len(todo)))

    def save(payload: dict) -> None:
        dst = out_dir / f"shots_ep{payload['ep']:02d}.json"
        dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"ep{payload['ep']:02d} -> {dst.name}   "
              f"tokens={payload['usage'].get('total_tokens', 0)}  镜头={payload['shot_count']}",
              flush=True)

    if jobs == 1:
        for ep in todo:
            save(run_one_ep(env, videos[ep], ep, args.threshold, use_vlm))
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        print(f"并行 {jobs} 路，共 {len(todo)} 集", flush=True)
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(run_one_ep, env, videos[ep], ep, args.threshold, use_vlm): ep
                    for ep in todo}
            for fut in as_completed(futs):
                ep = futs[fut]
                try:
                    save(fut.result())
                except Exception as e:
                    print(f"ep{ep:02d} 失败：{e.__class__.__name__} {str(e)[:120]}", flush=True)

    _rebuild_sqlite(args.drama)


def _rebuild_sqlite(drama: str) -> None:
    """镜头索引变更后重建派生缓存 index.sqlite（11-11 定：有变更即更新，缓存可随时全量重建）。"""
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_index.py")
    try:
        r = subprocess.run([sys.executable, script, "--drama", drama],
                           capture_output=True, text=True, timeout=180)
        print("SQLite 查询缓存已重建") if r.returncode == 0 else \
            print(f"SQLite 重建失败（JSON 不受影响）：{r.stderr.strip()[-200:]}")
    except Exception as e:
        print(f"SQLite 重建跳过（JSON 不受影响）：{e}")


if __name__ == "__main__":
    main()
