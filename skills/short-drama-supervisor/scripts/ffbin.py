#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ffmpeg / ffprobe 按平台自己备好：专家包不再背着 100MB 的可执行文件。

为什么改成不在包里带
--------------------
包 37.2MB，其中 36.5MB（98%）是两个 exe。而且包里只放得下 Windows 版，
Mac 用户背着这 36.5MB 却根本用不到，全靠系统里的 ffmpeg——他自己没装过就跑不了。
把可执行文件拿出来单独下，包能降到 0.7MB，顺带把 Mac 那个缺口补上。

为什么不走 pip
-------------
pip 上唯一像样的 ffmpeg 包（imageio-ffmpeg）**只带 ffmpeg、不带 ffprobe**，
实测 Mac 和 Windows 两个平台的包都这样。而「这一段最多能切到第几秒」是靠
ffprobe 读视频流时长算的，缺了它这条铁律直接作废。而且它 Windows 版单个
ffmpeg.exe 就 83.6MB，比我们原来那份 64MB 还大——换过去体积不减反增。

所以只能自己下。下载源优先走**国内**的淘宝 npm 二进制镜像（npmmirror），
它镜像了 npm 包 ffmpeg-static 的官方发布件，每个程序一个 .gz：
  Windows  ffmpeg-win32-x64.gz + ffprobe-win32-x64.gz（两份各约 28MB）
  Mac/Linux 同名换平台词，两份各 18～28MB
境外源（gyan.dev / martin-riedl.de）留着当备源，只在主源整段不通时才用。

为什么要换源（11-16 实测，别再改回去）
--------------------------------------
原来只挂境外源：martin-riedl 实测 25～150 KB/s，一份 30MB 要 20 分钟；
Windows 用的 gyan.dev 只有 9 KB/s，114MB 要三个半小时——这不是慢，是没法用。
换到国内镜像后 700 KB/s～1.9 MB/s，两件并行下 1 分钟。数字在这儿，不是感觉。

放哪儿
------
数据目录下的 bin/（_env.tool() 本来就优先查这里，不用改查找顺序）。
一部机器下一次，所有剧、所有片子共用；重装专家包不用重下。

什么算成功
----------
下完要能真的跑起来：`ffmpeg -version` 有输出、版本号能读出来、主版本不低于
MIN_MAJOR。只把文件放到位置不算数——半截下载、架构不对、被杀软拦，
都会表现为「文件在、跑不动」，那种失败最难查。

下不动怎么办
------------
按源逐个试；全失败时把「手动放哪儿」和「到哪儿下」一起打出来。
降级规则：本机已经有旧版 ffmpeg 时，下载失败就用旧的继续干活，
但要明确告诉人「正在用几年前的版本」，而不是假装一切正常。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    from _env import data_root, tool
except Exception:  # 单独拷出去跑的时候

    def data_root() -> Path:  # type: ignore
        return Path.home() / "Documents" / "短剧工作区"

    def tool(name: str) -> str:  # type: ignore
        return shutil.which(name) or name


BIN_DIR = data_root() / "bin"
MARK = BIN_DIR / ".ffbin.json"

# 低于这个主版本就认为「太旧」：6.x（2023 年起）已经够用，不必追最新
MIN_MAJOR = 6

# 主源：淘宝的 npm 二进制镜像（国内直连）。
# 实测：境外源只有 9～150 KB/s，一份 30MB 要 20 分钟（Windows 那个 gyan.dev
# 更夸张，9 KB/s，114MB 要三个半小时）；换到这儿是 700 KB/s～1.9 MB/s，
# Windows 两件 59MB 一分钟下完。这条是实测数字，别随便换回境外源。
# 这里镜像的是 npm 包 ffmpeg-static 的官方发布件（evermeet / BtbN 系构建），
# 每个程序一个 .gz，解开就是可执行文件，不用解压缩包。
# 想升版本就改下面的 bXXXX 目录名（镜像里按版本分文件夹）。
_NPM = "https://registry.npmmirror.com/-/binary/ffmpeg-static/b6.1.1"

# 每个平台一组源，按优先级排。kind：
#   bundle = 一个压缩包里同时有 ffmpeg 和 ffprobe（备源用）
#   each   = 两个程序各下一个压缩包（备源用）
#   gz     = 两个程序各下一个 .gz（主源用，国内快）
SOURCES = {
    ("nt", "amd64"): [
        ("npmmirror", "gz", [
            ("ffmpeg", _NPM + "/ffmpeg-win32-x64.gz"),
            ("ffprobe", _NPM + "/ffprobe-win32-x64.gz"),
        ]),
        # 备源：境外，慢，只在主源整段不通时兜底
        ("gyan-essentials", "bundle", [
            "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        ]),
    ],
    ("darwin", "amd64"): [
        ("npmmirror", "gz", [
            ("ffmpeg", _NPM + "/ffmpeg-darwin-x64.gz"),
            ("ffprobe", _NPM + "/ffprobe-darwin-x64.gz"),
        ]),
        ("martin-riedl", "each", [
            ("ffmpeg", "https://ffmpeg.martin-riedl.de/redirect/latest/macos/amd64/release/ffmpeg.zip"),
            ("ffprobe", "https://ffmpeg.martin-riedl.de/redirect/latest/macos/amd64/release/ffprobe.zip"),
        ]),
    ],
    ("darwin", "arm64"): [
        ("npmmirror", "gz", [
            ("ffmpeg", _NPM + "/ffmpeg-darwin-arm64.gz"),
            ("ffprobe", _NPM + "/ffprobe-darwin-arm64.gz"),
        ]),
        ("martin-riedl", "each", [
            ("ffmpeg", "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffmpeg.zip"),
            ("ffprobe", "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffprobe.zip"),
        ]),
    ],
    ("posix", "amd64"): [
        ("npmmirror", "gz", [
            ("ffmpeg", _NPM + "/ffmpeg-linux-x64.gz"),
            ("ffprobe", _NPM + "/ffprobe-linux-x64.gz"),
        ]),
        ("martin-riedl", "each", [
            ("ffmpeg", "https://ffmpeg.martin-riedl.de/redirect/latest/linux/amd64/release/ffmpeg.zip"),
            ("ffprobe", "https://ffmpeg.martin-riedl.de/redirect/latest/linux/amd64/release/ffprobe.zip"),
        ]),
    ],
    ("posix", "arm64"): [
        ("npmmirror", "gz", [
            ("ffmpeg", _NPM + "/ffmpeg-linux-arm64.gz"),
            ("ffprobe", _NPM + "/ffprobe-linux-arm64.gz"),
        ]),
        ("martin-riedl", "each", [
            ("ffmpeg", "https://ffmpeg.martin-riedl.de/redirect/latest/linux/arm64/release/ffmpeg.zip"),
            ("ffprobe", "https://ffmpeg.martin-riedl.de/redirect/latest/linux/arm64/release/ffprobe.zip"),
        ]),
    ],
}

_QUIET = False


def _say(msg: str = "") -> None:
    if not _QUIET:
        print(msg, flush=True)


# ---------------------------------------------------------------- 平台判断


def _arch() -> str:
    m = platform.machine().lower()
    if m in ("arm64", "aarch64"):
        return "arm64"
    if m in ("x86_64", "amd64", "x64", "i386", "i686"):
        return "amd64"
    return m or "amd64"


def _plat() -> tuple[str, str]:
    """(系统类, 架构)。Windows 单独一类，其余合称 posix，源表里再区分。"""
    if os.name == "nt":
        return ("nt", _arch())
    sysname = platform.system().lower()
    key = "darwin" if sysname == "darwin" else "posix"
    return (key, _arch())


def _exe(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


# ---------------------------------------------------------------- 探活与版本


def version_of(path: str | Path) -> tuple[int, str]:
    """跑一次 -version，返回（主版本号, 原始版本串）。跑不动就是 (0, "")。"""
    if not path or not Path(path).exists():
        return (0, "")
    try:
        r = subprocess.run(
            [str(path), "-version"],
            capture_output=True, text=True, timeout=25,
        )
    except Exception:
        return (0, "")
    blob = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"version\s+n?(\d+)\.(\d+)", blob)
    if not m:
        return (0, "")
    return (int(m.group(1)), f"{m.group(1)}.{m.group(2)}")


def _usable_pair() -> tuple[bool, str, int]:
    """(两个程序都在且能跑, 说明, 主版本)。优先看缓存，再看系统。"""
    for name in ("ffmpeg", "ffprobe"):
        p = tool(name)
        major, ver = version_of(p)
        if not major:
            return (False, f"{name} 跑不起来（{p}）", 0)
        if name == "ffmpeg":
            ff_major, ff_ver = major, ver
    return (True, ff_ver, ff_major)


# ---------------------------------------------------------------- 下载


def _download(url: str, dest: Path, timeout: int = 600, label: str = "") -> bool:
    """下到 dest。返回是否成功。失败只打印一句原因，不抛异常。"""
    tag = f"{label} " if label else ""
    req = urllib.request.Request(url, headers={
        "User-Agent": "short-drama-trailer-editor/ffbin",
        "Accept": "*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            last_pct = -1
            while True:
                chunk = r.read(262144)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    pct = got * 100 // total
                    # 每涨 10% 报一声就够了；每 1.5 秒一行会把自检输出刷满
                    if pct >= last_pct + 10 or pct == 100:
                        last_pct = pct
                        _say(f"      {tag}{got/1048576:5.1f} / {total/1048576:.1f} MB  ({pct}%)")
        if total and dest.stat().st_size < total * 0.98:
            _say(f"      {tag}没下完：只要到 {dest.stat().st_size:,} / {total:,} 字节")
            return False
        return True
    except urllib.error.HTTPError as e:
        _say(f"      {tag}HTTP {e.code}，这个源不行")
    except Exception as e:
        _say(f"      {tag}{type(e).__name__}: {str(e)[:120]}")
    return False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def _verify_sha(url: str, path: Path) -> None:
    """有 .sha256 就校验，没有就跳过。校验不过直接抛，别把坏文件装进去。"""
    try:
        with urllib.request.urlopen(url + ".sha256", timeout=30) as r:
            want = r.read().decode("utf-8", "ignore").split()[0].strip().lower()
    except Exception:
        return
    if not re.fullmatch(r"[0-9a-f]{64}", want or ""):
        return
    got = _sha256(path)
    if got != want:
        raise ValueError(f"SHA256 对不上（期望 {want[:12]}… 实际 {got[:12]}…）")


def _extract(archive: Path, want: list[str], outdir: Path) -> dict[str, Path]:
    """从压缩包里挑出要的文件，摊平到 outdir。返回 {名字: 路径}。"""
    found: dict[str, Path] = {}
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                base = Path(info.filename).name
                for name in want:
                    if base == _exe(name) and name not in found:
                        target = outdir / base
                        with z.open(info) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        found[name] = target
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            for member in t.getmembers():
                if not member.isfile():
                    continue
                base = Path(member.name).name
                for name in want:
                    if base == _exe(name) and name not in found:
                        target = outdir / base
                        with t.extractfile(member) as src, open(target, "wb") as dst:  # type: ignore
                            shutil.copyfileobj(src, dst)
                        found[name] = target
    else:
        raise ValueError("这个包既不是 zip 也不是 tar")
    return found


def _fetch_gz(entries: list[tuple[str, str]], tmpdir: Path) -> dict[str, Path]:
    """每个程序一个 .gz，两路同时下。

    单路实测 700 KB/s 上下，两路能跑到 1 MB/s 出头，Windows 那 59MB 就从
    两分钟压到一分钟。串行下没必要，反正是两条独立连接。
    """
    got: dict[str, Path] = {}
    lock = threading.Lock()

    def one(name: str, url: str) -> None:
        arc = tmpdir / f"{name}.gz"
        if not _download(url, arc, label=name):
            return
        try:
            target = tmpdir / _exe(name)
            with gzip.open(arc, "rb") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            with lock:
                got[name] = target
        except Exception as e:
            _say(f"      {name} 解压失败：{type(e).__name__}: {str(e)[:100]}")
        finally:
            try:
                arc.unlink()
            except Exception:
                pass

    threads = [threading.Thread(target=one, args=(n, u), daemon=True) for n, u in entries]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return got


def _install(pairs: dict[str, Path]) -> None:
    """放进数据目录 bin/，加可执行权限，去掉 Mac 的隔离标记。"""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    for name, src in pairs.items():
        dst = BIN_DIR / _exe(name)
        if dst.exists():
            dst.unlink()
        shutil.move(str(src), str(dst))
        dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if os.name != "nt":
            subprocess.run(["xattr", "-d", "com.apple.quarantine", str(dst)],
                           capture_output=True)  # 不是 Mac 或没有该属性就静默失败


# ---------------------------------------------------------------- 主流程


def _write_mark(source: str, ver: str) -> None:
    try:
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        MARK.write_text(json.dumps({
            "source": source,
            "version": ver,
            "platform": "%s/%s" % _plat(),
            "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _hint() -> str:
    plat = _plat()
    where = BIN_DIR
    base = _NPM
    names = {
        ("nt", "amd64"): ("ffmpeg-win32-x64", "ffprobe-win32-x64"),
        ("darwin", "amd64"): ("ffmpeg-darwin-x64", "ffprobe-darwin-x64"),
        ("darwin", "arm64"): ("ffmpeg-darwin-arm64", "ffprobe-darwin-arm64"),
        ("posix", "amd64"): ("ffmpeg-linux-x64", "ffprobe-linux-x64"),
        ("posix", "arm64"): ("ffmpeg-linux-arm64", "ffprobe-linux-arm64"),
    }
    a, b = names.get(plat, names[("posix", "amd64")])
    ext = ".exe" if plat[0] == "nt" else ""
    return (f"手动补：浏览器打开 {base}/ 下载 {a}.gz 和 {b}.gz，"
            f"解开后改名成 ffmpeg{ext} 和 ffprobe{ext} 放进上面这个目录\n"
            f"      目录：{where}")


def _recent_fail() -> bool:
    """24 小时内下失败过就不再自动重试。

    不然每次开工自检都要等一遍超时，越等越像卡死。用 --force 可以强行再试。
    """
    if not MARK.exists():
        return False
    try:
        d = json.loads(MARK.read_text(encoding="utf-8"))
        if not d.get("failed_at"):
            return False
        ts = time.mktime(time.strptime(d["failed_at"], "%Y-%m-%d %H:%M:%S"))
        return (time.time() - ts) < 86400
    except Exception:
        return False


def _mark_fail(reason: str) -> None:
    try:
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        old = {}
        if MARK.exists():
            try:
                old = json.loads(MARK.read_text(encoding="utf-8"))
            except Exception:
                old = {}
        old.update({
            "failed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "failed_why": reason[:300],
        })
        MARK.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def ensure(force: bool = False, quiet: bool = False, use_system: bool = True) -> tuple[bool, str]:
    """确保 ffmpeg / ffprobe 能用。返回（可用，一句话说明）。

    force=True 时不管现状都重下；use_system=False 时即使本机有旧版也不将就
    （打包验证、想强制换新时用）。
    """
    global _QUIET
    _QUIET = quiet

    ok, msg, major = _usable_pair()

    # 够用就走人：缓存里的或系统里的，只要主版本不低于 MIN_MAJOR 就不折腾
    if ok and not force and major >= MIN_MAJOR:
        _say(f"  ffmpeg {msg} 已经有了")
        return (True, msg)

    if ok and not force:
        _say(f"  ffmpeg 是 {msg}，比 {MIN_MAJOR}.x 旧，换一份新的")

    # 刚失败过就别说「正在下载」了——直接说清现状，别让人白等五分钟
    if not force and _recent_fail():
        if ok:
            return (True, f"{msg}（今天下载没成过，先用本机这份旧的；"
                          f"想再试一次就删掉 {MARK}）")
        return (False, f"之前下载失败过，24 小时内不再自动重试\n      {_hint()}")

    plat = _plat()
    plan = SOURCES.get(plat) or SOURCES.get(("posix", plat[1]))
    if not plan:
        return (False, f"没有这个平台的下载源：{plat[0]}/{plat[1]}\n      {_hint()}")

    want = ["ffmpeg", "ffprobe"]
    BIN_DIR.mkdir(parents=True, exist_ok=True)

    for source, kind, entries in plan:
        _say(f"  从 {source} 下载（30–60MB，只下这一次，下完一直用，一两分钟）…")
        tmpdir = Path(tempfile.mkdtemp(prefix="ffbin_"))
        got: dict[str, Path] = {}
        try:
            if kind == "bundle":
                url = entries[0]
                arc = tmpdir / "pkg.zip"
                if not _download(url, arc):
                    continue
                got = _extract(arc, want, tmpdir)
            elif kind == "gz":
                got = _fetch_gz(entries, tmpdir)
            else:
                for name, url in entries:
                    arc = tmpdir / f"{name}.zip"
                    _say(f"    {name}")
                    if not _download(url, arc):
                        break
                    try:
                        _verify_sha(url, arc)
                    except ValueError as e:
                        _say(f"      {e}，换下一个源")
                        break
                    got.update(_extract(arc, [name], tmpdir))
            if len(got) < 2:
                _say("      包里没找齐 ffmpeg 和 ffprobe，换下一个源")
                continue

            _install(got)
            ok2, msg2, major2 = _usable_pair()
            if not ok2:
                _say(f"      放好了但跑不起来：{msg2}")
                continue
            if major2 < MIN_MAJOR:
                _say(f"      下到的还是 {msg2}，太旧，换下一个源")
                continue
            _write_mark(source, msg2)
            _say(f"  装好了：ffmpeg {msg2}，放在 {BIN_DIR}")
            return (True, msg2)
        except Exception as e:
            _say(f"      出错：{type(e).__name__}: {str(e)[:140]}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # 全失败
    _mark_fail("所有下载源都没成")
    if ok and use_system:
        return (True, f"{msg}（下载失败，先用本机这份旧的，装配可能遇到怪问题）")
    return (False, f"下不到 ffmpeg\n      {_hint()}")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="备好 ffmpeg / ffprobe")
    ap.add_argument("--force", action="store_true", help="已经在也重下")
    ap.add_argument("--check", action="store_true", help="只看状态，不下载")
    ap.add_argument("--json", action="store_true", help="机器读的输出")
    a = ap.parse_args()

    if a.check:
        ok, msg, major = _usable_pair()
        info = {
            "ok": ok,
            "version": msg if ok else None,
            "major": major,
            "fresh": major >= MIN_MAJOR,
            "ffmpeg": tool("ffmpeg"),
            "ffprobe": tool("ffprobe"),
            "cache": str(BIN_DIR),
            "platform": "%s/%s" % _plat(),
        }
        if a.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            print(f"ffmpeg  : {info['ffmpeg']}")
            print(f"ffprobe : {info['ffprobe']}")
            print(f"版本    : {info['version'] or '跑不起来'}")
            print(f"够不够新: {'是' if info['fresh'] else '否（低于 %d.x）' % MIN_MAJOR}")
            print(f"缓存目录: {info['cache']}")
        return 0 if ok else 1

    ok, msg = ensure(force=a.force)
    print(("可用：" if ok else "不可用：") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
