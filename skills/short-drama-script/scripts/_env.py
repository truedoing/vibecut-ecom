#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短剧技能公共定位：数据目录 + ffmpeg 路径。换机器、换目录都不用改代码。

为什么单独有这么个文件
--------------------
一个技能可能住在两种地方：

  1. 工作仓库里：  <仓库>/skills/<技能>/scripts/xxx.py
  2. WorkBuddy 插件里：<专家包>/skills/<技能>/scripts/xxx.py

两种布局下 `Path(__file__).parents[3]` 都指向"宿主根"，但只有第 1 种情况下
宿主根同时也是放素材和进度的目录。所以数据目录不能靠数层级猜，必须单独找。

找数据目录的顺序（先命中先用）
  1. 环境变量 SHORT_DRAMA_ROOT（要强制指定位置时用）
  2. 从当前工作目录往上，找带 runs/工作台.json 或 packs/short-drama 的目录
  3. 从本技能位置往上，找同样的标志（技能住在仓库里时命中）
  4. 都没有 → 默认 ~/Documents/短剧工作区（不存在就自动建）

找 ffmpeg 的顺序
  1. 环境变量 SHORT_DRAMA_FFMPEG / SHORT_DRAMA_FFPROBE
  2. 宿主包的 bin/（专家包自带：<专家包>/bin/ffmpeg.exe）
  3. 数据目录的 bin/
  4. 系统 PATH
  5. 老的固定安装位置（本机历史遗留，留着不碍事）

11-15 为「给不懂技术的人装一次就能用」改写：默认位置自己建，
不需要先跑安装脚本，也不需要用户会敲命令行。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

_HERE = Path(__file__).resolve()
# 文件在 <宿主>/skills/<技能>/scripts/_env.py，往上四层是宿主根
HOST = _HERE.parents[3]

DEFAULT_DIR_NAME = "短剧工作区"
_cache: Path | None = None


def _looks_like_root(p: Path) -> bool:
    """认两个标志：有工作台，或者有剧集包目录。"""
    return (p / "runs" / "工作台.json").exists() or (p / "packs" / "short-drama").is_dir()


def default_root() -> Path:
    """默认数据目录：文档下的「短剧工作区」。

    macOS/Windows 的「文档」都可能不存在（OneDrive 重定向、精简装系统），
    所以退到用户主目录，不硬玩 Documents。
    """
    home = Path.home()
    docs = home / "Documents"
    base = docs if docs.is_dir() else home
    return base / DEFAULT_DIR_NAME


def data_root(create: bool = False) -> Path:
    """返回数据目录。create=True 时顺手把骨架建好。"""
    global _cache
    if _cache is not None:
        root = _cache
    else:
        root = None
        env = os.environ.get("SHORT_DRAMA_ROOT")
        if env:
            root = Path(env).expanduser()
        if root is None:
            for start in (Path.cwd().resolve(), HOST):
                for p in [start, *start.parents]:
                    if _looks_like_root(p):
                        root = p
                        break
                if root is not None:
                    break
        if root is None:
            root = default_root()
        _cache = root

    if create:
        ensure_skeleton(root)
    return root


def ensure_skeleton(root: Path) -> Path:
    """建目录骨架 + 空的配置文件。已经有的一律不动。"""
    for sub in ("runs", "packs/short-drama", "assets/short-drama", "config"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    env_file = root / ".env"
    if not env_file.exists():
        env_file.write_text(
            "# 短剧工作区配置。纯文本文件，用记事本改，改完存盘即可。\n"
            "# 也可以在对话里直接告诉专家「原片在 D:\\短剧」，它会替你写进来。\n"
            "\n"
            "# ============ 必填一：原片在哪 ============\n"
            "# 原片放在哪个【文件夹】（不是某个 mp4 文件）\n"
            "#   Windows 例：SHORT_DRAMA_ASSET_ROOT=D:\\短剧\n"
            "#   Mac     例：SHORT_DRAMA_ASSET_ROOT=/Users/你的名字/短剧\n"
            "SHORT_DRAMA_ASSET_ROOT=\n"
            "\n"
            "# ============ 可选：让 AI 认画面（默认关）============\n"
            "# 原片字幕只写「说了什么」，不写「谁说的」。谁在动嘴只能看画面判断，\n"
            "# 所以要判「谁在说、什么情绪、什么景别」就得开这一项。\n"
            "# 11-16 起默认关：纯剪用不上，而这一步是全程唯一花钱的地方\n"
            "# （一部 17 集的剧约 134 万 tokens；一部剧打一次，所有片子复用）。\n"
            "# 不开也能跑：只切镜头边界，句级表的画内/画外会全判画外，选段靠台词文本。\n"
            "# 要用就把下面这行改成 on，或者跑的时候加 --tag。\n"
            "SHORT_DRAMA_TAG=off\n"
            "# 开了才需要填 Key；服务地址和模型名是写死的，不用管。\n"
            "MIMO_API_KEY=\n"
            "\n"
            "# ============ 可选：AI 出片 ============\n"
            "# 只有让 AI 生成一小段插进片子才要（apiyi 网关）。纯剪不填。\n"
            "APIYI_API_KEY=\n",
            encoding="utf-8",
        )

    workbench = root / "runs" / "工作台.json"
    if not workbench.exists():
        import json

        workbench.write_text(
            json.dumps(
                {
                    "_readme": "短剧工作台：active 记现在做哪部剧哪条片子；dramas 记每部剧三个目录怎么对应；shared 记共用层版本。",
                    "active": {"drama": "", "task": "", "updated_at": ""},
                    "dramas": {},
                    "shared": {},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    sources = root / "config" / "drama-sources.json"
    if not sources.exists():
        import json

        sources.write_text(
            json.dumps(
                {"_说明": "本机原片目录对照表：剧名 → 原片所在文件夹。素材搬家只改这里。", "dramas": {}},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return root


def tool(name: str) -> str:
    """找 ffmpeg / ffprobe 的可执行路径。"""
    exe = name + (".exe" if os.name == "nt" else "")
    env_key = "SHORT_DRAMA_" + name.upper()
    env_val = os.environ.get(env_key)
    if env_val and Path(env_val).exists():
        return env_val

    candidates = [HOST / "bin" / exe, data_root() / "bin" / exe]
    for cand in candidates:
        if cand.exists():
            return str(cand)

    found = shutil.which(name)
    if found:
        return found

    for old in (f"/opt/anaconda3/bin/{name}", f"/usr/local/bin/{name}", f"/opt/homebrew/bin/{name}"):
        if Path(old).exists():
            return old

    return name  # 交给系统去报"找不到命令"


def ffmpeg() -> str:
    return tool("ffmpeg")


def ffprobe() -> str:
    return tool("ffprobe")


def read_env(root: Path | None = None) -> dict:
    """读数据目录 .env，叠加到 os.environ 之上返回（不改进程环境）。"""
    env = dict(os.environ)
    env_file = (root or data_root()) / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


# 画面打标用的服务地址和模型名是固定的，不写进配置文件，省得填错。
MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
MIMO_MODEL = "mimo-v2.6-pro"


def mimo(root: Path | None = None) -> dict:
    """画面打标配置。地址和模型名固定，只需要 .env 里的 Key。

    老配置文件如果还留着 MIMO_BASE_URL / MIMO_MODEL，仍然优先用它们，
    方便临时换模型试效果；没写就用固定的。
    """
    env = read_env(root)
    return {
        "base_url": (env.get("MIMO_BASE_URL") or MIMO_BASE_URL).rstrip("/"),
        "model": env.get("MIMO_MODEL") or MIMO_MODEL,
        "api_key": (env.get("MIMO_API_KEY") or "").strip(),
    }
