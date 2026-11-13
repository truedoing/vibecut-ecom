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


def find_root() -> Path:
    """按 runs/工作台.json 找仓库根。技能是软链到用户级目录的，不能数 parents。"""
    for parent in Path(__file__).resolve().parents:
        if (parent / "runs" / "工作台.json").exists():
            return parent
    raise RuntimeError("找不到仓库根（没看到 runs/工作台.json）")


ROOT = find_root()
sys.path.insert(0, str(SKILL_DIR / "scripts"))
import progress  # noqa: E402
import status_overview  # noqa: E402

ASSETS = ROOT / "assets" / "short-drama"
RUNS = ROOT / "runs"


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
    """
    base = asset_dir_of(drama)
    if not base:
        return {"drama": drama, "error": "这部剧还没建库",
                "ep_count": 0, "ready_count": 0, "eps": [], "char_map": {},
                "video_count": source_ep_count(drama)}

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
    return {
        "drama": drama, "ep_count": len(eps), "ready_count": ready, "eps": eps,
        "video_count": max(source_ep_count(drama), len(eps)),
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


def api_videos() -> dict:
    """按「剧 / 片子」分组，一组就是一个预览单元。

    旧版页面把 38 个 mp4 平铺成一张列表，用户得靠文件名猜哪条是成片。
    这里按状态文件里的 trailer.path 认当前成片，再按片子分组，前面只放人要看的。
    """
    vids = list_videos()
    by: dict[tuple[str, str], list[dict]] = {}
    for v in vids:
        by.setdefault((v["drama"], v["task"]), []).append(v)

    groups = []
    for (drama, task), items in sorted(by.items()):
        meta = _task_preview_meta(drama, task)
        trel = meta["trailer_rel"]
        for v in items:
            v["role"] = _video_role(v, trel)
            v["is_current"] = v["role"] == "当前成片"
        items.sort(key=lambda v: (_ROLE_ORDER[v["role"]], -v["mtime_ts"]))
        current = next((v for v in items if v["is_current"]), None)
        groups.append({
            "key": f"{drama}/{task}",
            "drama": drama, "task": task,
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
    return {
        "groups": groups, "videos": vids, "final": final,
        "active": ws.get("active") or {},
        "group_count": len(groups),
        "current_count": sum(1 for g in groups if g["has_current"]),
        "final_count": len(final),
        "old_count": sum(1 for v in vids if v["role"] == "旧版"),
        "build_count": sum(1 for v in vids if v["role"] == "装配中间文件"),
    }


def api_pipelines() -> dict:
    """一部剧一条管线，三个阶段的进度。

    后两段不是「一个开关」，是一部剧底下**一批片子**各自的进度：
    有的等人过目、有的已成片、有的改了清单要重拼。所以这里给的是数，不是勾。
    """
    ov = api_overview()
    by_task = {(t["drama"], t["task"]): t for t in ov["tasks"]}
    plans = api_plans()["plans"]
    # 出片进度按「状态文件里写了当前成片路径」算，与预览页同一口径。
    # 不能只看 sd_*.mp4：D1/D2 的成片在 build/vN.mp4 下，文件名不带 sd_。
    pv_groups = {(g["drama"], g["task"]): g
                 for g in api_videos()["groups"]}

    alias = drama_alias()
    # 剧只从素材根目录的一级文件夹来。原视频不在这台机器上的历史剧，
    # 片子记录和成片仍在 runs 下，不混进管线，单独列一句免得看着像丢了。
    names = set(api_dramas())
    historical = sorted({p["drama"] for p in plans} - names)

    out = []
    for drama in sorted(names):
        a = api_assets(drama)
        mine = [p for p in plans if p["drama"] == drama]
        waiting, passed, revise, to_mix, to_remix = [], [], [], [], []
        for p in mine:
            t = by_task.get((drama, p["task"]))
            if not t:
                # 没登记状态的新片子，多半是刚排出来等人过目
                waiting.append(p["task"])
                continue
            sc = next((s for s in t["stages"] if s["key"] == "script"), {})
            tr = next((s for s in t["stages"] if s["key"] == "trailer"), {})
            if "script" in t["reasons"]:
                revise.append(p["task"])
            elif sc.get("state") == "approved":
                passed.append(p["task"])
            elif sc.get("state") == "superseded":
                pass
            else:
                waiting.append(p["task"])
            if "trailer" in t["reasons"]:
                to_remix.append(p["task"])
            elif tr.get("state") not in ("done", "approved", "superseded"):
                to_mix.append(p["task"])
        eps = a.get("eps") or []
        video_count = a.get("video_count") or len(eps)
        # 分母沿用「已经动工的集数」，不换成全剧总集数：
        # 一部剧可能只挑前 19 集分析，用 60 集当分母会把做完的活显示成 29%。
        # 全剧集数另给前端，在展开的细节里提示。
        total_eps = len(eps) or video_count
        line_total = sum(e["line_count"] for e in eps)
        sentences = sum(e["sentence_count"] for e in eps)
        named = sum(e["named_count"] for e in eps)
        emos = sum(e["emotion_count"] for e in eps)
        lines_eps = sum(1 for e in eps if e["has_lines"])
        shots_eps = sum(1 for e in eps if e["has_shots"])
        sent_eps = sum(1 for e in eps if e["has_sentences"])
        # ① 的百分比：每集要交三份资料（台词、镜头标注、合表），按份数算。
        # 只按「三样都齐的集数」算，会出现台词全齐、只差合表却显示 0% 的冤枉进度。
        parts_total = total_eps * 3
        pct1 = round((lines_eps + shots_eps + sent_eps) / parts_total * 100) if parts_total else 0
        n_tasks = len(mine)
        pct2 = round(len(passed) / n_tasks * 100) if n_tasks else 0
        vf = [p for p in mine
              if pv_groups.get((drama, p["task"]), {}).get("has_current")]
        pct3 = round(len(vf) / n_tasks * 100) if n_tasks else 0
        active = (ov["active"] or {})
        out.append({
            "drama": drama,
            "active": active.get("drama") == drama,
            "stage1": {
                "label": "素材分析建库",
                "ep_count": a.get("ep_count", 0),
                "ready_count": a.get("ready_count", 0),
                "video_count": video_count,
                "lines_eps": lines_eps, "shots_eps": shots_eps, "sent_eps": sent_eps,
                "lines": line_total,
                "sentences": sentences,
                "named_pct": round(named / sentences * 100, 1) if sentences else 0,
                "emotion_pct": round(emos / sentences * 100, 1) if sentences else 0,
                "dense": sum(e["dense_shots"] for e in eps),
                "pct": pct1,
                "done": total_eps > 0 and sent_eps == total_eps,
            },
            "stage2": {
                "label": "文案生成",
                "tasks": n_tasks,
                "waiting": waiting, "passed": passed, "revise": revise,
                "pct": pct2,
                "unlogged": not any((drama, p["task"]) in by_task for p in mine),
            },
            "stage3": {
                "label": "出片",
                "final_count": len(vf),
                "to_mix": to_mix, "to_remix": to_remix,
                "pct": pct3,
            },
        })
    return {"pipelines": out, "generated_at": ov["generated_at"],
            "active": ov["active"],
            "source_root": str(source_root()),
            "historical": historical}


REVIEW_DOCS = ("清单过目.md", "清单人审表.md", "送审说明.md",
               "方向卡.md", "选段说明.md", "spec.json")


def api_reviews() -> dict:
    """待审文案：把等人过目的清单挑出来，附上看得到内容的文档。"""
    by_task = {(t["drama"], t["task"]): t for t in api_overview()["tasks"]}
    out = []
    for p in sorted(RUNS.glob("*/*/plan.json")):
        drama, task = p.parent.parent.name, p.parent.name
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
    out.sort(key=lambda x: (order.get(x["state"], 9), x["drama"], x["task"]))
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
            if path == "/api/progress":
                return self.send_json(api_progress())
            if path == "/api/dramas":
                return self.send_json({"dramas": api_dramas()})
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
                return self.send_json(api_pipelines())
            if path == "/api/reviews":
                return self.send_json(api_reviews())
            if path == "/api/videos":
                return self.send_json(api_videos())
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
    print(f"仓库根：{ROOT}", flush=True)
    print("按 Ctrl+C 停止", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
