#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""集时长缓存：探一次，所有工具复用。

为什么要有这个：每条片子校验都要 ffprobe 一遍它用到的集。10 条片子共用 17 集，
就是 170 次 ffprobe，光等就一分钟多。而集时长是死数，探一次存下来就够，
以后只在集数变了（新剧、补集）才重探。

缓存落在剧库里：assets/short-drama/<剧名>/ep_durations.json
跟台词库放一起，因为它是同一类东西——这部剧的客观事实，不是某条片子的中间结果。

取的是「视频流时长」，不是容器时长。两者的差是音频尾巴，每集 0.0–0.8 秒不等，
拿容器时长当上限，段尾就会带黑帧。这条以前撞过四次。
"""
import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "..", ".."))
CACHE_VER = 3


def ffprobe_exe():
    try:  # 优先用包里自带的 bin/ffprobe（分发到别的机器时就在包里）
        from _env import ffprobe as _fp
        p = _fp()
        if os.path.isabs(p) and os.path.exists(p):
            return p
    except Exception:
        pass
    for p in ("/opt/anaconda3/bin/ffprobe", "/usr/local/bin/ffprobe"):
        if os.path.exists(p):
            return p
    return shutil.which("ffprobe") or "ffprobe"


def cache_path(drama):
    return os.path.join(ROOT, "assets", "short-drama", drama, "ep_durations.json")


def registered_src(drama):
    """config/drama-sources.json 里登记的素材目录（素材搬家只改这一处）。"""
    cfg = os.path.join(ROOT, "config", "drama-sources.json")
    if not os.path.exists(cfg):
        return None
    try:
        d = json.load(open(cfg, encoding="utf-8"))
    except Exception:
        return None
    node = d.get("dramas") or d
    v = node.get(drama) if isinstance(node, dict) else None
    if isinstance(v, str) and os.path.isdir(v):
        return v
    if isinstance(v, dict) and os.path.isdir(v.get("dir", "")):
        return v["dir"]
    return None


def asset_root_from_env():
    """素材根：先看环境变量，再看仓库根 .env。

    换机器时只需要改 .env 里的 SHORT_DRAMA_ASSET_ROOT 一行，
    技能代码不必再出现任何本机绝对目录（11-15 为分发改）。
    """
    v = os.environ.get("SHORT_DRAMA_ASSET_ROOT")
    if v and os.path.isdir(v):
        return v
    envf = os.path.join(ROOT, ".env")
    if os.path.exists(envf):
        try:
            for line in open(envf, encoding="utf-8"):
                line = line.strip()
                if line.startswith("SHORT_DRAMA_ASSET_ROOT") and "=" in line:
                    p = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if os.path.isdir(p):
                        return p
        except Exception:
            pass
    return None


def resolve_src(drama, hint=None):
    """素材目录：登记表优先，其次 plan 里写的路径（按剧名末段可纠正）。"""
    if hint:
        p = os.path.normpath(os.path.expandvars(str(hint)))
        if not os.path.isabs(p):
            p = os.path.normpath(os.path.join(ROOT, p))
        if os.path.isdir(p):
            return p
    got = registered_src(drama)
    if got:
        return got
    roots = []
    env_root = asset_root_from_env()
    if env_root:
        roots.append(env_root)
    # 本机历史目录只作兜底，新机器上通常走不到这里
    roots += ["/Users/zgl/选择", "/Users/zgl/短剧"]
    for root in roots:
        p = os.path.join(root, drama)
        if os.path.isdir(p):
            return p
    return None


def ep_of(fname):
    m = re.search(r"第(\d+)集", fname)
    if m:
        return int(m.group(1))
    stem = os.path.splitext(fname)[0]
    return int(stem) if stem.isdigit() else None


def scan_files(src_dir):
    """集号 -> 文件路径。"""
    out = {}
    if not src_dir or not os.path.isdir(src_dir):
        return out
    for f in os.listdir(src_dir):
        if not f.lower().endswith((".mp4", ".mkv", ".mov")):
            continue
        n = ep_of(f)
        if n is not None:
            out[n] = os.path.join(src_dir, f)
    return out


def _probe_one(job):
    ep, path, exe = job
    try:
        r = subprocess.run([exe, "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=duration", "-of", "csv=p=0", path],
                           capture_output=True, text=True, timeout=40)
        if r.returncode == 0 and r.stdout.strip():
            return ep, float(r.stdout.strip().splitlines()[0])
    except Exception:
        pass
    return ep, None


def ensure(drama, src_dir=None, eps=None, workers=8, verbose=False):
    """返回 {集号: 视频流秒数}。缺的并行探，探到的写回缓存。

    eps 给了就只保证这些集；没给就扫全目录。
    """
    src = resolve_src(drama, src_dir)
    cache = {}
    cp = cache_path(drama)
    if os.path.exists(cp):
        try:
            raw = json.load(open(cp, encoding="utf-8"))
            if raw.get("ver") == CACHE_VER:
                cache = {int(k): float(v) for k, v in (raw.get("durations") or {}).items()}
        except Exception:
            cache = {}

    files = scan_files(src)
    if not files:
        return cache
    want = sorted(files) if eps is None else sorted({int(e) for e in eps if int(e) in files})
    missing = [e for e in want if e not in cache]
    if missing:
        exe = ffprobe_exe()
        jobs = [(e, files[e], exe) for e in missing]
        with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
            for ep, dur in pool.map(_probe_one, jobs):
                if dur:
                    cache[ep] = round(dur, 3)
        try:
            os.makedirs(os.path.dirname(cp), exist_ok=True)
            json.dump({"ver": CACHE_VER, "src_dir": src,
                       "durations": {str(k): v for k, v in sorted(cache.items())}},
                      open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass
        if verbose:
            print(f"  探测 {len(missing)} 集，已写入 {os.path.relpath(cp, ROOT)}")
    return cache


def of(drama, ep, src_dir=None):
    c = ensure(drama, src_dir, eps=[ep])
    return c.get(int(ep))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="查看/重建集时长缓存")
    ap.add_argument("drama")
    ap.add_argument("--rebuild", action="store_true", help="丢掉缓存重探")
    args = ap.parse_args()
    if args.rebuild:
        p = cache_path(args.drama)
        if os.path.exists(p):
            os.remove(p)
    got = ensure(args.drama, verbose=True)
    tot = sum(got.values())
    print(f"{args.drama}：{len(got)} 集，合计 {tot:.1f}s = {tot/60:.1f} 分钟")
    for k in sorted(got):
        print(f"  ep{k:>2}  {got[k]:>7.2f}s")
