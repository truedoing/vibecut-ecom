#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短剧线环境自检（开工第 0 步，只在这里做一次）。

11-15 改成「装完就能自查」：数据目录找不到就自己建，不再要求先有仓库。
11-16 起 ffmpeg/ffprobe 和几个 Python 库都改成「缺了当场自动补」，
所以查的只有四类：python、ffmpeg/ffprobe、数据目录与原片目录、出片密钥。
补不上的才报给人，报的时候带上「手动放哪儿」。

原片目录写在数据目录的 .env 里（SHORT_DRAMA_ASSET_ROOT），
技能代码和本脚本都不写死任何本机绝对路径。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SKILLS = ["short-drama-select", "short-drama-pack", "short-drama-asset-index",
          "short-drama-ai-assets", "short-drama-script", "short-drama-video",
          "short-drama-trailer", "short-drama-supervisor"]  # 主管也算一个，对外说 8 个要对得上


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import _env as env_mod
    except Exception:
        print("找不到同目录的 _env.py，技能包不完整")
        return 1

    root = env_mod.data_root(create=True)
    env = env_mod.read_env(root)
    ok, bad = [], []

    def check(name, cond, fix=""):
        (ok if cond else bad).append(f"{name}{'' if cond else '  ← ' + fix}")

    check(f"python3 {sys.version.split()[0]}", True)

    # ffmpeg / ffprobe：包里不再带那 100MB 可执行文件，缺了在这里按平台当场下
    # （约 30–120MB，只下一次，缓存在数据目录 bin/，以后升级专家包不用重下）。
    # 放在最前面探——它是整条链的第一步，缺了后面什么都干不了。
    try:
        import ffbin
        good, msg = ffbin.ensure()
        if good:
            ok.append(f"ffmpeg / ffprobe 可用（{msg}）")
        else:
            bad.append(f"ffmpeg 不可用  ← {msg}")
    except Exception as e:
        bad.append(f"ffmpeg 检查没跑成  ← {e.__class__.__name__}: {e}")

    root_value = env.get("SHORT_DRAMA_ASSET_ROOT") or os.environ.get("SHORT_DRAMA_ASSET_ROOT")
    check("原片目录已填写", bool(root_value), f"打开 {root / '.env'} 填 SHORT_DRAMA_ASSET_ROOT")
    if root_value:
        check("原片目录存在", Path(root_value).exists(), root_value)

    # 配套库：认字幕的字、切字幕区域、量音轨停顿、画对比图。
    # 装完专家包不该让用户自己敲 pip —— 缺了就在这里当场补，一次性，约 100MB。
    try:
        import deps as dep_mod
        miss = dep_mod.missing()
        if not miss:
            ok.append(f"配套库齐全（{len(dep_mod.SPEC)} 项）")
        else:
            names = "、".join(p for _i, p, _w in miss)
            print(f"配套库缺 {names}，正在自动补装（约 100MB，国内 1–3 分钟，只装一次）…")
            good, msg = dep_mod.ensure()
            if good:
                ok.append("配套库已自动补齐（认字幕/图像/数值计算）")
            else:
                bad.append(f"配套库装不上  ← {msg}")
    except Exception as e:
        bad.append(f"配套库检查没跑成  ← {e.__class__.__name__}: {e}")

    for d in ("packs/short-drama", "runs", "assets/short-drama"):
        check(f"{d}/ 存在", (root / d).exists(), "重跑一次自检会自动创建")

    check("工作台已就位", (root / "runs" / "工作台.json").exists(), "重跑一次自检会自动创建")

    key = env.get("APIYI_API_KEY") or os.environ.get("APIYI_API_KEY")
    if key:
        ok.append("出片密钥已配置")
    else:
        bad.append("出片密钥没填  ← 只有要用 AI 玩法才需要，纯剪可以不填")

    # 画面打标：默认不开（11-16 张老师定：纯剪用不上，别默认烧接口钱）。
    # 开了才有「谁在说」——原片字幕只给说了什么，不给谁说的；
    # 缺了它每句话都会被当成画外音（11-16 实测：49 句全部判成画外）。
    # 服务地址和模型名是写死的，只有 Key 需要填。
    tag_on = str(env.get("SHORT_DRAMA_TAG") or "").strip().lower() in ("on", "1", "true", "yes")
    if not tag_on:
        if env.get("MIMO_API_KEY"):
            ok.append("画面打标默认关着（要用就 .env 写 SHORT_DRAMA_TAG=on 或跑时加 --tag；"
                      f"Key 已填，模型 {env_mod.MIMO_MODEL}）")
        else:
            ok.append("画面打标默认关着（纯剪不需要）")
    elif env.get("MIMO_API_KEY"):
        ok.append(f"画面打标已开启（判断谁在说必需，模型 {env_mod.MIMO_MODEL}）")
    else:
        bad.append(
            "画面打标开着但没 Key  ← 缺 MIMO_API_KEY"
            "；不想打标就把 .env 里 SHORT_DRAMA_TAG 删掉或写 off"
        )

    host_skills = env_mod.HOST / "skills"
    missing = [s for s in SKILLS if not (host_skills / s / "SKILL.md").exists()]
    if missing:
        bad.append("技能不齐  ← 缺 " + ", ".join(missing))
    else:
        ok.append(f"技能 {len(SKILLS)} 个齐全")

    print(f"数据目录：{root}")
    for line in ok:
        print("  [OK]  ", line)
    for line in bad:
        print("  [要处理]", line)

    hard = [b for b in bad if not (b.startswith("出片密钥") or b.startswith("画面打标"))]
    tail = "（只差出片密钥，纯剪不受影响）" if (bad and not hard) else ""
    print(f"\n结果：{len(ok)} 项通过 / {len(bad)} 项待处理{tail}")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
