#!/usr/bin/env python3
"""环境自检 —— 全局判断只归入口，不放个体技能。

用法: python3 check_env.py [--json]
退出码: 0 = 硬依赖齐备（警告不算失败）

查四类，缺什么给什么命令：
  1. python     （WorkBuddy 自带 3.13；>=3.10 即可）
  2. ffmpeg/ffprobe（唯一系统级外部依赖，WorkBuddy 不代装）
  3. numpy      （WorkBuddy 自带环境已含；缺失只影响 extract_voice 的音高审计）
  4. 密钥与数据（.env 的 API key、ASSET_ROOT、packs/ 与 runs/ 目录）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

FFMPEG_HINT = {
    "win32": "winget install Gyan.FFmpeg",
    "darwin": "brew install ffmpeg",
}.get(sys.platform, "apt install ffmpeg  # 或从 ffmpeg.org 下载")

# 实测（2026-11-08）：本机 ffmpeg 装在 anaconda，只是没进 WorkBuddy 会话的 PATH。
# 旧版只会报「缺 → brew install」，导致 Agent 另装 imageio-ffmpeg 绕过技能链。
# 入口自检应能识别「装了但不在 PATH」，并给出可直接复制的修法，而不是劝人重装。
COMMON_FFMPEG_DIRS = (
    "/opt/anaconda3/bin", "/opt/homebrew/bin", "/usr/local/bin",
    str(Path.home() / "opt/anaconda3/bin"), str(Path.home() / "anaconda3/bin"),
    str(Path.home() / "miniconda3/bin"),
)


def find_ffmpeg(exe: str) -> tuple[str, str]:
    """返回 (可执行文件路径, 需补进 PATH 的目录)。目录为空表示本来就在 PATH 里。"""
    p = shutil.which(exe)
    if p:
        return p, ""
    for d in COMMON_FFMPEG_DIRS:
        cand = Path(d) / exe
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand), d
    return "", ""


KEY_VARS = {
    "APIYI_API_KEY": "出片（ecommerce-video-gen → APIYI）",
    # 2026-11-08 修正：旁白实现是 gen_voice.py 的 MiMo voiceclone，
    # 豆包三件套是「MiMo→豆包→回 MiMo」翻案留下的遗留配置，不是当前链路。
    "DOUBAO_TTS_KEY": "遗留备用（旁白已回 MiMo，见 supervisor SKILL.md 第九节）",
    "DOUBAO_ACCESS_TOKEN": "遗留备用（与 DOUBAO_TTS_KEY 同组）",
    "DOUBAO_APP_ID": "遗留备用（与 DOUBAO_TTS_KEY 同组）",
    "MIMO_API_KEY": "旁白/音色锚（ecommerce-narration → gen_voice.py MiMo voiceclone）"
                    " + 资产分析与音色审计",
}


def find_env() -> Path | None:
    seen: list[Path] = []
    for base in [Path.cwd(), *list(Path.cwd().parents)[:3]]:
        seen.append(base / ".env")
    here = Path(__file__).resolve()
    seen.append(here.parents[3] / ".env")
    for p in seen:
        if p.is_file():
            return p
    return None


def parse_env(p: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    a = ap.parse_args()

    rows: list[tuple[str, str, str, str]] = []  # (项, 状态, 说明, 修法)
    hard_fail = False

    # 1. python
    v = sys.version_info
    ok = v >= (3, 10)
    hard_fail |= not ok
    rows.append(("python", "OK" if ok else "缺",
                 f"{v.major}.{v.minor}.{v.micro}" + ("（WorkBuddy 自带）" if ok else ""),
                 "升级到 >=3.10" if not ok else ""))

    # 2. ffmpeg / ffprobe
    for exe in ("ffmpeg", "ffprobe"):
        p, hint_dir = find_ffmpeg(exe)
        if not p:
            hard_fail = True
            rows.append((exe, "缺", "混剪/抽帧/音锚全靠它，WorkBuddy 不代装", FFMPEG_HINT))
        elif hint_dir:
            rows.append((exe, "OK", f"{p}（已装，但不在 PATH）",
                         f"export PATH={hint_dir}:$PATH  # 本次会话；写进 shell profile 永久生效"))
        else:
            rows.append((exe, "OK", p, ""))

    # 3. numpy（唯一第三方库；自带环境已含）
    try:
        import numpy  # noqa: F401
        rows.append(("numpy", "OK", "仅音高审计用", ""))
    except ImportError:
        rows.append(("numpy", "警告", "缺失只影响 extract_voice 音高审计",
                     "pip install numpy"))

    # 4. 密钥
    envp = find_env()
    env: dict[str, str] = parse_env(envp) if envp else {}
    env.update({k: v for k, v in os.environ.items() if k in KEY_VARS and v})
    if envp:
        rows.append((".env", "OK", str(envp), ""))
    else:
        rows.append((".env", "警告", "没找到（在仓库根或当前目录建一个）", "参照 .env.example"))
    for k, use in KEY_VARS.items():
        if k == "DOUBAO_ACCESS_TOKEN" and env.get("DOUBAO_TTS_KEY"):
            continue  # 三件套任一在即算旁白可用
        if env.get(k):
            rows.append((k, "OK", use, ""))
        else:
            rows.append((k, "警告", f"缺：{use}", "填进 .env"))

    # 5. 数据与素材
    cwd = Path.cwd()
    packs = cwd / "packs"
    runs = cwd / "runs"
    cats = [d.name for d in packs.iterdir() if d.is_dir()] if packs.is_dir() else []
    rows.append(("packs/", "OK" if cats else "警告",
                 f"{len(cats)} 个品类: {'、'.join(cats)}" if cats else "还没有品类包（先跑 ecommerce-category-pack）", ""))
    rows.append(("runs/", "OK" if runs.is_dir() else "警告",
                 "产物目录在" if runs.is_dir() else "还没有产物目录", ""))
    # 多线各自有素材根：ASSET_ROOT（轻食）+ <LINE>_ASSET_ROOT（如 SHORT_DRAMA_ASSET_ROOT）
    # 2026-11-08 起逐个检查，不再只认单值 —— 各线素材根不在同一目录
    asset_keys = sorted(k for k in env
                        if k == "ASSET_ROOT" or k.endswith("_ASSET_ROOT"))
    if not asset_keys:
        rows.append(("ASSET_ROOT", "警告",
                     "素材根目录未登记——**建品类时必须提供**（一行写进 .env）",
                     'ASSET_ROOT=<本机素材目录> 写入 .env'))
    for k in asset_keys:
        ar = env.get(k, "")
        if ar and Path(ar).is_dir():
            rows.append((k, "OK", f"素材根目录 {ar}", ""))
        else:
            rows.append((k, "警告",
                         f"素材根未登记或不存在（{ar or '空'}）——该线开工前必须提供",
                         f"{k}=<本机素材目录> 写入 .env"))

    if a.json:
        print(json.dumps({"hard_fail": hard_fail, "rows": [
            {"item": r[0], "status": r[1], "info": r[2], "fix": r[3]} for r in rows
        ]}, ensure_ascii=False, indent=1))
        return 1 if hard_fail else 0

    print("=== 环境自检（入口职责，个体技能不重复查） ===")
    for item, st, info, fix in rows:
        mark = {"OK": "OK ", "缺": "✗  ", "警告": "⚠  "}.get(st, "?  ")
        line = f"{mark}{st:<4} {item:<22} {info}"
        print(line)
        if fix and st != "OK":
            print(f"       ↳ 修法: {fix}")
    print()
    if hard_fail:
        print("结论：✗ 硬依赖缺失（ffmpeg/python），先按上面修法装齐再开工")
    else:
        print("结论：✓ 硬依赖齐备；警告项按需补（key/数据不阻塞开工，用到哪项补哪项）")
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
