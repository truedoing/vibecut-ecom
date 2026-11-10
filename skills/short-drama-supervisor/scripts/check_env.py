#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短剧线环境自检（short-drama-supervisor 开工第 0 步，只在这里做一次）。

只查短剧线要用的东西，不查轻食/图书。素材根一律从仓库 .env 读，
技能文档与脚本都不写死本机绝对路径。
"""
import os
import shutil
import sys
from pathlib import Path

SKILLS = ["short-drama-select", "short-drama-pack", "short-drama-asset-index",
          "short-drama-ai-assets", "short-drama-script", "short-drama-video",
          "short-drama-trailer"]


def find_root():
    for parent in Path(__file__).resolve().parents:
        if (parent / ".env").exists():
            return parent
    return Path.cwd()


def load_env(root):
    env = {}
    for line in (root / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main():
    root = find_root()
    env = load_env(root) if (root / ".env").exists() else {}
    ok, bad = [], []

    def check(name, cond, fix=""):
        (ok if cond else bad).append(f"{name}{'' if cond else '  ← ' + fix}")

    check(f"python3 {sys.version.split()[0]}", True)
    for tool in ("ffmpeg", "ffprobe"):
        p = shutil.which(tool) or "/opt/anaconda3/bin/" + tool
        check(tool, os.path.exists(p), "Mac: brew install ffmpeg；Windows: winget install Gyan.FFmpeg")

    root_value = env.get("SHORT_DRAMA_ASSET_ROOT") or os.environ.get("SHORT_DRAMA_ASSET_ROOT")
    check("SHORT_DRAMA_ASSET_ROOT 已配置", bool(root_value), "写进仓库根 .env")
    if root_value:
        check("素材根目录存在", Path(root_value).exists(), root_value)

    for d in ("packs/short-drama", "runs", "assets/short-drama"):
        check(f"{d}/ 存在", (root / d).exists(), f"mkdir -p {root / d}")

    for key in ("APIYI_API_KEY",):
        check(f"{key} 已配置", bool(env.get(key)), "写进仓库根 .env（出片才用）")

    missing = [s for s in SKILLS if not (root / "skills" / s / "SKILL.md").exists()]
    check(f"短剧技能 {len(SKILLS)} 个齐全", not missing,
          "缺 " + ", ".join(missing) if missing else "")

    print(f"仓库根：{root}")
    for line in ok:
        print("  ✅", line)
    for line in bad:
        print("  ❌", line)
    print(f"\n结果：{len(ok)} 项通过 / {len(bad)} 项待修")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
