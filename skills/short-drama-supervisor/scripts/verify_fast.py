# -*- coding: utf-8 -*-
"""快速逐段音画对齐校验（11-17）。

判定口径与 verify_strict 一致，但快 ~11 倍：
  · 音频：成片该段（掐头去尾各 0.5s）vs 原片整段，FFT 互相关求滞后
  · 画面：成片段中点帧 vs 原片 a-2..a+2 每 0.25s 一帧，取 MAE 最小者
旧版每段起 17 个 ffmpeg 进程（29 段 493 次，14m45s）；新版每段 1 个进程用 fps
滤镜一次吐 17 帧、段间线程并行，约 1 分钟。

通用化（相对工作区 avsync 版的两处改动）：
  · ffmpeg 走 _env.ffmpeg()（数据目录 bin 优先，不写死裸命令）
  · 源片目录从 plan.src_dir 读（跟装配器同源），不写死剧名映射
  · 段间转场（out=black/card）在成片里占时间，计算段起点时把转场时长跳过去

用法：
    python verify_fast.py <plan.json> <成片.mp4> [--jobs 6]
"""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import numpy as np
except ImportError:
    sys.exit("需要 numpy：pip install numpy")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: E402

FF = _env.ffmpeg()
SR = 16000
FW, FH = 96, 170          # 比对用的极小帧尺寸
WIN = 2.0                 # 中点帧前后各搜 2 秒
STEP = 0.25               # 搜索步长
NULL = "NUL" if os.name == "nt" else "/dev/null"


def audio(path, a, b):
    cmd = [FF, "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", path,
           "-map", "0:a:0", "-ar", str(SR), "-ac", "1", "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def xlag(win, ref):
    """win 相对 ref 的滞后秒（正 = win 比 ref 晚）"""
    x = win - win.mean()
    y = ref - ref.mean()
    n = len(x) + len(y) - 1
    N = 1 << (n - 1).bit_length()
    c = np.fft.irfft(np.fft.rfft(y, N) * np.conj(np.fft.rfft(x, N)), N)
    c /= (np.linalg.norm(x) * np.linalg.norm(y) + 1e-9)
    k = int(np.argmax(c))
    if k > N // 2:
        k -= N
    return k / SR, float(c[k])


def one_frame(path, t):
    """抽一帧并缩成 FW×FH 灰度"""
    cmd = [FF, "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path,
           "-frames:v", "1", "-vf", f"scale={FW}:{FH}", "-pix_fmt", "gray",
           "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    if len(raw) < FW * FH:
        return None
    return np.frombuffer(raw[:FW * FH], dtype=np.uint8).reshape(FH, FW).astype(np.float32)


def frame_strip(path, start, span):
    """一次抽出 start 起 span 秒内的全部帧（fps=1/STEP），返回 (n, FH, FW) 灰度数组"""
    cmd = [FF, "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{span:.3f}",
           "-i", path, "-vf", f"fps={1 / STEP:.4f},scale={FW}:{FH}",
           "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    n = len(raw) // (FW * FH)
    if n == 0:
        return np.zeros((0, FH, FW), dtype=np.float32)
    return np.frombuffer(raw[:n * FW * FH], dtype=np.uint8).reshape(n, FH, FW).astype(np.float32)


def check_segment(args):
    idx, seg, t0, built, src_dir = args
    d = seg["b"] - seg["a"]
    # 音频：成片段（掐头去尾 0.5s）与原片段做全段互相关
    m = audio(built, t0 + 0.5, t0 + d - 0.5)
    ref = audio(str(Path(src_dir) / f"{seg['ep']}.mp4"), seg["a"], seg["b"])
    n = min(len(m), len(ref))
    if n < SR // 2:
        return idx, seg, d, None, None, None
    lag, peak = xlag(m[:n], ref[:n])
    aoff = lag - 0.5
    # 画面：成片中点帧 vs 原片逐 0.25s 帧条
    tm = t0 + d / 2
    fb = one_frame(built, tm)
    exp = seg["a"] + d / 2
    strip = frame_strip(str(Path(src_dir) / f"{seg['ep']}.mp4"), max(0.0, exp - WIN), WIN * 2 + STEP)
    if fb is None or len(strip) == 0:
        return idx, seg, d, aoff, peak, None
    maes = np.mean(np.abs(strip - fb), axis=(1, 2))
    k = int(np.argmin(maes))
    voff = (max(0.0, exp - WIN) + k * STEP) - exp
    return idx, seg, d, aoff, peak, (voff, float(maes[k]))


def resolve_src_dir(plan, plan_path):
    """源片目录：plan.src_dir → 环境变量 SHORT_DRAMA_SRC_DIR，展开 ~ 和环境变量。"""
    src = os.environ.get("SHORT_DRAMA_SRC_DIR") or plan.get("src_dir")
    if not src:
        sys.exit("清单里没有 src_dir，也没设 SHORT_DRAMA_SRC_DIR，无法定位原片")
    src = os.path.expandvars(os.path.expanduser(str(src)))
    # 相对路径按清单所在目录解析
    if not os.path.isabs(src):
        src = str((Path(plan_path).resolve().parent / src).resolve())
    return src


def main(plan_path, built_path, jobs=6):
    plan = json.loads(open(plan_path, encoding="utf-8").read())
    src_dir = resolve_src_dir(plan, plan_path)
    TR = plan.get("transitions") or {}
    black_d = float((TR.get("black") or {}).get("d", 0.45))
    card_d = float((TR.get("card") or {}).get("d", 1.2))

    # 成片时间轴上逐段累加起点：ep 段加自身时长，转场段加转场时长（跳过去不校验）
    starts, t = [], 0.0
    tasks = []
    for i, s in enumerate(plan["segs"]):
        typ = s.get("type", "ep")
        if typ == "ep":
            d = s["b"] - s["a"]
            starts.append(t)
            tasks.append((len(tasks), s, t, built_path, src_dir))
            t += d
        else:
            d = s["d"]
        # out 写在段上，表示该段结束后插入的转场，成片里占位
        out = s.get("out") or "cut"
        if out == "black":
            t += black_d
        elif out == "card":
            t += card_d
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        rows = list(ex.map(check_segment, tasks))
    print("段 | 集 | 段长 | 音频滞后 | 峰值 | 画面偏差")
    worst = 0.0
    for idx, seg, d, aoff, peak, pic in sorted(rows, key=lambda r: r[0]):
        voff, mae = pic if pic else (None, None)
        worst = max(worst, abs(aoff or 0), abs(voff or 0))
        print(f"{idx + 1} | ep{seg['ep']:<2} | {d:5.1f}s | "
              f"{aoff:+.3f}s | {peak:.2f} | "
              f"{voff:+.2f}s (MAE {mae:.0f})" if pic else
              f"{idx + 1} | ep{seg['ep']:<2} | {d:5.1f}s | {aoff:+.3f}s | {peak:.2f} | 抽帧失败")
    print(f"最大音画差: {worst:+.2f}s {'✓ 同步' if worst <= 0.3 else '✗ 需人工复核（低对比画面可能锁错峰，抽帧核对）'}")


if __name__ == "__main__":
    jobs = 6
    if "--jobs" in sys.argv:
        jobs = int(sys.argv[sys.argv.index("--jobs") + 1])
        sys.argv = [a for a in sys.argv if a != "--jobs" and a != str(jobs)]
    main(sys.argv[1], sys.argv[2], jobs)
