#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官方素材分析：摸底 → 切镜头 → 抽帧 → VLM 打标 → asset_index.json + 素材台账.md
用法见 SKILL.md。断点续跑：按视频文件粒度，已完成且源文件 mtime 未变的直接跳过。
"""
import argparse, base64, json, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov"}
ROOT = Path(__file__).resolve().parents[3]  # 仓库根（按技能自身位置上溯三级，不写死机器路径）

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def load_env():
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env.update({k: v for k, v in os.environ.items() if k.startswith(("MIMO_", "ZHIPU_"))})
    return env

# ---------- 第 1 步：摸底 ----------
def probe_video(path: Path):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height,r_frame_rate",
           "-show_entries", "format=duration,size",
           "-of", "json", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    d = json.loads(r.stdout)
    st = (d.get("streams") or [{}])[0]
    fmt = d.get("format", {})
    fps = st.get("r_frame_rate", "30/1")
    try:
        fps = round(eval(fps), 2)
    except Exception:
        fps = 30.0
    return {"width": st.get("width"), "height": st.get("height"), "fps": fps,
            "duration": round(float(fmt.get("duration", 0)), 2),
            "size_bytes": int(fmt.get("size", 0))}

# ---------- 第 2 步：切镜头 ----------
SCENE_CMD = ("fps=8,scale=320:-2,select='gt(scene,{thr})',showinfo")
def detect_shots(path: Path, duration: float, thr=0.30, max_shots=15, min_len=1.2,
                 long_clip=30.0, step=8.0, max_seg=8):
    # 长镜头（航拍/沙漠连续运动镜头）：不扫全片，等间隔伪镜头。实测 57s 全片检测 22.9s 且 0 切点，
    # 定点取帧只要 0.34s，8 秒采样足以描述"这一段画面是什么"。
    if duration > long_clip:
        n = max(1, min(max_seg, round(duration / step)))
        size = duration / n
        return [{"start": round(i * size, 2), "end": round((i + 1) * size, 2), "sampled": True}
                for i in range(n)]
    vf = SCENE_CMD.format(thr=thr)
    cmd = ["ffmpeg", "-v", "info", "-i", str(path), "-vf", vf, "-an", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    cuts = sorted({float(m) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr)})
    cuts = [c for c in cuts if 0.2 < c < duration - 0.3]
    bounds = [0.0] + cuts + [duration]
    shots = []
    for a, b in zip(bounds, bounds[1:]):
        if shots and b - shots[-1][0] < min_len:
            shots[-1][1] = b
        else:
            shots.append([a, b])
    if shots:
        shots[-1][1] = duration
    # 镜头数超限：按间隙从小到大合并，直到 <= max_shots
    while len(shots) > max_shots:
        gaps = [(shots[i+1][0] - shots[i][1], i) for i in range(len(shots) - 1)]
        _, i = min(gaps)
        shots[i][1] = shots[i+1][1]
        del shots[i+1]
    return [{"start": round(a, 2), "end": round(b, 2)} for a, b in shots]

# ---------- 第 3 步：抽帧 ----------
def extract_frame(video: Path, t: float, out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(t, 0.1):.2f}", "-i", str(video),
           "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "5", "-y", str(out)]
    subprocess.run(cmd, capture_output=True)
    return out.exists()

# ---------- 第 4 步：VLM ----------
def b64_url(p: Path):
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()

TAG_PROMPT = """你是电商带货视频的素材标注员。素材来自「{label}」品类的商家实拍，画面主体通常在：
产品罐体/包装、原料颗粒（认不准具体作物就写"颗粒原料"）、生产车间、种植产地、沙漠绿洲航拍。
下面按顺序给你 {n} 张视频抽帧图片，对应关系：{mapping}
请对每张图输出一个 JSON 对象，按顺序组成 JSON 数组返回，不要输出任何其他文字。
每个对象的字段：
- "i": 图片序号（与传入一致）
- "desc": 一句话客观画面描述，20-40字，具体到物体与动作，禁止"美味""诱人"等主观词
- "subject": 画面主体（如：罐体/油莎豆颗粒/灌装线/酱体特写/沙漠绿洲）
- "shot_size": 从 特写|近景|中景|全景|航拍 五选一
- "scene": 场景（如：室内桌拍/车间/田间/沙漠/天空）
- "text": 画面中清晰可读的文字，没有则为空字符串
- "usage": 数组，从 product|process|origin|texture|hand|other 六个标签里选 1-2 个
  （product=罐体包装, process=工艺过程, origin=种植产地, texture=质地细节, hand=手部动作, other=其他）
只描述确实可见的内容，认不出就写"未识别"，禁止编造品牌或人物身份。"""

def call_vlm(provider, env, images: list[Path], mapping: str, label: str):
    if provider == "mimo":
        url = env["MIMO_BASE_URL"].rstrip("/") + "/chat/completions"
        headers = {"api-key": env["MIMO_API_KEY"], "Content-Type": "application/json"}
        model = env["MIMO_MODEL"]
    else:  # zhipu
        url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        headers = {"Authorization": "Bearer " + env["ZHIPU_API_KEY"], "Content-Type": "application/json"}
        model = env.get("ZHIPU_MODEL", "glm-4v-flash")
    content = [{"type": "image_url", "image_url": {"url": b64_url(p)}} for p in images]
    content.append({"type": "text", "text": TAG_PROMPT.format(n=len(images), mapping=mapping, label=label)})
    payload = {"model": model, "messages": [{"role": "user", "content": content}],
               "temperature": 0.2, "max_tokens": 4000}
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    txt = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    usage = data.get("usage", {})
    return txt, usage

def parse_json_array(txt: str):
    txt = re.sub(r"```(?:json)?|```", "", txt).strip()
    m = re.search(r"\[.*\]", txt, re.S)
    if not m:
        raise ValueError("no json array")
    return json.loads(m.group(0))

def tag_video(provider, env, video_key, shots, frames_dir: Path, label: str):
    """给一个视频的所有镜头打标，返回 shots（已附 tag 字段）。"""
    todo = [(idx, s) for idx, s in enumerate(shots) if "tag" not in s]
    if not todo:
        return shots
    # 分批，每批 6 帧
    for batch_start in range(0, len(todo), 6):
        batch = todo[batch_start:batch_start + 6]
        images, mapping_parts = [], []
        for k, (idx, s) in enumerate(batch, 1):
            fp = frames_dir / f"shot_{idx:02d}.jpg"
            if not fp.exists():
                mid = (s["start"] + s["end"]) / 2
                if not extract_frame(video_key, mid, fp):
                    s["tag"] = {"desc": "抽帧失败", "usage": ["other"]}
                    continue
            images.append(fp)
            mapping_parts.append(f"图{k}=镜头{idx}（{s['start']:.1f}-{s['end']:.1f}s）")
        if not images:
            continue
        mapping = "；".join(mapping_parts)
        ok = False
        for attempt in range(3):
            try:
                txt, _usage = call_vlm(provider, env, images, mapping, label)
                if not txt.strip():
                    raise ValueError("empty content")
                arr = parse_json_array(txt)
                by_i = {o.get("i"): o for o in arr if isinstance(o, dict)}
                for k, (idx, s) in enumerate(batch, 1):
                    o = by_i.get(idx) or by_i.get(k) or {}
                    if o:
                        s["tag"] = {f: o.get(f, "") if f != "usage" else o.get(f, ["other"])
                                    for f in ("desc", "subject", "shot_size", "scene", "text", "usage")}
                ok = True
                break
            except Exception as e:
                log(f"    重试 {attempt+1}/3（{video_key.name} 批{batch_start//6+1}）: {e}")
                time.sleep(2 * (attempt + 1))
        if not ok:
            for _, s in batch:
                s.setdefault("tag", {"desc": "打标失败", "usage": ["other"]})
    return shots

# ---------- 主流程 ----------
def process_video(args, provider, env, vpath: Path, rel: str, index: dict, force=False):
    """返回 (status, entry)；entry 为 None 表示跳过。不在工作线程里改 index，避免并发写坏 JSON。"""
    entry = index["videos"].get(rel)
    src_mtime = int(vpath.stat().st_mtime)
    if entry and not force and entry.get("src_mtime") == src_mtime \
            and entry.get("status") == "done" and args.notag is False:
        return "skip", None
    if entry is None or force:
        info = probe_video(vpath)
        if info is None:
            return "fail", {"status": "probe_failed", "src_mtime": src_mtime}
        entry = {"manifest": info, "category": str(Path(rel).parent), "src_mtime": src_mtime,
                 "status": "probed", "shots": []}
    if "shots" not in entry or not entry["shots"] or force:
        entry["shots"] = detect_shots(vpath, entry["manifest"]["duration"],
                                      long_clip=args.long_clip)
        for s in entry["shots"]:
            s.pop("tag", None)
    if args.no_vlm:
        entry["status"] = "shotted"
    else:
        frames_dir = args.out / "frames" / vpath.stem
        entry["shots"] = tag_video(provider, env, vpath, entry["shots"], frames_dir, args.category_label)
        entry["status"] = "done"
    entry["src_mtime"] = src_mtime
    return "ok", entry

def render_ledger(index: dict, out: Path, label: str):
    from collections import Counter
    lines = [f"# {label} 素材台账", "",
             f"> 生成时间 {time.strftime('%Y-%m-%d %H:%M')} · 机器可读版：`asset_index.json` · "
             "本文件是人读视图，改数据请改 JSON。", ""]
    usage_counter = Counter()
    cats = {}
    for rel, e in sorted(index["videos"].items()):
        cats.setdefault(e["category"], []).append((rel, e))
    for cat, items in sorted(cats.items()):
        tot = sum(e["manifest"]["duration"] for e, in [(x[1],) for x in items])
        lines += [f"## {cat}（{len(items)} 个 · {tot/60:.1f} 分钟）", "",
                  "| 文件 | 时长 | 镜头 | 首镜头描述 | 景别 | 用途 |",
                  "|---|---|---|---|---|---|"]
        for rel, e in items:
            m = e["manifest"]
            shots = e.get("shots", [])
            first = shots[0].get("tag", {}) if shots else {}
            us = sorted({u for s in shots for u in s.get("tag", {}).get("usage", [])})
            usage_counter.update(us)
            lines.append(f"| {Path(rel).name} | {m['duration']:.1f}s | {len(shots)} | "
                         f"{first.get('desc','—')} | {first.get('shot_size','—')} | {'/'.join(us) or '—'} |")
        lines.append("")
    lines += ["## 用途标签统计", "", "| 标签 | 镜头数 |", "|---|---|"]
    for u, c in usage_counter.most_common():
        lines.append(f"| {u} | {c} |")
    (out / "素材台账.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="", help="素材根目录；不传则读 .env 的 ASSET_ROOT")
    ap.add_argument("--categories", required=True, help="逗号分隔，如 '桌拍素材/4.10,磨酱'")
    ap.add_argument("--out", required=True)
    ap.add_argument("--category-label", default="素材库")
    ap.add_argument("--provider", default="", choices=["", "mimo", "zhipu"])
    ap.add_argument("--only", default="", help="子串过滤文件名")
    ap.add_argument("--no-vlm", action="store_true")
    ap.add_argument("--notag", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--workers", type=int, default=2, help="视频级并发，4 核机器建议 2-3")
    ap.add_argument("--long-clip", type=float, default=30.0,
                    help="超过该秒数视为长镜头，跳过全片场景检测、改等间隔采样")
    ap.add_argument("--limit", type=int, default=0, help="本次最多处理多少个（分片执行用）")
    args = ap.parse_args()
    args.notag = args.no_vlm
    env = load_env()
    source = args.source or env.get("ASSET_ROOT", "")
    if not source:
        sys.exit("未指定素材根目录：用 --source 传入，或在 .env 里设 ASSET_ROOT=<本机素材根>")
    src, out = Path(source), Path(args.out)
    args.out = out
    out.mkdir(parents=True, exist_ok=True)

    provider = args.provider
    if not provider:
        provider = "mimo" if env.get("MIMO_API_KEY") else ("zhipu" if env.get("ZHIPU_API_KEY") else "")
    if not args.no_vlm and not provider:
        sys.exit("未找到 MIMO_API_KEY / ZHIPU_API_KEY（检查 .env）")
    if provider:
        log(f"VLM: {provider} / {env.get('MIMO_MODEL', env.get('ZHIPU_MODEL', ''))}")

    idx_path = out / "asset_index.json"
    index = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else \
        {"label": args.category_label, "asset_root": str(src), "videos": {}}
    index.pop("source", None)  # 老字段兼容：统一改叫 asset_root
    index["label"], index["asset_root"] = args.category_label, str(src)

    files = []
    for cat in [c.strip() for c in args.categories.split(",") if c.strip()]:
        d = src / cat
        if not d.exists():
            log(f"警告：目录不存在 {d}")
            continue
        for p in sorted(d.iterdir()):  # 非递归：只取目录直属文件，跳过「被分出去」等子目录
            if p.is_file() and p.suffix.lower() in VIDEO_EXT:
                if args.only and args.only not in p.name:
                    continue
                files.append((str(p.relative_to(src)), p))
    log(f"待处理 {len(files)} 个视频")
    if args.limit:
        files = files[:args.limit]

    stats = {"ok": 0, "skip": 0, "fail": 0}
    t_start = time.time()

    def work(rel, p):
        t0 = time.time()
        try:
            r, entry = process_video(args, provider, env, p, rel, index, force=args.force)
        except Exception as e:
            log(f"  异常 {rel}：{e}")
            return "fail", None, rel, time.time() - t0
        return r, entry, rel, time.time() - t0

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = [ex.submit(work, rel, p) for rel, p in files]
        for i, fut in enumerate(as_completed(futs), 1):
            r, entry, rel, dt = fut.result()
            stats[r] += 1
            if entry is not None:
                index["videos"][rel] = entry
            tag = {"ok": "完成", "skip": "跳过", "fail": "失败"}[r]
            n = len(entry.get("shots", [])) if entry else 0
            log(f"[{i}/{len(files)}] {tag} {rel} {n}镜头 {dt:.1f}s（累计 {time.time()-t_start:.0f}s）")
            idx_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    render_ledger(index, out, args.category_label)
    n_shots = sum(len(e.get("shots", [])) for e in index["videos"].values())
    log(f"完成：{stats} · 索引内 {len(index['videos'])} 个视频 / {n_shots} 镜头 · 耗时 {time.time()-t_start:.0f}s")

if __name__ == "__main__":
    main()
