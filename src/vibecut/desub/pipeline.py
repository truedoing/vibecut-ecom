"""单片段去字幕管线（两遍扫描）。

Pass 1  逐帧 OCR / 检测 → 跨帧 track → 判定字幕轨迹
Pass 2  重新读帧 → 字幕轨迹生成字形 mask → LaMa 修复 → 编码

输出：
  original.mp4 / result.mp4 / mask_overlay.mp4 / report.json / contact_sheet.jpg

`run_file()` 接受任意视频文件 + 起止秒，不依赖项目清单；
CLI 的 `check` 走 analyze_only，`run` 才做修复。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

from . import classify, mask as maskmod, ocr, qa, track
from .inpaint import Inpainter


def _ffmpeg() -> str:
    # 延迟导入，避免 desub 包在缺少 ffmpeg 时 import 即失败
    from ..core.media import ffmpeg_bin
    return ffmpeg_bin()


# ── ffmpeg rawvideo 写入 ──
class RawWriter:
    def __init__(self, path: Path, W: int, H: int, fps: float):
        self.path = str(path)
        cmd = [
            _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{W}x{H}", "-r", f"{fps:.6f}", "-i", "-",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", self.path,
        ]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def write(self, frame: np.ndarray):
        self.p.stdin.write(np.ascontiguousarray(frame).tobytes())

    def close(self):
        if self.p.stdin:
            self.p.stdin.close()
        self.p.wait()


def mux_audio(src: str, silent: str, out: str, start: float, dur: float):
    cmd = [
        _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src,
        "-i", silent,
        "-map", "0:a:0?", "-map", "1:v:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", out,
    ]
    subprocess.run(cmd, check=True)


def _grab(video: str, t: float):
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(t * fps))))
    ok, fr = cap.read()
    cap.release()
    return fr if ok else None


def make_contact_sheet(original: str, overlay: str, result: str,
                       dur: float, out: Path, cols=(0.0, 0.5, None)):
    times = [0.0, dur / 2, max(0.0, dur - 0.15)] if cols[2] is None else list(cols)
    rows = []
    for kind, path in (("ORIGINAL", original), ("MASK", overlay), ("RESULT", result)):
        row = []
        for t in times:
            fr = _grab(path, t)
            if fr is None:
                fr = np.zeros((960, 540, 3), np.uint8)
            fr = cv2.resize(fr, (360, 640))
            cv2.rectangle(fr, (0, 0), (360, 26), (0, 0, 0), -1)
            cv2.putText(fr, f"{kind}  {t:.1f}s", (8, 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
            row.append(fr)
        rows.append(np.hstack(row))
    sheet = np.vstack(rows)
    cv2.imwrite(str(out), sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])


def run_file(src: str | Path, start: float, end: float, out_dir: Path, *,
             sample_every: int = 6, mask_pad: int = 4,
             backend: str = "auto", det_only: bool = False,
             analyze_only: bool = False, verbose: bool = True,
             label: str = "") -> dict:
    t0 = time.time()
    src = Path(src).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"视频不存在: {src}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    duration = max(0.1, end - start)
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    start_f = int(round(start * fps))
    end_f = int(round(end * fps))
    n = max(1, end_f - start_f)

    # ── Pass 1: OCR 采样 + 过渡帧检测 ──
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
    frames: dict[int, dict] = {}
    samples: list[dict] = []
    sample_ord = 0
    for off in range(n):
        ok, frame = cap.read()
        if not ok:
            break
        if off % sample_every == 0:
            dets = ocr.detect(frame)
            blocks = classify.annotate_frame(frame, dets)
            samples.append({"si": sample_ord, "off": off, "t": off / fps, "blocks": blocks})
            frames[off] = {"dets": dets, "blocks": blocks, "sample_ord": sample_ord}
            if verbose:
                subs = [b for b in blocks if b["label"] == "subtitle"]
                if subs:
                    print(f"  [p1] {off / fps:5.2f}s OCR {len(dets):2d}框 "
                          f"字幕{len(subs)}: " + " / ".join(b.get("text", "") for b in subs))
            sample_ord += 1
        else:
            dets = ocr.detect_boxes(frame) if det_only else []
            frames[off] = {"dets": dets, "blocks": None, "sample_ord": None}
    cap.release()

    tracks = track.build_tracks(samples, W, H)
    sub_tracks = [tr for tr in tracks if tr["label"] == "subtitle"]
    bands = track.subtitle_track_bands(tracks, W, H)
    subtitle_tids = {tr["tid"] for tr in sub_tracks}

    def is_sub_block(b: dict) -> bool:
        """单框是字幕，或落在已判定为字幕的轨迹里且样式仍像字幕。"""
        if b.get("label") == "subtitle":
            return True
        return (
            b.get("tid") in subtitle_tids
            and b.get("stroke", 0) >= 0.22
            and b.get("white_ratio", 0) >= 0.03
            and not classify.packaging_like(b.get("text") or "")
        )

    removed_texts = sorted({
        b.get("text", "") for s in samples for b in s["blocks"]
        if is_sub_block(b) and (b.get("text") or "").strip()
    })

    # ── 风险检测：字幕压在包装文字上时，修复必然要「猜」被遮挡的包装内容 ──
    conflicts = {}
    for s in samples:
        blocks = s["blocks"]
        subs = [b for b in blocks if is_sub_block(b)]
        pkgs = [b for b in blocks if b.get("label") == "packaging"]
        for sb in subs:
            for pb in pkgs:
                ix1 = max(sb["x1"], pb["x1"])
                iy1 = max(sb["y1"], pb["y1"])
                ix2 = min(sb["x2"], pb["x2"])
                iy2 = min(sb["y2"], pb["y2"])
                inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
                frac = inter / max((sb["x2"] - sb["x1"]) * (sb["y2"] - sb["y1"]), 1.0)
                pkg_center_inside = (
                    sb["x1"] - 5 <= pb["cx"] <= sb["x2"] + 5
                    and sb["y1"] - 5 <= pb["cy"] <= sb["y2"] + 5
                )
                if frac > 0.05 or pkg_center_inside:
                    key = (sb.get("text", ""), pb.get("text", ""))
                    rec = conflicts.get(key)
                    val = {"t": round(s["t"], 2), "subtitle": sb.get("text", ""),
                           "packaging": pb.get("text", ""),
                           "overlap": round(frac, 3),
                           "packaging_center_inside": pkg_center_inside}
                    if rec is None or val["overlap"] > rec["overlap"]:
                        conflicts[key] = val
    conflict_list = sorted(conflicts.values(), key=lambda x: -x["overlap"])
    risk = "high" if conflict_list else "low"
    packaging_texts = sorted({
        b.get("text", "") for s in samples for b in s["blocks"]
        if b.get("label") == "packaging"
        and len((b.get("text") or "").strip()) >= 3
        and (b.get("conf") or 0) >= 0.5
    })
    if verbose:
        print(f"  [p1] tracks={len(tracks)} subtitle_tracks={len(sub_tracks)} "
              f"risk={risk} conflicts={len(conflict_list)} ({time.time() - t0:.0f}s)")

    base_report = {
        "source": str(src), "label": label, "out_dir": str(out_dir),
        "start_sec": start, "end_sec": end,
        "duration_sec": round(duration, 3), "fps": fps,
        "width": W, "height": H, "frames": n, "sample_every": sample_every,
        "subtitle_tracks": [
            {k: tr[k] for k in ("tid", "label", "label_reason", "n", "stroke_med",
                                "h_ratio_med", "horizontal_frac", "centered_frac",
                                "unique_texts", "texts", "labels")}
            for tr in sub_tracks
        ],
        "all_track_labels": {tr["tid"]: tr["label"] for tr in tracks},
        "removed_texts": removed_texts,
        "packaging_texts": packaging_texts,
        "risk": risk,
        "conflicts": conflict_list[:20],
    }

    if analyze_only:
        report = {**base_report, "analysis_only": True,
                  "elapsed_sec": round(time.time() - t0, 1), "ok": True}
        (out_dir / "analysis.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    # ── 原始片段（音画都保留）──
    original = out_dir / "original.mp4"
    silent = out_dir / "result_silent.mp4"
    result = out_dir / "result.mp4"
    overlay = out_dir / "mask_overlay.mp4"
    subprocess.run([
        _ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", str(original),
    ], check=True)

    # ── Pass 2: mask + inpaint ──
    inpainter = Inpainter(backend=backend)
    wr = RawWriter(silent, W, H, fps)
    ov = RawWriter(overlay, W, H, fps)
    cap = cv2.VideoCapture(str(src))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
    mask_px = 0
    masked_frames = 0
    sample_sub_blocks: dict[int, list[dict]] = {
        s["si"]: [b for b in s["blocks"] if b["label"] == "subtitle"] for s in samples
    }
    ord_to_off = {s["si"]: s["off"] for s in samples}

    try:
        for off in range(n):
            ok, frame = cap.read()
            if not ok:
                break
            info = frames.get(off) or {}
            keep_boxes = []
            sub_boxes = []

            if info.get("blocks") is not None:
                blocks = info["blocks"]
                sub_boxes = [b for b in blocks if is_sub_block(b)]
                keep_boxes = [b for b in blocks if not is_sub_block(b)]
            else:
                dets = info.get("dets") or []
                for d in dets:
                    feat = classify.stroke_features(frame, d)
                    d.update(feat)
                    matched = (
                        d.get("stroke", 0) >= 0.22
                        and d.get("white_ratio", 0) >= 0.03
                        and any(track.box_in_band(d, b, W, H) for b in bands)
                    )
                    if matched:
                        sub_boxes.append(d)
                    else:
                        keep_boxes.append(d)
                if not sub_boxes and info.get("sample_ord") is None and ord_to_off:
                    nearest = min(ord_to_off, key=lambda k: abs(ord_to_off[k] - off))
                    if abs(ord_to_off[nearest] - off) <= sample_every:
                        sub_boxes = sample_sub_blocks.get(nearest, [])

            if sub_boxes:
                m = maskmod.combine(frame, sub_boxes, pad=mask_pad)
                if m.any():
                    out_frame = inpainter(frame, m)
                    mask_px += int((m > 0).sum())
                    masked_frames += 1
                else:
                    out_frame = frame
                ovf = maskmod.draw_overlay(frame, m, keep_boxes, sub_boxes)
            else:
                out_frame = frame
                m = np.zeros((H, W), np.uint8)
                ovf = maskmod.draw_overlay(frame, m, keep_boxes, sub_boxes)

            wr.write(out_frame)
            ov.write(ovf)
            if verbose and off % 30 == 0:
                print(f"  [p2] {off}/{n} frames  masked={masked_frames}")
    finally:
        cap.release()
        wr.close()
        ov.close()

    mux_audio(str(src), str(silent), str(result), start, duration)

    # ── 质检 ──
    check_times = [0.2, duration * 0.35, duration * 0.65, max(0.2, duration - 0.3)]
    qa_res = qa.check(str(result), check_times, removed_texts, packaging_texts)

    make_contact_sheet(str(original), str(overlay), str(result), duration,
                       out_dir / "contact_sheet.jpg")

    report = {
        **base_report,
        "mask_pad": mask_pad,
        "backend": inpainter.backend,
        "elapsed_sec": round(time.time() - t0, 1),
        "masked_frames": masked_frames,
        "mask_pixels": mask_px,
        "qa": qa_res,
        "files": {
            "original": str(original), "result": str(result),
            "mask_overlay": str(overlay),
            "contact_sheet": str(out_dir / "contact_sheet.jpg"),
        },
        "ok": qa_res["pass"],
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if verbose:
        print(f"  [done] {time.time() - t0:.0f}s  masked_frames={masked_frames}  "
              f"QA={'PASS' if qa_res['pass'] else 'CHECK'}  "
              f"residual={qa_res['subtitle_residual_count']} "
              f"pkg_missing={qa_res['packaging_missing_count']}  "
              f"risk={risk} conflicts={len(conflict_list)}")
    return report
