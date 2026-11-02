"""集中配置：数据根、服务地址、ffmpeg 探测。

优先级：显式参数 > 环境变量 > config.json > 默认值。
项目数据全部从 data_root 派生，任何模块都不允许写死绝对路径。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REPO_DIR = APP_DIR.parent.parent

DEFAULT_DATA_ROOT = Path.home() / "VibeCutEcomData"
DEFAULT_CONFIG = Path.home() / ".vibecut-ecom.json"

_SLUG_RE = re.compile(r"[^\w一-鿿-]+", re.UNICODE)


def slugify(name: str) -> str:
    """项目名 → 目录名。保留中文，替换路径不安全字符。"""
    slug = _SLUG_RE.sub("_", (name or "").strip()).strip("_")
    return slug or "project"


@dataclass
class Settings:
    data_root: Path
    host: str = "127.0.0.1"
    port: int = 8766
    ffmpeg: str = ""
    frontend_dir: Path = field(default_factory=lambda: REPO_DIR / "web" / "dist")

    @property
    def db_path(self) -> Path:
        return self.data_root / "vibecut.db"

    @property
    def projects_dir(self) -> Path:
        return self.data_root / "projects"

    @property
    def api_base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def project_dir(self, slug: str) -> Path:
        return self.projects_dir / slug

    def project_assets_dir(self, slug: str) -> Path:
        return self.project_dir(slug) / "assets"

    def project_tasks_dir(self, slug: str) -> Path:
        return self.project_dir(slug) / "tasks"

    def project_desub_dir(self, slug: str) -> Path:
        return self.project_dir(slug) / "desub"

    def task_dir(self, slug: str, task: str) -> Path:
        return self.project_tasks_dir(slug) / slugify(task)

    def relative_to_project(self, slug: str, path: Path) -> str:
        """存库时统一用项目内相对路径，迁移/换盘不受影响。"""
        return str(Path(path).resolve().relative_to(self.project_dir(slug).resolve()))

    def project_path(self, slug: str, rel_path: str) -> Path:
        return self.project_dir(slug) / rel_path

    def ensure_dirs(self, slug: str) -> None:
        for d in (
            self.project_assets_dir(slug) / "files",
            self.project_assets_dir(slug) / "thumbs",
            self.project_tasks_dir(slug),
            self.project_desub_dir(slug),
        ):
            d.mkdir(parents=True, exist_ok=True)


def _read_config(path: Path | None) -> dict:
    if path and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def load_settings(data_root: str | Path | None = None,
                  config_path: str | Path | None = None) -> Settings:
    cfg = _read_config(Path(config_path) if config_path else DEFAULT_CONFIG)
    raw_root = data_root or os.environ.get("VIBECUT_DATA_ROOT") or cfg.get("data_root")
    root = Path(raw_root).expanduser() if raw_root else DEFAULT_DATA_ROOT
    ffmpeg = os.environ.get("VIBECUT_FFMPEG", cfg.get("ffmpeg", ""))
    host = os.environ.get("VIBECUT_HOST", cfg.get("host", "127.0.0.1"))
    port = int(os.environ.get("VIBECUT_PORT", cfg.get("port", 8766)))
    frontend = Path(cfg.get("frontend_dir", REPO_DIR / "web" / "dist")).expanduser()
    return Settings(data_root=root, host=host, port=port, ffmpeg=ffmpeg,
                    frontend_dir=frontend)
