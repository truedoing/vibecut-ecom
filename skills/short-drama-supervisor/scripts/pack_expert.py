#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本机装好的专家打成可以分发的 zip。

为什么不用系统自带的 zip 命令：macOS 的 zip 打出来的中文文件名，
到了 Windows 上解压会变成乱码，技能引用「清单人审表_模板.md」这类文件时就找不到。
这个脚本按标准把文件名标成 UTF-8，两边都认得。

用法：
    python3 pack_expert.py                 # 打到 dist/，文件名带版本号
    python3 pack_expert.py --out /tmp      # 换输出目录
    python3 pack_expert.py --check-only    # 只体检不打包

只打本机插件目录里那一份，不动仓库源码（仓库那份是开发用的，包内 skills/ 是分发副本）。
"""

import argparse
import json
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

HOME = Path.home()
NAME = "short-drama-trailer-editor"

# ---------------------------------------------------------------- 根目录唯一出口
# 11-16 加。张老师在测试机上一连踩了三处「各脚本自己数 parents 找数据目录」：
# 技能装进 ~/.workbuddy/skills 或专家包里之后，那条链会一路翻到 C:\，
# 写得严的当场抛异常（进度网页起不来），写得松的悄悄退回当前目录（切口表写错地方）。
# 根子不是某处写错，是**同一个查找逻辑被抄了好几份**。
# 所以规矩只有一条：数据目录一律 from _env import data_root。
# 打包时当门禁跑一遍，下一个新脚本想照抄会先被拦下来。
_ROOT_LOOP = re.compile(r"for\s+\w+\s+in\s+Path\(__file__\)(?:\.resolve\(\))?\.parents")
_CWD_RETURN = re.compile(r"^\s*return\s+Path\.cwd\(\)\s*$")


def check_root_exit(items) -> list:
    """扫一遍技能里的 py 文件，看有没有人又手写了一份「找数据目录」。

    _env.py 自己不算——它就是那个唯一出口。
    """
    bad = []
    for full, rel in items:
        if rel.suffix != ".py" or rel.name == "_env.py":
            continue
        try:
            src = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n, line in enumerate(src.splitlines(), 1):
            if _ROOT_LOOP.search(line):
                bad.append(f"{rel}:{n} 自己往上数目录找数据目录 —— 改成 from _env import data_root")
            elif _CWD_RETURN.match(line):
                bad.append(f"{rel}:{n} 找不到数据目录就悄悄退回当前目录 —— 改成 _env.data_root()")
    return bad

# 常见的插件落点，按顺序找
CANDIDATES = [
    HOME / ".workbuddy/plugins/marketplaces/my-experts/plugins" / NAME,
    HOME / ".workbuddy/plugins/marketplaces/my-creators/plugins" / NAME,
    HOME / ".workbuddy/plugins" / NAME,
]

SKIP_DIRS = {"__pycache__", "__MACOSX", ".git", "node_modules", "bin"}
SKIP_FILES = {".DS_Store", ".gitignore", "Thumbs.db"}
SKIP_EXT = {".pyc", ".pyo"}


def find_host() -> Path:
    for p in CANDIDATES:
        if (p / ".codebuddy-plugin" / "plugin.json").exists():
            return p
    # 兜底：在 marketplaces 下扫一遍
    base = HOME / ".workbuddy/plugins/marketplaces"
    if base.exists():
        for p in base.glob(f"*/plugins/{NAME}"):
            if (p / ".codebuddy-plugin" / "plugin.json").exists():
                return p
    raise SystemExit(f"找不到装好的专家目录，找过这些地方：\n  " + "\n  ".join(map(str, CANDIDATES)))


def read_version(host: Path) -> str:
    d = json.loads((host / ".codebuddy-plugin" / "plugin.json").read_text(encoding="utf-8"))
    v = str(d.get("version") or "").strip()
    if not v:
        raise SystemExit("plugin.json 里没有版本号，先填一个再打包")
    return v


def walk(host: Path):
    """列出要打进包的文件，返回 [(绝对路径, 包内相对路径)]。"""
    items = []
    for r, dirs, fs in os.walk(host):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(fs):
            if f in SKIP_FILES or Path(f).suffix in SKIP_EXT:
                continue
            full = Path(r) / f
            items.append((full, full.relative_to(host)))
    return items


def check(host: Path, items) -> list:
    """打包前体检，返回问题清单（空＝没问题）。"""
    bad = []
    pj = host / ".codebuddy-plugin" / "plugin.json"
    try:
        d = json.loads(pj.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"plugin.json 读不出来：{e}"]

    for k in ("name", "version", "description", "author", "agents"):
        if not d.get(k):
            bad.append(f"plugin.json 缺 {k}")
    for s in d.get("skills") or []:
        if not (host / s).is_dir():
            bad.append(f"plugin.json 声明了技能但目录不存在：{s}")
    for a in d.get("agents") or []:
        if not (host / a).is_file():
            bad.append(f"plugin.json 声明了人格文件但不存在：{a}")

    names = {str(rel) for _, rel in items}
    if not any(n.startswith("agents/") for n in names):
        bad.append("包里没有 agents/ 人格文件")
    if not any(n.startswith("skills/short-drama-supervisor/") for n in names):
        bad.append("包里没有主管技能")

    # 打包时最容易漏的东西
    for must in ("LICENSE", "README.md"):
        if must not in names:
            bad.append(f"包根目录缺 {must}")

    # bin/ 已从打包范围里排除（SKIP_DIRS）。可执行文件不打进包：
    # ffmpeg / ffprobe 改成按平台自己下（见 skills/.../scripts/ffbin.py），
    # 包从 37.2MB 降到 1MB 以内，Mac 用户也不用白背 Windows 的 exe。
    big = [(str(rel), full.stat().st_size) for full, rel in items if full.stat().st_size > 10 * 1024 * 1024]
    for n, sz in big:
        bad.append(f"{n} 有 {sz // 1024 // 1024}MB，包里不该有这么大的文件，确认是不是误打进去的素材")

    bad += check_root_exit(items)
    return bad


def build(host: Path, out_dir: Path, version: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{NAME}_v{version}.zip"
    items = walk(host)
    total = sum(f.stat().st_size for f, _ in items)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for full, rel in items:
            # 包内顶层目录用专家名，解压出来就是一个完整文件夹
            arc = f"{NAME}/{rel.as_posix()}"
            info = zipfile.ZipInfo(arc)
            st = full.stat()
            info.date_time = datetime.fromtimestamp(st.st_mtime).timetuple()[:6]
            info.compress_type = zipfile.ZIP_DEFLATED
            # 可执行位要留着，不然 Mac / Linux 上 install.command 点不动
            mode = 0o755 if os.access(full, os.X_OK) else 0o644
            info.external_attr = (0o100000 | mode) << 16
            with z.open(info, "w") as fh:
                fh.write(full.read_bytes())
    print(f"打好：{zip_path}")
    print(f"  文件 {len(items)} 个，未压缩 {total / 1024 / 1024:.1f}MB，"
          f"压缩后 {zip_path.stat().st_size / 1024 / 1024:.1f}MB")
    return zip_path


def verify(zip_path: Path, version: str) -> int:
    """打完自检：文件齐不齐、中文名标没标对、版本号对不对。"""
    bad = []
    with zipfile.ZipFile(zip_path) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        names = [i.filename for i in infos]

        junk = [n for n in names if "__pycache__" in n or n.endswith(".pyc") or ".DS_Store" in n]
        if junk:
            bad.append(f"包里有垃圾文件 {len(junk)} 个：{junk[:3]}")

        # 关键：非 ASCII 文件名必须带 UTF-8 标记（flag bit 11），否则 Windows 解压乱码
        nonascii = [i for i in infos if any(ord(c) > 127 for c in i.filename)]
        nomark = [i.filename for i in nonascii if not (i.flag_bits & 0x800)]
        if nomark:
            bad.append(f"有 {len(nomark)} 个中文文件名没标 UTF-8，Windows 上会乱码：{nomark[:3]}")

        pj = z.read(f"{NAME}/.codebuddy-plugin/plugin.json").decode("utf-8")
        got = json.loads(pj).get("version")
        if got != version:
            bad.append(f"包内版本号是 {got}，不是 {version}")

        for must in ("LICENSE", "README.md", "INSTALL.md", "agents/", "skills/"):
            key = must if must.startswith(NAME) else f"{NAME}/{must}"
            if not any(n.startswith(key) for n in names):
                bad.append(f"包里缺 {must}")

    print(f"自检：文件 {len(names)} 个，中文名 {len(nonascii)} 个（都标了 UTF-8）" if not nomark
          else f"自检：文件 {len(names)} 个")
    if bad:
        print("有问题：")
        for b in bad:
            print("  -", b)
        return 1
    print("自检通过。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="把本机的短剧专家打成可分发的 zip")
    ap.add_argument("--out", default=os.environ.get("SD_PACK_OUT") or str(Path.cwd() / "dist"),
                    help="输出目录（默认当前目录下的 dist/，可用 SD_PACK_OUT 覆盖）")
    ap.add_argument("--check-only", action="store_true", help="只体检，不打包")
    a = ap.parse_args()

    host = find_host()
    version = read_version(host)
    items = walk(host)
    print(f"源目录：{host}")
    print(f"版本：{version}")

    problems = check(host, items)
    if problems:
        print("体检没过：")
        for p in problems:
            print("  -", p)
        if a.check_only or problems:
            return 1
    if a.check_only:
        print("体检通过，没打包。")
        return 0

    zp = build(host, Path(a.out), version)
    return verify(zp, version)


if __name__ == "__main__":
    sys.exit(main())
