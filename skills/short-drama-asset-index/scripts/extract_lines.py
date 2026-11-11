"""从官方素材抽「台词时间轴」——短剧素材索引的第 1 道工序。

只读原片，零修改。硬字幕 OCR 取台词，省掉 ASR。
增量续跑：按集级断点，已产出的 ep 自动跳过（--force 可强制重跑）。

用法：
  python extract_lines.py --drama 八零憨妻的甜蜜日常 --all --exclude 20
  python extract_lines.py --drama 以真心守护至亲 --ep 1 2 3
  python extract_lines.py --drama 以真心守护至亲 --all --force

剧名必填（11-11 起）：选片阶段只跑前几集做判断，产物落同一处，
索引阶段再跑 --all 时这几集自动跳过，不重复抽帧。
素材根一律走 .env 的 <LINE>_ASSET_ROOT，不写本机绝对路径。
输出：<out>/lines_epNN.json，每行 {"t": 秒, "text": 台词}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ASSET_ROOT = None
for _line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if _line.startswith("SHORT_DRAMA_ASSET_ROOT="):
        ASSET_ROOT = Path(_line.split("=", 1)[1].strip())
        break
if ASSET_ROOT is None:
    sys.exit("缺少 .env 的 SHORT_DRAMA_ASSET_ROOT")

# 竖屏硬字幕在画面 60%–82% 处（实测 720x1280，字幕基线约 y=900）
CROP_TOP, CROP_BOTTOM = 0.58, 0.84
STEP = 2  # 秒/帧，与既有台词稿同一粒度


def tool(name: str) -> str:
    """ffmpeg/ffprobe 一律按名字找；本机 anaconda 装有但不在 PATH 时兜底。"""
    p = shutil.which(name) or f"/opt/anaconda3/bin/{name}"
    if not Path(p).exists():
        sys.exit(f"找不到 {name}：装一个，或加进 PATH")
    return p

NOISE = re.compile(r"[\|\•\.…。、,，\s0-9oOJlI`'\"]+")


def norm(s: str) -> str:
    return NOISE.sub("", s)


def find_videos(src: Path) -> dict[int, Path]:
    """两种命名都要认：`xxx第12集.mp4` 与 `12.mp4`（官方包/裸切片两种来源）。"""
    out = {}
    for p in sorted(src.glob("*.mp4")):
        m = re.search(r"第(\d+)集", p.name) or re.fullmatch(r"(\d+)", p.stem)
        if m:
            out[int(m.group(1))] = p
    return out


def duration(video: Path) -> float:
    r = subprocess.run(
        [tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def ocr_one(engine, frame: Path, crop_top: float, crop_bottom: float) -> str:
    import cv2
    img = cv2.imread(str(frame))
    if img is None:
        return ""
    h, w = img.shape[:2]
    crop = img[int(h * crop_top):int(h * crop_bottom), :]
    crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    res, _ = engine(crop)
    if not res:
        return ""
    parts = []
    for box, txt, conf in res:
        if conf is not None and float(conf) < 0.45:
            continue
        t = (txt or "").strip()
        if len(norm(t)) >= 2:
            parts.append(t)
    return " ".join(parts)


def extract_ep(ep: int, video: Path, engine, crop=(CROP_TOP, CROP_BOTTOM), step=STEP) -> dict:
    dur = duration(video)
    with tempfile.TemporaryDirectory(prefix=f"sd_ep{ep:02d}_") as td:
        pattern = str(Path(td) / "f_%04d.jpg")
        subprocess.run(
            [tool("ffmpeg"), "-v", "error", "-i", str(video),
             "-vf", f"fps=1/{step}", "-q:v", "3", pattern],
            check=True,
        )
        frames = sorted(Path(td).glob("f_*.jpg"))
        raw = []
        for i, f in enumerate(frames):
            txt = ocr_one(engine, f, crop[0], crop[1])
            raw.append({"t": i * step, "text": txt})

    # 相邻帧同一句字幕去重：保留首次出现，时间取起点
    lines = []
    prev = ""
    for row in raw:
        cur = norm(row["text"])
        if not cur:
            prev = ""
            continue
        if prev and SequenceMatcher(None, prev, cur).ratio() >= 0.72:
            continue
        lines.append({"t": row["t"], "text": row["text"]})
        prev = cur

    return {
        "ep": ep,
        "file": video.name,
        "duration": round(dur, 2),
        "step": step,
        "frame_count": len(frames),
        "line_count": len(lines),
        "lines": lines,
    }


# ---------- 并行：每集一个任务，进程级隔离 ----------
# 一个 OCR 引擎实例不能被多线程安全复用，且它是 CPU 密集，所以用进程池而不是线程池。
# 每个子进程自己加载一次引擎（initializer 里做，只付一次加载成本），
# 并把 onnxruntime 的线程数压到 1——否则 4 进程 × 多线程会互相抢核，反而更慢。
_W = {}


def _init_worker():
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["ORT_NUM_THREADS"] = "1"
    from rapidocr_onnxruntime import RapidOCR
    _W["engine"] = RapidOCR()


def _work_one(task):
    ep, video, crop, step = task
    return extract_ep(ep, video, _W["engine"], crop, step)


def _save(out_dir: Path, data: dict) -> Path:
    dst = out_dir / f"lines_ep{data['ep']:02d}.json"
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drama", required=True,
                    help="剧名＝素材根下的目录名，也是产物目录名，如 以真心守护至亲")
    ap.add_argument("--jobs", type=int, default=4,
                    help="并行集数（默认 4）。每集独立，产物按集落盘，结果与串行完全一致")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--ep", type=int, nargs="*", default=[])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--exclude", type=int, nargs="*", default=[],
                    help="要排除的集。大结局不许入成片的剧，用 --exclude 20 显式指定；"
                         "不再默认排除，否则新剧会被悄悄少跑一集")
    ap.add_argument("--crop-top", type=float, default=CROP_TOP,
                    help="硬字幕区上边界（占画面高度比例），默认 0.58")
    ap.add_argument("--crop-bottom", type=float, default=CROP_BOTTOM,
                    help="硬字幕区下边界（占画面高度比例），默认 0.84")
    ap.add_argument("--step", type=float, default=STEP,
                    help="抽帧间隔（秒）。字幕卡片常 1–2 秒一换，2 秒会漏卡，"
                         "要切台词边界请用 1；已存文件间隔不一致会自动重跑")
    args = ap.parse_args()

    src = ASSET_ROOT / args.drama
    if not src.exists():
        sys.exit(f"素材目录不存在：{src}")
    out_dir = ROOT / "assets" / "short-drama" / args.drama / "lines"

    videos = find_videos(src)
    if not videos:
        sys.exit(f"{src} 下没有认出逐集 mp4（认 `第N集` 与 `N.mp4` 两种命名）")
    eps = sorted(videos) if args.all else sorted(set(args.ep))
    eps = [e for e in eps if e not in args.exclude]
    if not eps:
        sys.exit("没有指定集数")

    out_dir.mkdir(parents=True, exist_ok=True)

    # 先定哪些集真要跑（间隔不一致的旧文件算「重跑」），再统一派活
    todo, skipped, redone = [], [], []
    for ep in eps:
        dst = out_dir / f"lines_ep{ep:02d}.json"
        if dst.exists() and not args.force:
            try:
                old_step = json.loads(dst.read_text(encoding="utf-8")).get("step")
            except Exception:
                old_step = None
            if old_step == args.step:
                skipped.append(ep)
                continue
            redone.append(ep)  # 旧文件抽帧间隔不同，数据偏稀，重跑
        todo.append(ep)

    crop = (args.crop_top, args.crop_bottom)
    jobs = max(1, min(args.jobs, len(todo) or 1))
    done = []
    if todo:
        if jobs == 1:
            from rapidocr_onnxruntime import RapidOCR
            engine = RapidOCR()
            for ep in todo:
                data = extract_ep(ep, videos[ep], engine, crop, args.step)
                dst = _save(out_dir, data)
                done.append(ep)
                print(f"ep{ep:02d}  {data['duration']:>7.1f}s  {data['line_count']:>3d} 句"
                      f"  -> {dst.name}", flush=True)
        else:
            from concurrent.futures import ProcessPoolExecutor, as_completed
            print(f"并行 {jobs} 路，共 {len(todo)} 集", flush=True)
            with ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker) as ex:
                futs = {ex.submit(_work_one, (ep, videos[ep], crop, args.step)): ep
                        for ep in todo}
                for fut in as_completed(futs):
                    ep = futs[fut]
                    try:
                        data = fut.result()
                    except Exception as e:
                        print(f"ep{ep:02d} 失败：{e}", flush=True)
                        continue
                    dst = _save(out_dir, data)
                    done.append(ep)
                    print(f"ep{ep:02d}  {data['duration']:>7.1f}s  {data['line_count']:>3d} 句"
                          f"  -> {dst.name}  （已完成 {len(done)}/{len(todo)}）", flush=True)

    print(f"完成 {len(done)} 集，跳过 {len(skipped)} 集（间隔一致）"
          + (f"，重跑 {len(redone)} 集（旧文件间隔不同：{sorted(redone)}）" if redone else ""))
    print(f"产物目录：{out_dir}")
    if done:
        _rebuild_sqlite(args.drama)


def _rebuild_sqlite(drama: str) -> None:
    """JSON 台词库变更后重建派生缓存 index.sqlite（11-11 定：有变更即更新，缓存可随时全量重建）。"""
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
