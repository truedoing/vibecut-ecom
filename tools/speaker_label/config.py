# -*- coding: utf-8 -*-
"""读取 config.json，暴露成大写常量。改配置只动 config.json。"""
import json
from pathlib import Path

_cfg = json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))

DRAMA = _cfg["drama"]
SOURCE_DIR = _cfg["source_dir"]
LINES_DIR = _cfg["lines_dir"]
EPISODE_COUNT = _cfg.get("episode_count", 60)
SAMPLE_RATE = _cfg.get("sample_rate", 16000)
CLIP_SECONDS = _cfg.get("clip_seconds", 2.4)
HF_ENDPOINT = _cfg.get("hf_endpoint", "https://hf-mirror.com")
MODEL_SOURCE = _cfg.get("model_source", "speechbrain/spkrec-ecapa-voxceleb")
SPEAKERS = _cfg.get("speakers", {})
