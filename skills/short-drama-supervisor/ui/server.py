#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短剧工作台网页（只读）。

为什么不用 FastAPI：一个人、一台机、本机打开，看的是几十个 JSON 文件，
Python 自带的 http.server 就够，项目里一个 web 框架都没装，不必为这个引进依赖。
真实项目里内部工具、本机看板就是这么起的。

**这一版只让看，不让点着干活。** 服务只处理 GET，别的请求一律回 405；
不启动任何脚本、不写任何数据文件、不改状态。要跑什么回对话里说。

启动：
    python3 server.py                 # 打开 http://127.0.0.1:8765
    python3 server.py --port 9000
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

UI_DIR = Path(__file__).resolve().parent
SKILL_DIR = UI_DIR.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))


def find_root() -> Path:
    """数据目录只认一个出口：scripts/_env.py（11-16 修，张老师测试机踩到）。

    以前这里从 __file__ 往上数 parents 找 runs/工作台.json——技能装进
    ~/.workbuddy/skills 或专家包的 skills/ 之后，那条链会一路翻到 C:\\ 或 /，
    永远碰不到工作台，服务一启动就 RuntimeError 退出；而启动时的输出又被
    上层丢进黑洞，最终表现只有一句「进度网页没起起来」，看不出原因。

    _env.data_root() 的查找顺序：环境变量 → 当前目录往上 → 技能位置往上 →
    默认的「文档/短剧工作区」。自动跑片时上层会把工作目录设成数据目录，
    手动起服务也照样能找到。
    """
    import _env  # noqa: E402  （上面已经把 scripts/ 放进 sys.path）

    return _env.data_root()


ROOT = find_root()
import progress  # noqa: E402
import status_overview  # noqa: E402
import drama_registry  # noqa: E402
import analysis_log  # noqa: E402
import asset_summary  # noqa: E402
try:  # 无人值守进度接口要用它的阶段表；导入失败只影响那一个面板
    import autopilot  # noqa: E402
except Exception:
    autopilot = None

ASSETS = ROOT / "assets" / "short-drama"
RUNS = ROOT / "runs"

# 素材分析做到多少算够——张老师 11-13 定的行业规则：一般分析全剧的 60%。
# 分母一律用「全剧集数」，不用「已动工集数」：后者会把只挑了前 19 集的剧
# 显示成快做完（60 集的剧挑 19 集，按已动工算 100%，实际只覆盖三分之一）。
COVERAGE_TARGET = 0.6


def source_root() -> Path:
    """原始视频放在哪，只从 .env 读，不写死本机路径。

    剧的唯一来源就是这个目录下的**一级文件夹**。分析产物目录
    （assets/short-drama）里混着 ai_assets 之类的非剧目录、还有一部剧两个名字，
    拿它当剧列表会把同一部剧算成两条管线（11-13 实测踩到）。
    """
    env = dict(os.environ)
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return Path(env.get("SHORT_DRAMA_ASSET_ROOT", str(ROOT / "素材")))


def source_dramas() -> list[str]:
    """只读素材根目录的一级文件夹名，不递归、不写入。"""
    src = source_root()
    if not src.is_dir():
        return []
    return sorted(p.name for p in src.iterdir()
                  if p.is_dir() and not p.name.startswith("."))


def source_ep_count(drama: str) -> int:
    """这剧有几集原视频。还没建库时用它当分母，不然进度条是 0/0 没法看。"""
    src = source_root() / drama
    if not src.is_dir():
        return 0
    return sum(1 for p in src.iterdir()
               if p.is_file() and p.suffix.lower() in (".mp4", ".mov", ".mkv"))


_JSON_CACHE: dict = {}
_JSON_LOCK = threading.Lock()
_MISS = object()


def jload(p: Path, default=None):
    """按文件修改时间缓存解析结果。

    实测这台机器上打开一个小 JSON 要 60 毫秒，一部剧 37 个文件就是 2 秒多，
    总览三部剧要 9 秒。文件内容按 mtime 变，改了自然重读，页面不会看到旧数。
    """
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
        with _JSON_LOCK:
            hit = _JSON_CACHE.get(key, _MISS)
        if hit is not _MISS:
            return hit
        data = json.loads(p.read_text(encoding="utf-8"))
        with _JSON_LOCK:
            _JSON_CACHE[key] = data
            for old in [k for k in _JSON_CACHE if k[0] == key[0] and k != key]:
                del _JSON_CACHE[old]
        return data
    except (OSError, json.JSONDecodeError):
        return default


# ---------------------------------------------------------------- mp4 元数据

_MP4_CACHE: dict = {}
_MP4_LOCK = threading.Lock()


def _iter_boxes(f, start: int, end: int):
    """按 MP4 box 头跳读，不把整个文件读进内存。"""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        hdr = f.read(16)
        if len(hdr) < 8:
            return
        size = int.from_bytes(hdr[:4], "big")
        typ = hdr[4:8]
        hsize = 8
        if size == 1:
            if len(hdr) < 16:
                return
            size = int.from_bytes(hdr[8:16], "big")
            hsize = 16
        elif size == 0:
            size = end - pos
        if size < hsize or pos + size > end:
            return
        yield typ, pos + hsize, pos + size
        pos += size


def _read_mp4_meta(p: Path) -> dict:
    """读 mvhd / tkhd 拿时长和分辨率。

    为什么不用 ffprobe：这台机器上一个 65MB 的 mp4 跑 ffprobe 要 5 秒（磁盘慢），
    看板一次要列几十个文件。box 头是几十字节，跳读三个小盒子就够。
    """
    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            moov = None
            for typ, ps, pe in _iter_boxes(f, 0, size):
                if typ == b"moov":
                    moov = (ps, pe)
                    break
            if not moov:
                return {}
            dur = width = height = None
            for typ, ps, pe in _iter_boxes(f, *moov):
                if typ == b"mvhd":
                    f.seek(ps)
                    b = f.read(32)
                    if len(b) >= 20:
                        if b[0] == 1 and len(b) >= 32:
                            ts = int.from_bytes(b[20:24], "big")
                            d = int.from_bytes(b[24:32], "big")
                        else:
                            ts = int.from_bytes(b[12:16], "big")
                            d = int.from_bytes(b[16:20], "big")
                        if ts:
                            dur = round(d / ts, 1)
                elif typ == b"trak":
                    for t2, ps2, pe2 in _iter_boxes(f, ps, pe):
                        if t2 != b"tkhd":
                            continue
                        f.seek(ps2)
                        b = f.read(100)
                        off = 88 if b and b[0] == 1 else 76
                        if len(b) >= off + 8:
                            width = int.from_bytes(b[off:off + 4], "big") / 65536
                            height = int.from_bytes(b[off + 4:off + 8], "big") / 65536
                        break
                if dur is not None and width:
                    break
            return {"duration": dur,
                    "w": int(width) if width else None,
                    "h": int(height) if height else None}
    except (OSError, ValueError):
        return {}


def mp4_meta(p: Path) -> dict:
    try:
        st = p.stat()
    except OSError:
        return {}
    key = (str(p), st.st_mtime_ns, st.st_size)
    with _MP4_LOCK:
        hit = _MP4_CACHE.get(key, _MISS)
    if hit is not _MISS:
        return hit
    data = _read_mp4_meta(p)
    with _MP4_LOCK:
        _MP4_CACHE[key] = data
        for old in [k for k in _MP4_CACHE if k[0] == key[0] and k != key]:
            del _MP4_CACHE[old]
    return data


def _frames_url(p: Path) -> str:
    return "/frames/" + quote(str(p.relative_to(RUNS)))


def _video_url(rel: Path) -> str:
    return "/video/" + quote(str(rel))


# ---------------------------------------------------------------- 数据接口

def api_overview() -> dict:
    """状态总览：直接调主管自己的算法，不另算一遍。"""
    return status_overview.overview_json(str(RUNS))


def api_progress() -> dict:
    items = progress.read_all()
    running = [x for x in items if x.get("state") == "running" and not x.get("suspect")]
    suspects = [x for x in items if x.get("suspect")]
    return {"items": items, "running_count": len(running), "suspect_count": len(suspects)}


def api_autopilot() -> dict:
    """无人值守实时进度：走到第几步、当前在跑什么、心跳还活着没有。

    为什么单独开一个接口而不是混进 /api/progress：那个是「哪个脚本在跑」的流水账，
    这个是「整条流水线走到哪」的账。无人值守挂一晚上，第二天要一眼看出
    「跑完了 / 还在跑 / 半夜死了」，三句话不能靠翻流水账拼出来。
    """
    try:
        stages_def = [(k, lb, kd) for k, lb, kd in autopilot.STAGES if k != "report"]
    except Exception:
        stages_def = []

    books = []
    for logp in RUNS.glob("*/_autopilot/日志.json"):
        try:
            d = json.loads(logp.read_text(encoding="utf-8"))
        except Exception:
            continue
        drama = d.get("drama") or logp.parent.parent.name
        stages = d.get("stages") or {}
        row = {
            "drama": drama,
            "updated_at": d.get("updated_at") or "",
            "authorized": d.get("authorized") or {},
            "stages": [
                {
                    "key": k,
                    "label": lb,
                    "kind": kd,
                    "status": (stages.get(k) or {}).get("status") or "todo",
                    "detail": (stages.get(k) or {}).get("detail") or "",
                    "seconds": (stages.get(k) or {}).get("seconds"),
                    "at": (stages.get(k) or {}).get("at") or "",
                }
                for k, lb, kd in stages_def
            ],
        }
        rep = logp.parent / "汇总报告.md"
        if rep.exists():
            row["report_url"] = "/doc/" + quote(str(rep.relative_to(ROOT)))
        books.append(row)

    # 关联实时心跳：这一趟正在跑的脚本，以及「标着在跑但没动静」的疑似卡死
    try:
        live = progress.read_all()
    except Exception:
        live = []
    for b in books:
        mine = [x for x in live if x.get("drama") == b["drama"]]
        b["live"] = [x for x in mine if x.get("state") == "running" and not x.get("suspect")]
        b["suspect"] = [x for x in mine if x.get("suspect")]

    books.sort(key=lambda b: b.get("updated_at") or "", reverse=True)
    return {
        "books": books,
        "server_time": datetime.now().strftime("%H:%M:%S"),
        # 出问题时先看这三行：服务读的是哪个目录、从哪个文件起来的
        "where": {"runs": str(RUNS), "cwd": os.getcwd(), "file": str(Path(__file__).resolve())},
    }


def drama_alias() -> dict[str, str]:
    """剧名对照：工作台记的是短名，素材目录可能用全名。

    例：八零憨妻 → assets/short-drama/八零憨妻的甜蜜日常。
    不对照就会把同一部剧算成两条管线（11-13 实测踩到）。
    """
    ws = jload(RUNS / "工作台.json", {}) or {}
    out = {}
    for short, info in (ws.get("dramas") or {}).items():
        ad = (info or {}).get("asset_dir") or ""
        if ad:
            out[Path(ad).name] = short
    return out


def api_dramas() -> list[str]:
    """剧列表只认素材根目录的一级文件夹，别的目录一概不扫。"""
    return source_dramas()


# ---------------------------------------------------------------- 剧注册表
REGISTRY = RUNS / "剧注册表.json"


def load_registry() -> dict:
    return jload(REGISTRY, {}) or {}


def canon_drama(text: str) -> str:
    """接口也认简称：?drama=父爱剧 能对到《父爱跨越山海》。

    对不上就原样返回，让下游照旧报「没建库」，不要在接口层替人猜。
    """
    m = drama_registry.match_drama(text, load_registry())
    return m["name"] if m.get("ok") else text


def registry_entries() -> list[dict]:
    """按 drama_order 排；未登记的素材根剧默认 active，排在最后。

    code 只在活动剧上有效；归档剧用 archived_code，保留归档那一刻的号。
    """
    reg = load_registry()
    order = reg.get("drama_order") or []
    infos = reg.get("dramas") or {}
    names = list(order)
    for n in sorted(set(source_dramas()) | set(infos)):
        if n not in names:
            names.append(n)
    out = []
    for name in names:
        info = infos.get(name) or {}
        status = info.get("status") or "active"
        code = info.get("archived_code") if status == "archived" else info.get("code")
        out.append({
            "name": name,
            "status": status,
            "code": code,
            "archived_code": info.get("archived_code"),
            "archived_at": info.get("archived_at") or "",
            # 简称给页面的搜索框用：输「父爱」「鸡排」也能找得到
            "aliases": info.get("aliases") or [],
        })
    return out


def drama_meta(name: str) -> dict:
    for e in registry_entries():
        if e["name"] == name:
            return e
    return {"name": name, "status": "active", "code": None,
            "archived_code": None, "archived_at": ""}


def names_for_scope(scope: str = "active") -> list[str]:
    if scope == "archived":
        return [e["name"] for e in registry_entries() if e["status"] == "archived"]
    return [e["name"] for e in registry_entries() if e["status"] != "archived"]


def api_registry() -> dict:
    ents = registry_entries()
    return {
        "dramas": ents,
        "counts": {
            "active": sum(1 for e in ents if e["status"] != "archived"),
            "archived": sum(1 for e in ents if e["status"] == "archived"),
        },
    }


def asset_dir_of(drama: str) -> Path | None:
    alias = drama_alias()
    ws = jload(RUNS / "工作台.json", {}) or {}
    info = (ws.get("dramas") or {}).get(drama) or {}
    if info.get("asset_dir"):
        p = ROOT / info["asset_dir"]
        if p.exists():
            return p
    p = ASSETS / drama
    if p.exists():
        return p
    # 短名找不到，试试有没有哪个全名映射到它
    for full, short in alias.items():
        if short == drama:
            p = ASSETS / full
            if p.exists():
                return p
    return None


def api_assets(drama: str) -> dict:
    """素材进度：按集看台词库、镜头标签、句子视图齐不齐。

    这一页不依赖脚本上报，读的是已经落盘的产物，所以历史数据也能看。
    drama 可以是简称（父爱剧），先对成正式剧名再找目录。
    """
    drama = canon_drama(drama)
    base = asset_dir_of(drama)
    if not base:
        vc = source_ep_count(drama)
        return {"drama": drama, "error": "这部剧还没建库",
                "ep_count": 0, "ready_count": 0, "eps": [], "char_map": {},
                "video_count": vc,
                "cover_pct": 0,
                "target_eps": math.ceil(vc * COVERAGE_TARGET) if vc else 0,
                "target_pct": round(COVERAGE_TARGET * 100),
                "subs": [], "subs_ok": 0, "subs_total": 8, "pct": 0,
                "summary": asset_summary.build(drama, [], vc, 0,
                                               analysis_log.bundle(drama), 0)}

    def ep_of(name: str):
        m = re.search(r"ep(\d+)", name)
        return int(m.group(1)) if m else None

    lines, shots, sentences = {}, {}, {}
    for p in (base / "lines").glob("lines_ep*.json"):
        ep = ep_of(p.name)
        if ep is not None:
            lines[ep] = p
    for p in (base / "shots").glob("shots_ep[0-9][0-9].json"):
        ep = ep_of(p.name)
        if ep is not None:
            shots[ep] = p
    for p in (base / "sentences").glob("sentences_ep*.json"):
        ep = ep_of(p.name)
        if ep is not None:
            sentences[ep] = p

    eps = []
    for ep in sorted(set(lines) | set(shots) | set(sentences)):
        ln = jload(lines.get(ep), {}) if ep in lines else {}
        sh = jload(shots.get(ep), {}) if ep in shots else {}
        sv = jload(sentences.get(ep), {}) if ep in sentences else {}
        lc = ln if isinstance(ln, list) else (ln.get("lines") or [])
        rows = sv.get("rows") or []
        emo = sum(1 for r in rows if (r.get("emotion") or "").strip())
        named = sum(1 for r in rows if (r.get("name") or "").strip())
        eps.append({
            "ep": ep,
            "has_lines": ep in lines,
            "has_shots": ep in shots,
            "has_sentences": ep in sentences,
            "line_count": len(lc),
            "shot_count": sh.get("shot_count") or len(sh.get("shots") or []),
            "sentence_count": len(rows),
            "emotion_count": emo,
            "named_count": named,
            "off_screen": sum(1 for r in rows if r.get("kind") == "off_screen"),
            "dense_shots": len(sv.get("dense_shots") or []),
            "shot_version": sh.get("prompt_version") or "",
        })

    ready = sum(1 for e in eps if e["has_lines"] and e["has_shots"] and e["has_sentences"])
    video_count = max(source_ep_count(drama), len(eps))
    target = math.ceil(video_count * COVERAGE_TARGET) if video_count else 0
    cover = round(len(eps) / video_count * 100, 1) if video_count else 0
    # 用量：走 jload 的缓存，否则 17 个文件读一遍就多等 1 秒（这台机器开一个文件 60 毫秒）
    tokens = sum(int(((jload(p, {}) or {}).get("usage") or {}).get("total_tokens") or 0)
                 for p in (base / "shots").glob("shots_ep[0-9][0-9].json"))
    # 概况：耗时 + 用量 + 质量分。耗时来自 analysis_log（脚本记的或事后补记的），
    # 没有记录就如实说没有——不拿文件时间戳去猜，猜出来的数比没有更坏。
    summary = asset_summary.build(drama, eps, video_count, cover,
                                  analysis_log.bundle(drama), tokens)
    # 素材分析不是一个进度，是 8 件事：有的按集跑（台词/切口/切段/打标/边界帧/句级表），
    # 有的整部剧只做一次（体检/人名映射），速度差几十倍。混成一个百分比会谎报完成——
    # 打标只跑了 8 集，主数字已经 60%（11-16 拆开）。
    def _eps_count(d: Path, pat: str) -> int:
        seen = set()
        for p in d.glob(pat):
            m = re.search(r"ep_?(\d+)", p.name)
            if m:
                seen.add(int(m.group(1)))
        return len(seen)

    target_eps = target or 1
    cuts_n = _eps_count(base / "cuts", "*.json")
    sk = jload(base / "segments_skeleton.json", {})
    eps_map = (sk.get("episodes") if isinstance(sk, dict) else None) or {}
    seg_n = len(eps_map) if isinstance(eps_map, dict) else 0
    tagged_n = 0
    for _ep, _p in shots.items():
        _doc = jload(_p, {}) or {}
        if any((s.get("tag") or {}) for s in (_doc.get("shots") or [])):
            tagged_n += 1
    man = jload(base / "edge_frames" / "manifest.json", {})
    edge_n = len(man) if isinstance(man, dict) else 0
    cmap = jload(base / "char_map.json", {})
    cmap_n = len((cmap.get("chars") if isinstance(cmap, dict) else None) or cmap or {})

    def sub(key, label, done, total):
        total = total or 0
        done = min(done, total) if total else done
        return {
            "key": key, "label": label, "done": done, "total": total,
            "pct": round(done / total * 100) if total else 0,
            "ok": bool(total) and done >= total,
        }

    subs = [
        sub("check", "素材体检（原片能不能读）", 1 if video_count else 0, 1),
        sub("lines", "台词时间轴（OCR 认字幕）", len(lines), target_eps),
        sub("cuts", "切口停顿表（找自然刀口）", cuts_n, target_eps),
        sub("segment", "冲突段落切分", seg_n, target_eps),
        sub("tag", "镜头画面打标（VLM 看图）", tagged_n, target_eps),
        sub("charmap", "外观对人名", 1 if cmap_n else 0, 1),
        sub("edge", "边界帧扫描", edge_n, target_eps),
        sub("index", "查询库与句级表", len(sentences), target_eps),
    ]
    subs_ok = sum(1 for s in subs if s["ok"])
    subs_pct = round(sum(s["pct"] for s in subs) / len(subs)) if subs else 0

    return {
        "drama": drama, "ep_count": len(eps), "ready_count": ready, "eps": eps,
        "video_count": video_count,
        "cover_pct": cover,
        "target_eps": target,
        "target_pct": round(COVERAGE_TARGET * 100),
        "subs": subs,
        "subs_ok": subs_ok,
        "subs_total": len(subs),
        "pct": subs_pct,
        "summary": summary,
        "char_map": jload(base / "char_map.json", {}),
    }


def api_plans() -> dict:
    """列出所有带剪辑清单的片子，供分镜台下拉选。"""
    out = []
    for p in sorted(RUNS.glob("*/*/plan.json")):
        d = jload(p, {})
        if not isinstance(d, dict):
            continue
        segs = d.get("segs") or []
        out.append({
            "drama": p.parent.parent.name,
            "task": p.parent.name,
            "path": str(p),
            "title": d.get("title") or p.parent.name,
            "angle": d.get("angle") or "",
            "ai_strategy": d.get("ai_strategy") or "none",
            "seg_count": len(segs),
            "duration": round(sum(float(s.get("b", 0)) - float(s.get("a", 0))
                                  for s in segs), 1),
            "src_dir": d.get("src_dir") or "",
            # 有首尾帧才能在分镜台看图；老片子的原视频不在这台机器上，抽不出来
            "card_count": len(list((p.parent / "cards").glob("*.jpg"))),
        })
    return {"plans": out}


def api_plan(drama: str, task: str) -> dict:
    drama = canon_drama(drama)
    p = RUNS / drama / task / "plan.json"
    d = jload(p, {})
    if not d:
        return {"error": "找不到这条片子的剪辑清单"}
    segs = []
    for s in d.get("segs") or []:
        label = str(s.get("label") or "")
        segs.append({
            "label": label,
            "type": s.get("type") or "",
            "ep": s.get("ep"),
            "a": s.get("a"), "b": s.get("b"),
            "dur": round(float(s.get("b", 0)) - float(s.get("a", 0)), 2),
            "beats": s.get("beats") or "",
            "card_a": f"/frames/{drama}/{task}/cards/{label}_a.jpg",
            "card_b": f"/frames/{drama}/{task}/cards/{label}_b.jpg",
        })
    return {
        "drama": drama, "task": task,
        "title": d.get("title") or task,
        "angle": d.get("angle") or "",
        "ai_strategy": d.get("ai_strategy") or "none",
        "total": round(sum(x["dur"] for x in segs), 1),
        "segs": segs,
    }


def api_shared() -> dict:
    ws = jload(RUNS / "工作台.json", {}) or {}
    cards = []
    menu = RUNS / "玩法菜单"
    if menu.exists():
        for p in sorted(menu.glob("*.md")):
            txt = p.read_text(encoding="utf-8", errors="replace")
            head = next((l.lstrip("# ").strip() for l in txt.splitlines()
                         if l.strip().startswith("#")), p.stem)
            cards.append({"name": p.stem, "title": head,
                          "lines": len(txt.splitlines())})
    return {"shared": ws.get("shared") or {}, "menu": cards,
            "active": ws.get("active") or {}}


# ---------------------------------------------------------------- 按剧汇总的三阶段管线

def list_videos() -> list[dict]:
    """本机所有 mp4，带上时长和分辨率。

    成片 / 旧版 / 中间文件不在这里判，交给 api_videos 按状态文件里的 trailer.path
    说话——同一个文件名在不同片子里角色不同，单看文件名会判错。
    """
    out = []
    for p in sorted(RUNS.rglob("*.mp4")):
        rel = p.relative_to(RUNS)
        parts = rel.parts
        if len(parts) < 3:
            continue
        drama, task = parts[0], parts[1]
        in_build = "build" in parts[2:-1]
        try:
            st = p.stat()
        except OSError:
            continue
        meta = mp4_meta(p)
        out.append({
            "drama": drama, "task": task,
            "name": p.name,
            "rel": str(rel),
            "in_build": in_build,
            "final": (not in_build) and p.name.startswith("sd_"),
            "kind": ("装配中间文件" if in_build else
                     "成片" if p.name.startswith("sd_") else "AI 素材段 / 验证片"),
            "duration": meta.get("duration"),
            "w": meta.get("w"), "h": meta.get("h"),
            "size_mb": round(st.st_size / 1024 / 1024, 1),
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"),
            "mtime_ts": st.st_mtime,
            "url": _video_url(rel),
        })
    return out


def _task_preview_meta(drama: str, task: str) -> dict:
    """一条片子的预览上下文：计划、当前成片路径、段落首尾帧、自检图。"""
    base = RUNS / drama / task
    status = jload(base / "状态.json", {}) or {}
    arts = status.get("artifacts") if isinstance(status.get("artifacts"), dict) else {}
    script = arts.get("script") if isinstance(arts.get("script"), dict) else {}
    trailer = arts.get("trailer") if isinstance(arts.get("trailer"), dict) else {}

    plan_p = None
    sp = script.get("path") or ""
    if sp:
        cand = ROOT / sp
        if cand.exists():
            plan_p = cand
    if plan_p is None:
        for name in ("plan_v2.json", "plan.json"):
            cand = base / name
            if cand.exists():
                plan_p = cand
                break
    plan = jload(plan_p, {}) if plan_p else {}
    if not isinstance(plan, dict):
        plan = {}

    tp = trailer.get("path") or ""
    trailer_rel = ""
    if tp:
        cand = Path(tp) if os.path.isabs(tp) else ROOT / tp
        try:
            trailer_rel = str(cand.resolve().relative_to(RUNS.resolve()))
        except (ValueError, OSError):
            trailer_rel = ""

    cdir = base / "cards"
    segs = []
    for s in plan.get("segs") or []:
        if not isinstance(s, dict):
            continue
        label = str(s.get("label") or "")
        a = cdir / f"{label}_a.jpg"
        b = cdir / f"{label}_b.jpg"
        typ = s.get("type") or ""
        try:
            if s.get("d") is not None and s.get("a") is None:
                dur = round(float(s["d"]), 2)   # 黑场 / 字幕卡按设计时长
            else:
                dur = round(float(s.get("b", 0)) - float(s.get("a", 0)), 2)
        except (TypeError, ValueError):
            dur = None
        segs.append({
            "label": label, "type": typ, "ep": s.get("ep"),
            "a": s.get("a"), "b": s.get("b"), "dur": dur,
            "beats": s.get("beats") or "",
            "card_a": _frames_url(a) if a.exists() else "",
            "card_b": _frames_url(b) if b.exists() else "",
        })

    selfchecks = []
    bdir = base / "build"
    if bdir.is_dir():
        for p in sorted(bdir.iterdir()):
            if not p.is_file() or p.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            if p.name.startswith("build_black") or p.name == "card.png":
                continue
            selfchecks.append({"name": p.name, "url": _frames_url(p)})

    return {
        "plan_path": str(plan_p.relative_to(ROOT)) if plan_p and ROOT in plan_p.parents else "",
        "title": plan.get("title") or task,
        "angle": plan.get("angle") or plan.get("_选题") or status.get("intent") or "",
        "rev": plan.get("rev") or trailer.get("rev"),
        "trailer_rel": trailer_rel,
        "trailer_name": Path(trailer_rel).name if trailer_rel else "",
        "trailer_exists": bool(trailer_rel) and (RUNS / trailer_rel).exists(),
        "trailer_state": trailer.get("state") or "unlogged",
        "trailer_rev": trailer.get("rev"),
        "script_state": script.get("state") or "unlogged",
        "cycle_round": status.get("cycle_round") or 1,
        "segs": segs,
        "selfchecks": selfchecks,
    }


def _video_role(v: dict, trailer_rel: str) -> str:
    name = v["name"]
    if trailer_rel and v["rel"] == trailer_rel:
        return "当前成片"
    if trailer_rel and (re.match(r"^v\d+\.mp4$", name)
                        or re.search(r"_v\d+\.mp4$", name)):
        return "旧版"
    if name.startswith("sd_"):
        return "成片"
    if v["in_build"]:
        return "装配中间文件"
    return "AI 素材段 / 验证片"


_ROLE_ORDER = {"当前成片": 0, "成片": 1, "旧版": 2, "装配中间文件": 3,
               "AI 素材段 / 验证片": 4}


def api_videos(scope: str = "active") -> dict:
    """按「剧 / 片子」分组，一组就是一个预览单元。

    旧版页面把 38 个 mp4 平铺成一张列表，用户得靠文件名猜哪条是成片。
    这里按状态文件里的 trailer.path 认当前成片，再按片子分组，前面只放人要看的。
    scope=active 只看活动剧；archived 只看归档剧。
    """
    allowed = set(names_for_scope(scope))
    vids = [v for v in list_videos() if v["drama"] in allowed]
    by: dict[tuple[str, str], list[dict]] = {}
    for v in vids:
        by.setdefault((v["drama"], v["task"]), []).append(v)

    order = {e["name"]: i for i, e in enumerate(registry_entries())}
    groups = []
    for (drama, task), items in sorted(
            by.items(), key=lambda kv: (order.get(kv[0][0], 999), kv[0][1])):
        meta = _task_preview_meta(drama, task)
        dmeta = drama_meta(drama)
        trel = meta["trailer_rel"]
        for v in items:
            v["role"] = _video_role(v, trel)
            v["is_current"] = v["role"] == "当前成片"
        items.sort(key=lambda v: (_ROLE_ORDER[v["role"]], -v["mtime_ts"]))
        current = next((v for v in items if v["is_current"]), None)
        groups.append({
            "key": f"{drama}/{task}",
            "drama": drama, "task": task,
            "code": dmeta["code"], "status": dmeta["status"],
            "archived_at": dmeta["archived_at"],
            "title": meta["title"], "angle": meta["angle"],
            "rev": meta["rev"],
            "trailer_state": meta["trailer_state"],
            "trailer_rev": meta["trailer_rev"],
            "trailer_name": meta["trailer_name"],
            "trailer_exists": meta["trailer_exists"],
            "script_state": meta["script_state"],
            "cycle_round": meta["cycle_round"],
            "current": current["url"] if current else (items[0]["url"] if items else ""),
            "has_current": bool(current),
            "versions": items,
            "segs": meta["segs"],
            "selfchecks": meta["selfchecks"],
        })

    final = [v for v in vids if v["role"] in ("当前成片", "成片")]
    ws = jload(RUNS / "工作台.json", {}) or {}
    active = ws.get("active") or {}
    if active.get("drama") not in allowed:
        active = {}
    return {
        "groups": groups, "videos": vids, "final": final,
        "active": active,
        "group_count": len(groups),
        "current_count": sum(1 for g in groups if g["has_current"]),
        "final_count": len(final),
        "old_count": sum(1 for v in vids if v["role"] == "旧版"),
        "build_count": sum(1 for v in vids if v["role"] == "装配中间文件"),
    }


def _task_dirs(drama: str) -> list[Path]:
    """一部剧下面真正的「片子」目录。排除 specs* 这类资料目录。"""
    base = RUNS / drama
    if not base.is_dir():
        return []
    return [p for p in base.iterdir()
            if p.is_dir() and not p.name.startswith((".", "specs"))]


def _plan_file(base: Path) -> Path | None:
    """一条片子可能叫 plan.json / plan_v2.json / plan_v3ws.json。"""
    for name in ("plan.json", "plan_v2.json", "plan_v3ws.json"):
        p = base / name
        if p.exists():
            return p
    cands = sorted(base.glob("plan*.json"))
    return cands[0] if cands else None


def api_pipelines(scope: str = "active") -> dict:
    """一部剧一条管线，三个阶段的进度。

    后两段不是「一个开关」，是一部剧底下**一批片子**各自的进度：
    有的等人过目、有的已成片、有的改了清单要重拼。所以这里给的是数，不是勾。
    scope=active 只看活动剧；archived 只看归档剧。
    """
    ov = api_overview()
    by_task = {(t["drama"], t["task"]): t for t in ov["tasks"]}
    plans = api_plans()["plans"]
    # 出片进度按「状态文件里写了当前成片路径」算，与预览页同一口径。
    # 不能只看 sd_*.mp4：D1/D2 的成片在 build/vN.mp4 下，文件名不带 sd_。
    pv_groups = {(g["drama"], g["task"]): g
                 for g in api_videos(scope)["groups"]}

    names = names_for_scope(scope)
    historical = sorted({p["drama"] for p in plans} - set(source_dramas()))

    out = []
    for drama in names:
        meta = drama_meta(drama)
        a = api_assets(drama)
        task_dirs = _task_dirs(drama)
        plan_tasks = [p for p in task_dirs if _plan_file(p)]
        task_count = len(task_dirs)       # 这部戏排了多少条片子
        script_count = len(plan_tasks)    # 其中已经出了文案的

        waiting, passed, revise, to_mix, to_remix = [], [], [], [], []
        for p in plan_tasks:
            name = p.name
            t = by_task.get((drama, name))
            if not t:
                # 有清单、没登记状态的，按「等人过目」算
                waiting.append(name)
                continue
            sc = next((s for s in t["stages"] if s["key"] == "script"), {})
            tr = next((s for s in t["stages"] if s["key"] == "trailer"), {})
            if "script" in t["reasons"]:
                revise.append(name)
            elif sc.get("state") == "approved":
                passed.append(name)
            elif sc.get("state") == "superseded":
                pass
            else:
                waiting.append(name)
            if "trailer" in t["reasons"]:
                to_remix.append(name)
            elif tr.get("state") not in ("done", "approved", "superseded"):
                to_mix.append(name)

        eps = a.get("eps") or []
        video_count = a.get("video_count") or len(eps)
        # 分母用「全剧集数」：够不够看的是覆盖率，不是把挑出来的那几集做得多完整。
        # 达标线 60%（COVERAGE_TARGET），比如 60 集的剧要分析到 36 集才够。
        eps_total = max(video_count, len(eps))
        line_total = sum(e["line_count"] for e in eps)
        sentences = sum(e["sentence_count"] for e in eps)
        named = sum(e["named_count"] for e in eps)
        emos = sum(e["emotion_count"] for e in eps)
        lines_eps = sum(1 for e in eps if e["has_lines"])
        shots_eps = sum(1 for e in eps if e["has_shots"])
        sent_eps = sum(1 for e in eps if e["has_sentences"])
        # 每集要交三份资料（台词、镜头标注、合表），按份数算，分母是 全剧集数 × 3。
        parts_done = lines_eps + shots_eps + sent_eps
        parts_total = eps_total * 3
        pct1 = round(parts_done / parts_total * 100) if parts_total else 0
        # 覆盖率单算一个：已建台词的集数 ÷ 全剧集数，跟「60% 够不够」这条线直接比。
        cover_pct = round(lines_eps / eps_total * 100, 1) if eps_total else 0
        target_eps = math.ceil(eps_total * COVERAGE_TARGET)

        script_pct = round(script_count / task_count * 100) if task_count else 0
        review_pct = round(len(passed) / script_count * 100) if script_count else 0
        vf = [p for p in plan_tasks
              if pv_groups.get((drama, p.name), {}).get("has_current")]
        final_pct = round(len(vf) / script_count * 100) if script_count else 0

        active = (ov["active"] or {})
        out.append({
            "drama": drama,
            "active": active.get("drama") == drama,
            "code": meta["code"],
            "status": meta["status"],
            "archived_at": meta["archived_at"],
            "stage1": {
                "label": "素材分析",
                "ep_count": a.get("ep_count", 0),
                "ready_count": a.get("ready_count", 0),
                "video_count": video_count,
                "lines_eps": lines_eps, "shots_eps": shots_eps, "sent_eps": sent_eps,
                "parts_done": parts_done, "parts_total": parts_total,
                "cover_pct": cover_pct,
                "target_eps": target_eps,
                "target_pct": round(COVERAGE_TARGET * 100),
                "cover_ok": lines_eps >= target_eps and eps_total > 0,
                "lines": line_total,
                "sentences": sentences,
                "named_pct": round(named / sentences * 100, 1) if sentences else 0,
                "emotion_pct": round(emos / sentences * 100, 1) if sentences else 0,
                "dense": sum(e["dense_shots"] for e in eps),
                # 主数字按 8 道工序算（素材分析不是一个进度）。pct_parts 是老的
                # 「三份资料份数」口径，留着做明细，不再当主数字（11-16）。
                "pct": a.get("pct", pct1),
                "pct_parts": pct1,
                "subs": a.get("subs", []),
                "subs_ok": a.get("subs_ok", 0),
                "subs_total": a.get("subs_total", 8),
                # 8 项全满才算过：有一项没满，下游拿到的数据就是缺的
                "done": bool(a.get("subs_total")) and a.get("subs_ok") == a.get("subs_total"),
            },
            "stage2": {
                "label": "文案",
                "count": script_count,
                "total": task_count,
                "pct": script_pct,
            },
            "review": {
                "label": "审核",
                "passed": passed, "waiting": waiting, "revise": revise,
                "total": script_count,
                "pct": review_pct,
            },
            "stage3": {
                "label": "出片",
                "final_count": len(vf),
                "to_mix": to_mix, "to_remix": to_remix,
                "total": script_count,
                "pct": final_pct,
            },
        })
    return {"pipelines": out, "generated_at": ov["generated_at"],
            "active": ov["active"],
            "source_root": str(source_root()),
            "historical": historical}


REVIEW_DOCS = ("清单过目.md", "清单人审表.md", "送审说明.md",
               "方向卡.md", "选段说明.md", "spec.json")


def api_reviews(scope: str = "active") -> dict:
    """待审文案：把等人过目的清单挑出来，附上看得到内容的文档。"""
    allowed = set(names_for_scope(scope))
    by_task = {(t["drama"], t["task"]): t for t in api_overview()["tasks"]}
    out = []
    for p in sorted(RUNS.glob("*/*/plan.json")):
        drama, task = p.parent.parent.name, p.parent.name
        if drama not in allowed:
            continue
        dmeta = drama_meta(drama)
        d = jload(p, {})
        docs = []
        for f in sorted(p.parent.glob("*.md")) + sorted(p.parent.glob("spec.json")):
            docs.append({"name": f.name,
                         "size": f.stat().st_size,
                         "mtime": datetime.fromtimestamp(f.stat().st_mtime)
                                   .strftime("%m-%d %H:%M"),
                         "url": "/api/doc?path=" + quote(str(f.relative_to(RUNS)))})

        t = by_task.get((drama, task))
        sc = next((s for s in t["stages"] if s["key"] == "script"), {}) if t else {}
        if t and "script" in t["reasons"]:
            state = "要改"
        elif sc.get("state") == "approved":
            state = "已通过"
        elif sc.get("state") == "superseded":
            state = "已废弃"
        elif not t:
            state = "没登记状态"
        else:
            state = "等人过目"
        out.append({
            "drama": drama, "task": task,
            "code": dmeta["code"], "status": dmeta["status"],
            "title": d.get("title") or task,
            "angle": d.get("angle") or "",
            "seg_count": len(d.get("segs") or []),
            "state": state,
            "has_status": bool(t),
            "gate": sc.get("state") or "未登记",
            "docs": docs,
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M"),
        })
    order = {"要改": 0, "等人过目": 1, "没登记状态": 2, "已通过": 3, "已废弃": 4}
    idx = {e["name"]: i for i, e in enumerate(registry_entries())}
    out.sort(key=lambda x: (order.get(x["state"], 9),
                            idx.get(x["drama"], 999), x["task"]))
    return {
        "reviews": out,
        # 没登记状态的片子也有清单要人看，算进来，另给一个数字说清来源
        "waiting_count": sum(1 for x in out if x["state"] in
                             ("要改", "等人过目", "没登记状态")),
        "unlogged_count": sum(1 for x in out if x["state"] == "没登记状态"),
    }


def api_doc(rel: str) -> dict:
    p = (RUNS / rel).resolve()
    if not str(p).startswith(str(RUNS.resolve())) or p.suffix.lower() not in (".md", ".json", ".txt"):
        return {"error": "只允许读 runs 下的文本说明", "text": ""}
    try:
        return {"name": p.name, "text": p.read_text(encoding="utf-8", errors="replace")}
    except OSError as e:
        return {"error": str(e), "text": ""}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, p: Path, ctype: str):
        try:
            body = p.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def reject(self):
        # 先把请求体读干净再回，否则客户端会看到「连接被断开」而不是明确的 405
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        self.send_json({"error": "只读页面，不接受提交；要跑任务回对话里说"}, 405)

    def send_video(self, p: Path):
        """支持分段读取，否则浏览器拖进度条会卡死，也不能边下边播。"""
        try:
            size = p.stat().st_size
        except OSError:
            return self.send_error(404)
        rng = (self.headers.get("Range") or "").strip()
        start, end = 0, size - 1
        if rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                start = min(int(m.group(1)), size - 1)
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
        if start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        length = end - start + 1
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with p.open("rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 用户拖进度条、关页面都会断，正常

    def do_POST(self):
        self.reject()

    def do_PUT(self):
        self.reject()

    def do_DELETE(self):
        self.reject()

    def do_PATCH(self):
        self.reject()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = unquote(u.path)  # 中文文件名浏览器会编码，这里还原

        try:
            if path == "/" or path == "/index.html":
                return self.send_file(UI_DIR / "index.html", "text/html; charset=utf-8")
            if path == "/api/overview":
                return self.send_json(api_overview())
            if path == "/api/autopilot":
                return self.send_json(api_autopilot())
            if path == "/api/progress":
                return self.send_json(api_progress())
            if path == "/api/dramas":
                return self.send_json({"dramas": api_dramas()})
            if path == "/api/registry":
                return self.send_json(api_registry())
            if path == "/api/assets":
                return self.send_json(api_assets(q.get("drama", [""])[0]))
            if path == "/api/plans":
                return self.send_json(api_plans())
            if path == "/api/plan":
                return self.send_json(api_plan(q.get("drama", [""])[0],
                                              q.get("task", [""])[0]))
            if path == "/api/shared":
                return self.send_json(api_shared())
            if path == "/api/pipelines":
                return self.send_json(api_pipelines(q.get("scope", ["active"])[0]))
            if path == "/api/reviews":
                return self.send_json(api_reviews(q.get("scope", ["active"])[0]))
            if path == "/api/videos":
                return self.send_json(api_videos(q.get("scope", ["active"])[0]))
            if path == "/api/doc":
                return self.send_json(api_doc(q.get("path", [""])[0]))
            if path.startswith("/video/"):
                p = (RUNS / path[len("/video/"):]).resolve()
                if not str(p).startswith(str(RUNS.resolve())) or p.suffix.lower() != ".mp4":
                    return self.send_error(403)
                return self.send_video(p)
            if path.startswith("/frames/"):
                # 只允许读 runs/ 下的卡片图，挡住 ../ 目录穿越
                rel = path[len("/frames/"):]
                p = (RUNS / rel).resolve()
                if not str(p).startswith(str(RUNS.resolve())) or p.suffix.lower() not in (".jpg", ".png"):
                    return self.send_error(403)
                return self.send_file(p, "image/jpeg" if p.suffix.lower() == ".jpg" else "image/png")
        except Exception as e:
            return self.send_json({"error": f"{e.__class__.__name__}: {e}"}, 500)

        self.send_error(404)


def warm_cache():
    """服务一起来就先把要用到的文件读进内存，用户第一次打开不用等 9 秒。

    只读不写：缓存的键带文件修改时间，磁盘上的文件一改，下次请求自动重读。
    """
    try:
        for drama in source_dramas():
            api_assets(drama)
        api_pipelines()
        api_videos()
    except Exception as e:
        print(f"[预热] 跳过：{e}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    threading.Thread(target=warm_cache, daemon=True).start()
    print(f"短剧工作台（只读）：http://127.0.0.1:{a.port}", flush=True)
    print(f"数据目录：{ROOT}", flush=True)
    if not (ROOT / "runs" / "工作台.json").exists():
        print("  注意：这个目录里还没有 runs/工作台.json，页面会是空的。", flush=True)
        print("  想指定位置：设环境变量 SHORT_DRAMA_ROOT 指向真正的数据目录再起。", flush=True)
    print("按 Ctrl+C 停止", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
