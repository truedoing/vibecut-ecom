# -*- coding: utf-8 -*-
"""
公共工具：配置、音频切片、声纹模型。
跨平台：Mac / Windows / Linux 通用。路径全部用 pathlib，中文显式 UTF-8。
"""
import os
import json
import tempfile
import subprocess
from pathlib import Path

# hf-mirror 必须在 import speechbrain 之前设置，模型下载才走国内镜像
import config as CFG

os.environ.setdefault("HF_ENDPOINT", CFG.HF_ENDPOINT)
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

ROOT = Path(__file__).resolve().parent
SOURCE_DIR = Path(CFG.SOURCE_DIR)
LINES_DIR = Path(CFG.LINES_DIR)
OUT_DIR = ROOT / "output"
DATA_DIR = ROOT / "data"
MODEL_DIR = ROOT / "models" / "spkrec-ecapa-voxceleb"
SAMPLES_DIR = ROOT / "samples"
TMP_DIR = Path(tempfile.gettempdir()) / "speaker_label_tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)

for d in (OUT_DIR, DATA_DIR, MODEL_DIR, SAMPLES_DIR):
    d.mkdir(parents=True, exist_ok=True)


def ffmpeg_bin():
    """优先用 imageio-ffmpeg 自带的二进制（跨平台，免系统安装），找不到再用系统 ffmpeg。"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def source_video(ep):
    """原片命名容错：1.mp4 / 01.mp4 都试。"""
    for name in (f"{ep}.mp4", f"{ep:02d}.mp4"):
        p = SOURCE_DIR / name
        if p.exists():
            return p
    raise FileNotFoundError(f"找不到第{ep}集原片：{SOURCE_DIR} / {ep}.mp4")


def lines_json(ep):
    for name in (f"lines_ep{ep:02d}.json", f"lines_ep{ep}.json"):
        p = LINES_DIR / name
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"找不到第{ep}集台词库：{LINES_DIR} / lines_ep{ep:02d}.json")


def sentences_json(ep):
    p = LINES_DIR.parent / "sentences" / f"sentences_ep{ep:02d}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def line_window(row, clip=None, next_t=None):
    """取一句台词的实际发声窗口。
    有 shot 用 shot；没有则用本句起点到下一句起点之间，取中间段，避免切到相邻句。
    """
    clip = clip or CFG.CLIP_SECONDS
    t = float(row["t"])
    shot = row.get("shot")
    if shot:
        s, e = float(shot[0]), float(shot[1])
    else:
        # lines 文件只有起点：窗口 = 本句起点 到 下一句起点之间
        gap = (float(next_t) - t) if next_t is not None else clip + 0.6
        gap = min(max(gap, 1.0), clip + 0.6)
        s = t + 0.1
        e = t + gap - 0.1
    s = max(s, t - 0.15)
    e = min(e, s + clip)
    if e - s < 0.8:
        e = s + 0.8
    return s, e


def slice_wav(ep, start, end, out_path):
    """用 ffmpeg 从原片切一段 16k 单声道 wav。start/end 为秒。"""
    src = source_video(ep)
    cmd = [
        ffmpeg_bin(), "-y", "-v", "error",
        "-ss", f"{start:.3f}", "-i", str(src),
        "-t", f"{end - start:.3f}",
        "-vn", "-ac", "1", "-ar", str(CFG.SAMPLE_RATE),
        "-c:a", "pcm_s16le", str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def load_classifier():
    """加载 ECAPA 声纹模型（本地有就用本地，没有自动走 hf-mirror 下载）。"""
    from speechbrain.inference.speaker import EncoderClassifier
    return EncoderClassifier.from_hparams(
        source=CFG.MODEL_SOURCE,
        savedir=str(MODEL_DIR),
    )


def emb_of_wav(classifier, wav_path):
    """提一条 wav 的声纹向量，返回一维 list[float]。"""
    import torch
    import torchaudio
    sig, sr = torchaudio.load(str(wav_path))
    if sr != CFG.SAMPLE_RATE:
        sig = torchaudio.functional.resample(sig, sr, CFG.SAMPLE_RATE)
    # 极短片段补静音，ECAPA 太短会不稳
    min_len = int(CFG.SAMPLE_RATE * 0.8)
    if sig.shape[1] < min_len:
        sig = torch.nn.functional.pad(sig, (0, min_len - sig.shape[1]))
    with torch.no_grad():
        emb = classifier.encode_batch(sig)
    return emb.squeeze().cpu().numpy().tolist()


def cosine(a, b):
    import numpy as np
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    n = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / n) if n else 0.0
