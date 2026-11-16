#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配套库自动补齐：装完专家包不用敲任何命令，第一次干活时自己装好。

为什么需要这个文件
------------------
专家包只带程序，不带 Python 库——一个库一百来兆、还分 Windows / Mac 两份，
塞不进 zip。以前的做法是等跑到那一步才抛 ImportError，让人自己去 pip，
对不懂技术的人就是死路（11-16 在测试机上实际栽过：一路绿灯跑到读台词才炸）。
现在改成谁要用谁自己补，补不上才喊人。

补什么
------
安装名只写一个：rapidocr_onnxruntime。它自带全套依赖——
opencv（切字幕区域）、numpy（数值计算）、Pillow（画首尾帧对比图）、
onnxruntime（推理引擎）。所以别额外再指定 opencv-python-headless：
两个 opencv 发行包同时装会互相覆盖 cv2，是已知的坑。

装到哪个 Python
--------------
用跑本脚本的那个解释器（sys.executable），也就是 WorkBuddy 自带那一个。
不找系统 python，不要求用户知道 python 装在哪。

装不上怎么办
----------
依次尝试：清华镜像 → 官方源 → 装到用户目录（--user）。
三条都不通就把 pip 的原话打出来，让人拿这句话去问人，
而不是只回一句「安装失败」等于没信息。
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
import time

# (import 名, pip 安装名, 干什么用的)
# 装 rapidocr_onnxruntime 时会把后三个一起带下来，这里列出来只是为了
# 「缺哪个单独补哪个」，以及缺的时候能说清是哪一步会受影响。
SPEC = [
    ("rapidocr_onnxruntime", "rapidocr_onnxruntime", "认画面上的字幕（台词提取要用）"),
    ("cv2", "opencv-python", "切字幕区域、放大图片"),
    ("numpy", "numpy", "量音轨停顿（切点判断要用）"),
    ("PIL", "Pillow", "画首尾帧对比图（看收尾切点要用）"),
]

MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
OFFICIAL = "https://pypi.org/simple"

_MARK = "SD_DEPS_RESTARTED"     # 防止装完重启自己变成死循环
_QUIET = False


def _say(msg: str) -> None:
    if not _QUIET:
        print(msg, flush=True)


def have(import_name: str) -> bool:
    """只探不导：不执行模块代码，几十毫秒。"""
    return importlib.util.find_spec(import_name) is not None


def missing() -> list[tuple[str, str, str]]:
    """返回还缺的 [(import 名, pip 名, 说明)]，全齐就是空表。"""
    return [(imp, pip_name, why) for imp, pip_name, why in SPEC if not have(imp)]


def _pip(args: list[str], tag: str) -> tuple[int, str]:
    """跑一次 pip，行级转发输出（顺带当心跳，避免看起来卡死）。"""
    cmd = [sys.executable, "-m", "pip", "install", *args,
           "--disable-pip-version-check", "--no-input", "--timeout", "30"]
    _say(f"  → {tag}")
    env = dict(os.environ, PIP_NO_INPUT="1", PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace", env=env)
    except Exception as e:  # pip 都不在
        return 1, f"{e.__class__.__name__}: {e}"

    tail: list[str] = []
    assert p.stdout is not None
    for line in p.stdout:
        s = line.strip()
        if not s:
            continue
        tail.append(s)
        if len(tail) > 40:
            tail.pop(0)
        # 只把关键行喊出来：在装什么、下载多大、装到哪，其余吞掉免得刷屏
        if any(k in s for k in ("Collecting", "Downloading", "Installing",
                                "Successfully", "ERROR", "error:")):
            _say("    " + s[:160])
    p.wait()
    return p.returncode, "\n".join(tail[-6:])


def _pip_works() -> bool:
    r = subprocess.run([sys.executable, "-m", "pip", "--version"],
                       capture_output=True, text=True)
    return r.returncode == 0


def ensure(restart: bool = True, quiet: bool = False) -> tuple[bool, str]:
    """缺的补齐。返 (是否齐了, 说明)。

    已经齐了就是一次 find_spec，毫秒级，不碰网络、不碰 pip。
    补齐过程会打进度：第一次约 100MB，国内镜像 1–3 分钟。
    """
    global _QUIET
    _QUIET = quiet
    miss = missing()
    if not miss:
        return True, "配套库齐全"

    pkgs = []
    for _imp, pip_name, _why in miss:
        if pip_name not in pkgs:
            pkgs.append(pip_name)

    _say("配套库没装齐，先自动补（只装一次，约 100MB，国内 1–3 分钟）：")
    for _imp, pip_name, why in miss:
        _say(f"    缺 {pip_name}  — {why}")

    if not _pip_works():
        _say("  本机 python 没带 pip，试着修一下")
        r = subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return False, ("这个 python 没有 pip，也修不起来。"
                           f"它是 {sys.executable}；请把这句话原样转给专家。")

    t0 = time.time()
    attempts = [
        (["-i", MIRROR], "国内镜像"),
        (["-i", OFFICIAL], "官方源"),
        (["-i", MIRROR, "--user"], "国内镜像 + 装到用户目录"),
        (["-i", OFFICIAL, "--user"], "官方源 + 装到用户目录"),
    ]
    last = ""
    for extra, tag in attempts:
        rc, out = _pip([*pkgs, *extra], tag)
        last = out
        if rc == 0:
            break
    else:
        return False, ("自动装配套库没成功，最后 pip 是这么说的：\n"
                       + (last or "（没有输出）")
                       + f"\n解释器：{sys.executable}")

    # pip 把包写进了 site-packages，但本进程的 import 缓存还记着「没有」
    importlib.invalidate_caches()
    for _imp, _pip_name, _why in miss:
        importlib.import_module(_imp)  # 顺手验一遍真能导进来

    left = missing()
    if left:
        names = "、".join(p for _i, p, _w in left)
        if restart and not os.environ.get(_MARK):
            # 极少数情况：装好了但当前进程怎么都认不到，重启一次自己。
            # 加环境变量护栏，装失败时不会无限重启。
            _say(f"  装好了，但当前进程认不到（{names}），重启一次接着干")
            os.environ[_MARK] = "1"
            os.execv(sys.executable, [sys.executable, *sys.argv])
        return False, f"装完了仍缺 {names}，请把这句话转给专家"

    _say(f"  配套库已就位（用时 {time.time() - t0:.0f} 秒）")
    return True, "配套库已自动补齐"


def need(*import_names: str) -> None:
    """给各脚本用的单行入口：我要用的这几样必须能 import。

    齐了立刻返回（毫秒级）；不齐就当场补；补不上直接退出并说清缺什么，
    不让脚本带着半条命往后跑出更奇怪的错。
    """
    lack = [(i, p, w) for i, p, w in SPEC if i in import_names and not have(i)]
    if not lack:
        return
    ok, msg = ensure()
    if ok:
        return
    sys.exit("这一步要用的库没装上，无法继续。\n" + msg)


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="检查或补齐配套库")
    ap.add_argument("--check", action="store_true", help="只看缺不缺，不装")
    ap.add_argument("--no-install", action="store_true", help="同 --check")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.check or a.no_install or a.json:
        miss = missing()
        if a.json:
            import json

            print(json.dumps({"ok": not miss,
                              "missing": [p for _i, p, _w in miss],
                              "python": sys.executable}, ensure_ascii=False))
        else:
            if not miss:
                print(f"[OK] 配套库齐全（{sys.executable}）")
            for _i, p, w in miss:
                print(f"[要处理] 缺 {p} — {w}")
        return 0 if not miss else 1

    ok, msg = ensure(restart=False)
    print(("[OK] " if ok else "[要处理] ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
