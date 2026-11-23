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

import json
import os
import sys
from pathlib import Path

SKILLS = ["short-drama-select", "short-drama-pack", "short-drama-asset-index",
          "short-drama-ai-assets", "short-drama-microscript", "short-drama-script",
          "short-drama-video", "short-drama-trailer", "short-drama-supervisor",
          "short-drama-dedup", "short-drama-modify"]
# 主管也算一个，对外说 11 个要对得上（去重 / 修饰 11-16 加、微剧本 11-23 加，都不新增流水线环节）



def _expert_version_lines(host: Path) -> list[tuple[str, bool]]:
    """读本地已装专家版本，并核对各处登记是否一致。返回 (提示文字, 是否算问题)。"""
    # 专家装在 WorkBuddy 自己的数据目录（~/.workbuddy），不是短剧仓库；
    # 传进来的 host 是仓库根，这里另起 ~/.workbuddy，可被 WB_WORKDIR 覆盖便于测试。
    base = Path(os.environ.get("WB_WORKDIR") or (Path.home() / ".workbuddy"))
    base = base / "plugins" / "marketplaces" / "my-experts"
    plugin = base / "plugins" / "short-drama-trailer-editor" / ".codebuddy-plugin" / "plugin.json"
    market = base / ".codebuddy-plugin" / "marketplace.json"
    out: list[tuple[str, bool]] = []
    local_ver = None
    try:
        local_ver = json.loads(plugin.read_text(encoding="utf-8")).get("version")
    except Exception:
        out.append(("专家版本号读不到  ← 安装位 plugin.json 缺失或损坏", True))
    if local_ver:
        out.append((f"专家版本 v{local_ver}（权威源：安装位 plugin.json）", False))
    try:
        data = json.loads(market.read_text(encoding="utf-8"))
        entry = next(
            (p for p in data.get("plugins", [])
             if p.get("name") == "short-drama-trailer-editor"
             or str(p.get("source", "")).endswith("short-drama-trailer-editor")),
            None,
        )
        reg_ver = entry.get("version") if entry else None
        if entry is None:
            out.append(("专家登记缺失  ← marketplace.json 里没有这条", True))
        elif reg_ver != local_ver:
            out.append((f"专家版本号对不上  ← plugin.json 是 v{local_ver}，登记处是 v{reg_ver}；以 plugin.json 为准", True))
    except Exception:
        out.append(("专家登记读不到  ← marketplace.json 缺失或损坏", True))
    out.append(('资产库新版核对：助手开工时联网查「资产库/专家包/版本.json」，有新版会提示', False))
    return out


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

    # 专家版本核对（11-18 张老师定）：版本号只认安装位的 plugin.json，
    # 但登记处 marketplace.json 曾残留旧版本（装了 0.5 仍读到 0.1.0 那次），
    # 所以两处一起读，不一致就报警——让"装了新版却读成旧版"当场现形。
    # 资产库的版本联网核对由助手开工时做（脚本连不上需要登录的资产库），
    # 有新版就提示人升级，不自动装。
    ver_lines = _expert_version_lines(env_mod.HOST)
    for line, is_bad in ver_lines:
        (bad if is_bad else ok).append(line)

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
