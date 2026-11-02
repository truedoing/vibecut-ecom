"""视频修复后端。

onnx: LaMa ONNX（512×512 固定输入，onnxruntime CPU，推荐）
lama: SimpleLama TorchScript（需要 big-lama.pt）
cv2 : cv2.inpaint(Telea)，无模型时的快速兜底
"""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np

DEFAULT_ONNX = Path.home() / ".cache" / "vibecut" / "lama_fp32.onnx"


class Inpainter:
    def __init__(self, backend: str = "auto", threads: int = 4):
        self.backend = backend
        self.lama = None
        self.sess = None
        self.onnx_path = Path(os.environ.get("LAMA_ONNX_MODEL", str(DEFAULT_ONNX)))

        if backend in ("auto", "onnx") and self.onnx_path.exists():
            try:
                import onnxruntime as ort
                so = ort.SessionOptions()
                so.intra_op_num_threads = max(1, threads)
                self.sess = ort.InferenceSession(
                    str(self.onnx_path), sess_options=so,
                    providers=["CPUExecutionProvider"])
                self.backend = "onnx"
                print(f"[inpaint] LaMa ONNX 已加载: {self.onnx_path}")
                return
            except Exception as e:
                print(f"[inpaint] ONNX 加载失败: {e}")
                if backend == "onnx":
                    self.backend = "cv2"
                    return

        if backend in ("auto", "lama"):
            try:
                import torch
                torch.set_num_threads(max(1, threads))
                from simple_lama_inpainting import SimpleLama
                self.lama = SimpleLama()
                self.backend = "lama"
                print("[inpaint] LaMa TorchScript 已加载")
                return
            except Exception as e:
                print(f"[inpaint] LaMa 不可用，退回 cv2.inpaint: {e}")
        self.backend = "cv2"

    # ── ONNX 512×512（直接缩放；实测比补成正方形效果更好）──
    def _onnx_inpaint(self, crop: np.ndarray, cmask: np.ndarray) -> np.ndarray:
        h, w = crop.shape[:2]
        img512 = cv2.resize(crop, (512, 512), interpolation=cv2.INTER_LINEAR)
        m512 = cv2.resize(cmask, (512, 512), interpolation=cv2.INTER_NEAREST)

        rgb = cv2.cvtColor(img512, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        inp = rgb.transpose(2, 0, 1)[None]
        msk = (m512 > 0).astype(np.float32)[None, None]

        names = [i.name for i in self.sess.get_inputs()]
        feed = {names[0]: inp, names[1]: msk}
        out = self.sess.run(None, feed)[0][0]
        if out.max() > 2.0:
            out = out / 255.0
        out = np.clip(out, 0, 1)
        out = (out.transpose(1, 2, 0) * 255).astype(np.uint8)
        out = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
        return cv2.resize(out, (w, h), interpolation=cv2.INTER_LINEAR)

    def __call__(self, frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
        if self.backend in ("onnx", "lama") and (self.sess is not None or self.lama is not None):
            H, W = mask.shape[:2]
            ys, xs = np.where(mask > 0)
            if ys.size == 0:
                return frame
            x1, x2 = int(xs.min()), int(xs.max()) + 1
            y1, y2 = int(ys.min()), int(ys.max()) + 1
            # 只把 mask 周围一小块交给模型，速度比整帧快一个数量级
            m = 48
            cx1, cy1 = max(0, x1 - m), max(0, y1 - m)
            cx2, cy2 = min(W, x2 + m), min(H, y2 + m)
            if (cx2 - cx1) * (cy2 - cy1) > 0.55 * W * H:
                cx1, cy1, cx2, cy2 = 0, 0, W, H
            crop = frame[cy1:cy2, cx1:cx2]
            cmask = mask[cy1:cy2, cx1:cx2]

            if self.backend == "onnx":
                out = self._onnx_inpaint(crop, cmask)
            else:
                from PIL import Image
                rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                out = self.lama(Image.fromarray(rgb), Image.fromarray(cmask))
                out = cv2.cvtColor(np.array(out), cv2.COLOR_RGB2BGR)

            if out.shape[:2] != crop.shape[:2]:
                out = cv2.resize(out, (crop.shape[1], crop.shape[0]))
            res = frame.copy()
            res[cy1:cy2, cx1:cx2] = out
            return res
        return cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA)
