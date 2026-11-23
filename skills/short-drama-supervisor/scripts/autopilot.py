#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无人值守驾驶舱：把一部剧从建档跑到成片，中间不等人。

它不替代判断，只负责三件事：
  ① 把能自动跑的步骤按顺序跑完，跳过已经做完的；
  ② 跑到必须有人拍板的地方，明确喊停并说清「该你决定了」；
  ③ 全程记账，最后出一份汇总报告。

用法（三个阶段名见下面 STAGES，从上往下）：
  python3 autopilot.py status --drama 离婚后我靠鸡排翻身
  python3 autopilot.py run    --drama 离婚后我靠鸡排翻身 --upto lines
  python3 autopilot.py run    --drama 离婚后我靠鸡排翻身 --only tag --episodes 1
  python3 autopilot.py report --drama 离婚后我靠鸡排翻身

设计上的三条规矩（别人改这个文件前请先读）：
  1. 每步都幂等：产物齐了就跳过，绝不重跑一遍烧钱。
  2. 失败不吞掉：记进日志继续往下，除非 --on-error stop。
  3. 判断不代劳：需要人拍板的步骤只喊「该你了」，绝不自己糊一个结论上去。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 定位
# 数据目录与技能目录都靠 _env 找，不数层级——换机器、装进插件目录都不用改。
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _env import data_root, read_env  # noqa: E402
    ROOT = data_root()
except Exception:  # 极端情况兜底，至少别崩
    ROOT = Path(__file__).resolve().parents[3]


def _skill(name: str) -> Path:
    """技能目录。本机软链到仓库、插件里在包内，两处都试。"""
    for base in (Path(__file__).resolve().parents[2], ROOT / "skills"):
        p = base / name
        if (p / "SKILL.md").exists():
            return p
    return ROOT / "skills" / name


PY = sys.executable or "python3"

# ---------------------------------------------------------------- 阶段表
# kind: auto = 脚本自己跑；model = 模型要写东西；mixed = 先跑脚本再要模型看图
STAGES = [
    ("check",    "素材体检",        "auto"),
    ("lines",    "台词时间轴",      "auto"),
    # 建包排在台词之后不是排版问题：剧集包唯一的事实来源就是台词，
    # 台词没抽完就建包，写出来的剧情概要只能靠编。
    ("pack",     "剧集包",          "model"),
    ("cuts",     "切口停顿表",      "auto"),
    ("segment",  "冲突段落切分",    "auto"),
    ("tag",      "镜头画面打标",    "auto"),
    ("charmap",  "外观对人名",      "model"),
    ("edge",     "边界帧扫描",      "mixed"),
    ("index",    "查询库与句级表",  "auto"),
    ("story",    "冲突地图",        "mixed"),
    ("plan",     "剪辑清单",        "model"),
    ("assemble", "装配成片",        "auto"),
    ("report",   "汇总报告",        "auto"),
]
ORDER = [s[0] for s in STAGES]
LABEL = {s[0]: s[1] for s in STAGES}

# 这几步挂了就不能往下走——不是流程规定，是后面的活全靠它们喂数据。
# 台词没有就选不了段；画面没打标就分不清谁在说；素材不在就什么都干不了。
HARD = {"check", "lines", "tag"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _log_path(drama: str) -> Path:
    d = ROOT / "runs" / drama / "_autopilot"
    d.mkdir(parents=True, exist_ok=True)
    return d / "日志.json"


def load_log(drama: str) -> dict:
    p = _log_path(drama)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"drama": drama, "created_at": _now(), "runs": [], "stages": {}}


def save_log(drama: str, log: dict) -> None:
    log["updated_at"] = _now()
    _log_path(drama).write_text(
        json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8"
    )


# ---------------------------------------------------------------- 素材与集数
def asset_dir(drama: str) -> Path:
    return ROOT / "assets" / "short-drama" / drama


def ep_list(drama: str) -> tuple[list[int], str]:
    """这部剧该解析哪几集。

    优先读工作台里写的 usable_episodes（人已经定过）；
    没写就按不剧透铁律取前 60%——短剧预告只靠前段立住，后面是要留给人去追的。
    """
    # ① 工作台登记过就照登记来
    wb = ROOT / "runs" / "工作台.json"
    if wb.exists():
        try:
            d = json.loads(wb.read_text(encoding="utf-8"))
            info = (d.get("dramas") or {}).get(drama) or {}
            ue = str(info.get("usable_episodes") or "").strip()
            if ue and ue[0].isdigit():
                head = ue.split("（")[0].split("(")[0]
                if "-" in head:
                    a, b = head.split("-")[:2]
                    return list(range(int(a), int(b) + 1)), f"工作台登记 {ue}"
                return [int(head)], f"工作台登记 {ue}"
            fin = info.get("finale_episodes") or []
            if fin:
                pass  # 落到下面按素材算
        except Exception:
            pass

    # ② 按素材文件算
    env = read_env()
    base = env.get("SHORT_DRAMA_ASSET_ROOT") or str(Path.home() / "短剧")
    src = Path(base) / drama
    eps = sorted(
        int(p.stem) for p in src.glob("*.mp4") if p.stem.isdigit()
    ) if src.exists() else []
    if not eps:
        return [], f"素材目录没有 mp4：{src}"
    keep = eps[: max(1, int(len(eps) * 0.6))]
    return keep, f"按前 60% 取 {len(keep)} 集（素材共 {len(eps)} 集）"


def has_all(d: Path, pattern: str, eps: list[int]) -> tuple[bool, int]:
    if not d.exists():
        return False, 0
    got = 0
    for ep in eps:
        if list(d.glob(pattern.format(ep=ep))):
            got += 1
    return got == len(eps) and len(eps) > 0, got


# ---------------------------------------------------------------- 跑一条外部脚本
def sh(cmd: list[str], timeout: int = 7200) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT)
        )
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out
    except subprocess.TimeoutExpired:
        return 124, f"超时（>{timeout}s）"
    except Exception as e:
        return 1, f"{type(e).__name__}: {e}"


def _tail(s: str, n: int = 600) -> str:
    s = " ".join(s.split())
    return s[-n:] if len(s) > n else s


# ---------------------------------------------------------------- 各阶段
def st_check(a) -> dict:
    # ffmpeg / ffprobe 先备好：包里不带可执行文件了，缺了在这里按平台下。
    # 跟配套库一样放在体检里——不能等跑到装配才发现，那时候已经跑了几小时。
    try:
        import ffbin
        good, fmsg = ffbin.ensure()
        if not good:
            return dict(status="fail", detail="ffmpeg 备不好，后面全没法跑：" + fmsg)
    except Exception as e:
        return dict(status="fail", detail=f"ffmpeg 自检没跑成：{e}")

    # 配套库先补齐。放在体检里，不是等跑到「读台词」才下载——
    # 27 集的活儿跑到一半停下来装 100MB，人还以为卡死了（11-16 实际栽过）。
    try:
        import deps
        good, dmsg = deps.ensure()
        if not good:
            return dict(status="fail", detail="配套库装不上，后面全没法跑：" + dmsg)
    except Exception as e:
        return dict(status="fail", detail=f"配套库自检没跑成：{e}")

    env = read_env()
    base = env.get("SHORT_DRAMA_ASSET_ROOT") or str(Path.home() / "短剧")
    src = Path(base) / a.drama
    if not src.exists():
        return dict(status="fail", detail=f"素材目录不存在：{src}")
    eps, why = ep_list(a.drama)
    if not eps:
        return dict(status="fail", detail=why)
    mp4 = [p for p in src.glob("*.mp4")]
    if not mp4:
        return dict(status="fail", detail=f"{src} 里没有 mp4")
    use = a.eps if getattr(a, "eps", None) else eps
    return dict(
        status="ok",
        detail=f"{len(mp4)} 个素材文件；本次解析 {len(use)} 集（{use[0]}–{use[-1]}）；依据：{why}",
    )


def st_pack(a) -> dict:
    pack = ROOT / "packs" / "short-drama" / a.drama / "drama-pack.json"
    if not pack.exists():
        return dict(
            status="needs_model",
            detail=f"剧集包还没建：{pack}。要让模型通读素材后写出来，然后跑 validate_pack.py 校验。",
        )
    rc, out = sh([PY, str(_skill("short-drama-pack") / "scripts" / "validate_pack.py"), str(pack)])
    if rc != 0:
        return dict(status="fail", detail=f"剧集包校验没过：{_tail(out)}")
    rc2, out2 = sh([PY, str(_skill("short-drama-pack") / "scripts" / "check_finale_gate.py"), str(pack)])
    note = "" if rc2 == 0 else f"；大结局门禁提醒：{_tail(out2, 200)}"
    return dict(status="ok", detail=f"剧集包校验通过{note}")


def st_lines(a) -> dict:
    d = asset_dir(a.drama) / "lines"
    done, got = has_all(d, "lines_ep{ep:02d}.json", a.eps)
    if done and not a.force:
        return dict(status="skip", detail=f"台词已齐（{got}/{len(a.eps)} 集），跳过")
    cmd = [PY, str(_skill("short-drama-asset-index") / "scripts" / "extract_lines.py"),
           "--drama", a.drama, "--jobs", str(a.jobs), "--ep"] + [str(e) for e in a.eps]
    rc, out = sh(cmd)
    done, got = has_all(d, "lines_ep{ep:02d}.json", a.eps)
    if rc != 0 and got == 0:
        return dict(status="fail", detail=f"台词抽取失败：{_tail(out)}")
    st = "ok" if done else "fail"
    return dict(status=st, detail=f"台词 {got}/{len(a.eps)} 集" + ("" if done else "（不齐，看日志）"))


def st_cuts(a) -> dict:
    d = asset_dir(a.drama) / "cuts"
    done, got = has_all(d, "ep{ep:02d}.json", a.eps)
    if done and not a.force:
        return dict(status="skip", detail=f"切口表已齐（{got}/{len(a.eps)}），跳过")
    cmd = [PY, str(_skill("short-drama-asset-index") / "scripts" / "cut_index.py"),
           "--build", "--drama", a.drama, "--ep"] + [str(e) for e in a.eps]
    rc, out = sh(cmd)
    done, got = has_all(d, "ep{ep:02d}.json", a.eps)
    return dict(status="ok" if done else "fail", detail=f"切口表 {got}/{len(a.eps)} 集；{_tail(out, 200)}")


def _seg_count(seg: Path) -> int:
    """数切出多少段。产物按集存在 episodes 底下，别只看顶层有没有 segments。"""
    try:
        d = json.loads(seg.read_text(encoding="utf-8"))
    except Exception:
        return -1
    eps = d.get("episodes")
    if isinstance(eps, dict):
        return sum(len(v.get("segments") or []) for v in eps.values() if isinstance(v, dict))
    if isinstance(d.get("segments"), list):
        return len(d["segments"])
    return -1


def st_segment(a) -> dict:
    seg = asset_dir(a.drama) / "segments_skeleton.json"
    if seg.exists() and not a.force:
        n = _seg_count(seg)
        return dict(status="skip", detail=f"骨架已在（共 {n} 段），跳过")
    cmd = [PY, str(_skill("short-drama-asset-index") / "scripts" / "segment.py"),
           "--drama", a.drama, "--ep"] + [str(e) for e in a.eps] + ["--write"]
    rc, out = sh(cmd)
    if not seg.exists():
        return dict(status="fail", detail=f"切段没出产物：{_tail(out)}")
    n = _seg_count(seg)
    if n <= 0:
        return dict(status="fail", detail=f"切出 0 段，台词可能没抽到：{_tail(out, 300)}")
    return dict(status="ok", detail=f"切出 {n} 段")


def tag_mode(a=None) -> str:
    """要不要调接口做画面打标。

    11-16 张老师定：**默认不调**。纯剪用不上，这一步是全流程唯一花钱的地方
    （一部 17 集的剧约 134 万 tokens），不该默认就烧。

    `.env` 里 `SHORT_DRAMA_TAG` 三个值：
      off（默认）只切镜头边界、不调接口。谁在说只能靠台词上下文推，
                句级表的「画内/画外」会全判画外，选段就只靠台词文本
      on         逐镜头打标，谁在说、什么表情、什么景别都有
      auto       有 MIMO 的 key 就打，没 key 自动退成 off
    命令行 `--tag` 可以不管 `.env` 直接开这一趟。
    """
    if a is not None and getattr(a, "tag", False):
        return "on"
    env = read_env()
    v = (env.get("SHORT_DRAMA_TAG") or "").strip().lower()
    if v in ("on", "1", "true", "yes"):
        return "on"
    if v == "auto":
        return "on" if (env.get("MIMO_API_KEY") or "").strip() else "off"
    return "off"


def st_tag(a) -> dict:
    d = asset_dir(a.drama) / "shots"
    eps = a.eps
    script = _skill("short-drama-asset-index") / "scripts" / "tag_shots.py"
    todo = [e for e in eps if a.force or not list(d.glob(f"shots_ep{e:02d}.json"))]
    if not todo:
        k, got = has_all(d, "shots_ep{ep:02d}.json", eps)
        return dict(status="skip", detail=f"镜头已切/已打标（{got}/{len(eps)} 集），跳过")

    mode = tag_mode(a)
    vlm = mode == "on"
    if not vlm:
        print("  画面打标：默认关，只切镜头边界（不调接口、不花钱）", flush=True)

    # 每集独立、产物按集落盘，可以并行。开打标时是全流程最慢的一步（实测约 10 分钟一集），
    # 串行跑 27 集要四个半小时，并行 3 路压到一个半小时左右。
    # 只切镜头是本地 ffmpeg 的活，更快，同样并行。
    failed: list[str] = []
    done = [0]
    lock = threading.Lock()
    jobs = max(1, a.tag_jobs)

    def one(ep: int) -> None:
        cmd = [PY, str(script), "--drama", a.drama, "--ep", str(ep)]
        if not vlm:
            cmd.append("--no-vlm")
        rc, out = sh(cmd, timeout=7200)
        with lock:
            done[0] += 1
            if rc != 0:
                failed.append(f"ep{ep:02d}:{_tail(out, 90)}")
            print(f"    {'打标' if vlm else '切镜头'} ep{ep:02d} "
                  f"{'ok' if rc == 0 else 'fail'} ({done[0]}/{len(todo)})", flush=True)

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        list(ex.map(one, todo))

    k, got = has_all(d, "shots_ep{ep:02d}.json", eps)
    if vlm:
        detail = f"画面打标 {got}/{len(eps)} 集（并行 {jobs} 路）"
    else:
        detail = (f"只切了镜头边界 {got}/{len(eps)} 集，没调接口打标"
                  f"（默认关；要用 AI 玩法或要判谁在说，加 --tag 或 .env 写 SHORT_DRAMA_TAG=on）")
    if failed:
        detail += f"；失败 {len(failed)} 集（例：{failed[0]}）"
    return dict(status="ok" if k else "fail", detail=detail)


def st_charmap(a) -> dict:
    """外观串对人名。

    打标只给「年轻女性 浅灰长袖连衣裙 黑发扎马尾」这种描述——因为短剧演员没参考照，
    让模型认人只会更不准（11-08 定的）。所以要把外观对到剧集包里的人物名上，
    之后才能按「许晴说了什么」去搜台词。这一步只有模型能推，脚本推不了。
    """
    p = asset_dir(a.drama) / "char_map.json"
    if p.exists():
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            # 顶层是 {"chars": {...}, "note": "..."}，直接 len() 数的是键名（2 条），
            # 不是人物条数。旧格式顶层就是名字→配置的映射，兜底按顶层数（11-16 修）。
            chars = doc.get("chars") if isinstance(doc, dict) else None
            n = len(chars if isinstance(chars, dict) else (doc or {}))
        except Exception:
            n = -1
        return dict(status="skip", detail=f"人名映射已在（{n} 条规则），跳过")
    if tag_mode(a) != "on":
        return dict(
            status="skip",
            detail="跳过：这一趟没做画面打标，没有外观描述可对，人名映射无从做起。"
                   "要用就加 --tag 先打标，再单独补这一步。",
        )
    return dict(
        status="needs_model",
        detail="缺人名映射 char_map.json。画面打标只有外观描述（比如「中年女性 深色旧棉袄」），"
               "对不上人名就没法按人搜台词、也没法判断「谁在跟谁较劲」。"
               "要读剧集包的人物表 + 各集打标结果，把外观对到人名上，再跑 build_sentence_view。",
    )


def st_edge(a) -> dict:
    d = asset_dir(a.drama) / "edge_frames"
    man = d / "manifest.json"
    if man.exists() and not a.force:
        try:
            n = len(json.loads(man.read_text(encoding="utf-8")))
        except Exception:
            n = -1
        return dict(status="needs_model",
                    detail=f"边界帧图已生成（{n} 条记录），但「每段从哪出去」得看图判读，脚本判不了。")
    cmd = [PY, str(_skill("short-drama-asset-index") / "scripts" / "edge_frames.py"), "--ep"] + [str(e) for e in a.eps]
    if a.force:
        cmd.append("--force")
    rc, out = sh(cmd)
    if not man.exists():
        return dict(status="fail", detail=f"边界帧没出图：{_tail(out)}")
    return dict(status="needs_model",
                detail="边界帧图已生成，下一步要看图判读每段从哪儿出去（脚本判不了）。")


def st_index(a) -> dict:
    db = asset_dir(a.drama) / "index.sqlite"
    sb = asset_dir(a.drama) / "sentences"
    detail = []
    rc, out = sh([PY, str(_skill("short-drama-asset-index") / "scripts" / "build_index.py"), "--drama", a.drama])
    detail.append("查询库" + ("重建好了" if rc == 0 and db.exists() else f"失败：{_tail(out, 200)}"))
    ok, got = has_all(sb, "sentences_ep{ep:02d}.json", a.eps)
    if not ok or a.force:
        bs = _skill("short-drama-asset-index") / "scripts" / "build_sentence_view.py"
        rc2, out2 = sh([PY, str(bs), "--drama", a.drama])
        ok, got = has_all(sb, "sentences_ep{ep:02d}.json", a.eps)
        detail.append(f"句级表 {got}/{len(a.eps)} 集" + ("" if ok else f"（{_tail(out2, 150)}）"))
    else:
        detail.append(f"句级表已齐 {got}/{len(a.eps)} 集")
    return dict(status="ok" if rc == 0 else "fail", detail="；".join(detail))


def st_story(a) -> dict:
    out = ROOT / "runs" / a.drama / "_autopilot" / "冲突地图.md"
    if not out.exists() or a.force:
        rc, o = sh([PY, str(_skill("short-drama-script") / "scripts" / "storymap.py"), a.drama,
                    "--out", str(out)])
        if not out.exists():
            return dict(status="fail", detail=f"冲突地图没出：{_tail(o)}")
    return dict(status="needs_model",
                detail=f"冲突地图已出（{out.relative_to(ROOT)}）。"
                       f"下一步派 short-drama-microscript 跟人把故事聊成微剧本（圈线→骨架→逐场定稿）"
                       f"——这是全流程唯一必须人拍板的地方；微剧本没定稿，不许排剪辑清单。")


def st_plan(a) -> dict:
    run_dir = ROOT / "runs" / a.drama
    plans = sorted(run_dir.glob("*/plan.json"))
    if not plans:
        return dict(status="needs_model",
                    detail="还没有剪辑清单。要模型读冲突地图+台词库，为每条故事线写一份清单，再交给展开器。")
    lines = []
    for p in plans:
        rc, out = sh([PY, str(_skill("short-drama-script") / "scripts" / "selfcheck.py"), "--plan", str(p)])
        lines.append(f"{p.parent.name}: {'自检过' if rc == 0 else '自检有问题 -> ' + _tail(out, 150)}")
    return dict(status="ok", detail="；".join(lines))


def st_assemble(a) -> dict:
    run_dir = ROOT / "runs" / a.drama
    plans = sorted(run_dir.glob("*/plan.json"))
    if not plans:
        return dict(status="fail", detail="没有清单可装配")
    results, made = [], []
    for p in plans:
        out = p.parent / "build" / f"{p.parent.name}_成片.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists() and not a.force:
            results.append(f"{p.parent.name}: 已有成片，跳过")
            made.append(str(out.relative_to(ROOT)))
            continue
        rc, o = sh([PY, str(_skill("short-drama-trailer") / "scripts" / "mix_trailer.py"),
                    "--plan", str(p), "--out", str(out)], timeout=3600)
        if rc == 0 and out.exists():
            results.append(f"{p.parent.name}: 装好了")
            made.append(str(out.relative_to(ROOT)))
        else:
            results.append(f"{p.parent.name}: 装不出来 -> {_tail(o, 200)}")
    ok = len(made) == len(plans)
    return dict(status="ok" if ok else "fail", detail="；".join(results), artifacts=made)


# ---------------------------------------------------------------- 驾驶
AUTO = {
    "check": st_check, "pack": st_pack, "lines": st_lines, "cuts": st_cuts,
    "segment": st_segment, "tag": st_tag, "charmap": st_charmap,
    "edge": st_edge, "index": st_index,
    "story": st_story, "plan": st_plan, "assemble": st_assemble,
}


def probe(name: str, drama: str, eps: list[int]) -> str:
    """只查产物在不在，不跑任何脚本、不花一分钱。

    进度必须反映磁盘真实情况，不能只信日志——日志可能是空的，
    但东西早就做完了（或者反过来）。挂一晚上回来先看这个。
    """
    ad = asset_dir(drama)
    rd = ROOT / "runs" / drama
    try:
        if name == "check":
            env = read_env()
            base = env.get("SHORT_DRAMA_ASSET_ROOT") or str(Path.home() / "短剧")
            n = len(list((Path(base) / drama).glob("*.mp4")))
            return f"素材 {n} 个 mp4" if n else "素材目录里没有 mp4"
        if name == "pack":
            p = ROOT / "packs" / "short-drama" / drama / "drama-pack.json"
            return "剧集包已在" if p.exists() else "还没建"
        if name == "lines":
            k, got = has_all(ad / "lines", "lines_ep{ep:02d}.json", eps)
            return f"{got}/{len(eps)} 集" + ("（齐）" if k else "（不齐）")
        if name == "cuts":
            k, got = has_all(ad / "cuts", "ep{ep:02d}.json", eps)
            return f"{got}/{len(eps)} 集" + ("（齐）" if k else "（不齐）")
        if name == "segment":
            return "骨架已在" if (ad / "segments_skeleton.json").exists() else "还没切"
        if name == "tag":
            k, got = has_all(ad / "shots", "shots_ep{ep:02d}.json", eps)
            return f"{got}/{len(eps)} 集" + ("（齐）" if k else "（不齐）")
        if name == "charmap":
            p = ad / "char_map.json"
            if not p.exists():
                return "还没建"
            try:
                return f"{len(json.loads(p.read_text(encoding='utf-8')))} 条规则"
            except Exception:
                return "在（读不出条数）"
        if name == "edge":
            return "图已生成" if (ad / "edge_frames" / "manifest.json").exists() else "还没扫"
        if name == "index":
            db = (ad / "index.sqlite").exists()
            k, got = has_all(ad / "sentences", "sentences_ep{ep:02d}.json", eps)
            return f"查询库{'在' if db else '缺'}／句级表 {got}/{len(eps)} 集"
        if name == "story":
            return "地图已出" if (rd / "_autopilot" / "冲突地图.md").exists() else "还没出"
        if name == "plan":
            n = len(list(rd.glob("*/plan.json")))
            return f"{n} 份清单" if n else "还没排"
        if name == "assemble":
            n = len(list(rd.glob("*/build/*.mp4")))
            return f"{n} 条成片" if n else "还没装"
    except Exception as e:
        return f"查不到（{type(e).__name__}）"
    return ""


def cmd_status(a) -> int:
    eps, why = ep_list(a.drama)
    a.eps = eps
    print(f"剧名：{a.drama}")
    print(f"本次解析：{len(eps)} 集（{why}）")
    log = load_log(a.drama)
    print("\n磁盘上实际做到哪：")
    for name, label, kind in STAGES:
        if name == "report":
            continue
        seen = probe(name, a.drama, eps)
        rec = (log.get("stages") or {}).get(name) or {}
        when = (rec.get("at") or "")[:16].replace("T", " ")
        spent = f" 上次跑了{rec.get('seconds')}s" if rec.get("seconds") else ""
        flag = {"fail": "  ← 上次出错", "needs_model": "  ← 等人定"}.get(rec.get("status"), "")
        print(f"  {label:<10} {seen:<22} {when:<17}{spent}{flag}")
    rpt = ROOT / "runs" / a.drama / "_autopilot" / "汇总报告.md"
    if rpt.exists():
        print(f"\n上一份汇总报告：{rpt}")
    return 0


def _beat(drama: str, stage: str, state: str, note: str = "", idx: int = 0, total: int = 0) -> None:
    """往进度心跳里写一条，好让网页知道「现在跑到第几步」。

    心跳失败一律吞掉——进度记录绝不能把主线跑挂（11-12 的教训）。
    """
    try:
        import progress
        if state == "start":
            progress.start("autopilot", drama, stage, total=total, unit="步")
        elif state == "update":
            progress.update("autopilot", drama, stage, done=idx, total=total, note=note)
        else:
            progress.finish("autopilot", drama, stage, note=note)
    except Exception:
        pass


def _hb(drama: str, note: str, every: int = 20):
    """长步骤期间每 20 秒跳一下。不跳的话页面会把「打标 1.5 小时没动静」当成卡死。"""
    try:
        import progress
        return progress.heartbeat("autopilot", drama, "run", every=every, note=note)
    except Exception:
        return lambda: None


def _ui_server() -> Path | None:
    """找到一份**带页面文件**的服务脚本。

    踩过的坑：技能目录是软链，指向用户级目录，那边只有 SKILL.md 和脚本，
    没有 ui/index.html。直接启动软链路径 → 服务起来了但首页 404，
    而且报错都被丢掉，看着像"网页没反应"（11-16 实测）。
    所以这里必须检查同目录下 index.html 真的在。
    """
    cands = [
        ROOT / "skills" / "short-drama-supervisor",
        Path(__file__).resolve().parents[1],
    ]
    for cand in cands:
        srv = cand / "ui" / "server.py"
        try:
            real = srv.resolve()
        except Exception:
            continue
        if real.exists() and (real.parent / "index.html").exists():
            return real
    return None


def _probe_ui(port: int) -> str:
    """端口上到底是什么：ok=新版在跑 / old=旧版占着 / none=没人。

    只查端口通不通会误判——旧版服务也开着端口，但它读不到无人值守的记录，
    打开页面就是空的（11-16 实测踩到）。

    另外必须绕开系统代理：Windows 上开着 Clash 之类的代理时，urllib 连
    127.0.0.1 也可能被塞给代理，本机服务明明活着却探成「没人」，
    于是又去起一个、再探还是没人，最后报「起不来」（11-16 补）。
    """
    import json as _json
    import urllib.error
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    api = f"http://127.0.0.1:{port}/api/autopilot"
    try:
        with opener.open(api, timeout=1.5) as r:
            if r.status == 200:
                _json.loads(r.read())
                return "ok"
    except urllib.error.HTTPError:
        return "old"          # 服务在，但不认这个接口 → 旧版本
    except Exception:
        pass
    try:
        with opener.open(f"http://127.0.0.1:{port}/", timeout=1.5) as r:
            if r.status == 200:
                return "old"
    except Exception:
        pass
    return "none"


def _open_browser(url: str) -> bool:
    """在你面前的这台机器上把网页打开。打不开就返回 False，让调用方把地址说出来。"""
    try:
        import webbrowser
        if webbrowser.open(url, new=2):
            return True
    except Exception:
        pass
    try:
        if os.name == "nt":
            os.startfile(url)          # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
        return True
    except Exception:
        return False


def _ui_log() -> Path:
    p = ROOT / "runs" / ".ui_server.log"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def _ensure_ui(port: int = 8765) -> tuple[str, str, str]:
    """确保进度网页在跑，返回（地址，状态，说明）。

    状态：ok=本来就在跑 / new=刚起 / fail=起不来。
    失败时把服务自己的报错带回来——以前用 DEVNULL 全吞掉，只剩一句
    「没起起来」，等于没法查（11-16 补）。

    端口被旧版服务占着时不硬撞，自动往后退一个（8765→8766…）。
    撞上去的后果是页面打开一片空、还以为功能坏了（11-16 实测折腾了半天）。
    """
    ports = list(range(port, port + 10))

    # ① 已经有新版在某个端口上跑，直接用
    for p in ports:
        if _probe_ui(p) == "ok":
            return f"http://127.0.0.1:{p}", "ok", ""

    # ② 找一个真没人用的端口起
    server = _ui_server()
    if not server:
        return "", "fail", "包里找不到进度网页的服务脚本（ui/server.py 或 index.html 缺失）"
    for p in ports:
        if _probe_ui(p) != "none":
            continue          # 有东西占着（多半是旧版），跳过
        logp = _ui_log()
        fh = None
        try:
            fh = open(logp, "ab")
        except Exception:
            fh = subprocess.DEVNULL
        try:
            # Windows 上除了「脱离控制台」还要「自成进程组」：
            # 只脱离控制台的话，跑它的那个命令行一结束，服务常常被一起收走。
            detach = ({"creationflags": 0x00000008 | 0x00000200} if os.name == "nt"
                      else {"start_new_session": True})
            subprocess.Popen(
                [PY, str(server), "--port", str(p)],
                cwd=str(ROOT),   # 稳在仓库根：服务靠它找素材和台账，cwd 错了会读不到数据
                stdin=subprocess.DEVNULL, stdout=fh,
                stderr=(subprocess.STDOUT if fh is not subprocess.DEVNULL else subprocess.DEVNULL),
                **detach,
            )
            if fh is not subprocess.DEVNULL:
                try:
                    fh.close()      # 子进程已继承句柄，父进程这头可以放掉
                except Exception:
                    pass
            for _ in range(20):     # 最多等 10 秒，服务要 import 一堆模块
                time.sleep(0.5)
                if _probe_ui(p) == "ok":
                    return f"http://127.0.0.1:{p}", "new", ""
            why = _tail_log(logp)
            return "", "fail", (f"端口 {p} 上服务没起来。{why}" if why else f"端口 {p} 上服务没起来。")
        except Exception as e:
            return "", "fail", f"起服务时出错：{type(e).__name__}: {e}"
        finally:
            if fh is not None and fh is not subprocess.DEVNULL:
                try:
                    fh.close()
                except Exception:
                    pass
        break
    return "", "fail", "8765 起往后十个端口都被占着"


def _tail_log(path: Path, n: int = 400) -> str:
    """读服务自己留下的报错尾巴，失败时拿来给人看。"""
    try:
        txt = path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""
    if not txt:
        return ""
    return "服务最后说：" + " ".join(txt.splitlines()[-6:])[-n:]


def cmd_run(a) -> int:
    eps, why = ep_list(a.drama)
    if a.episodes:
        # 测试用：只取前几集，验证链路通不通，不烧全集的钱
        eps = eps[: a.episodes]
        why += f"；本次只取前 {len(eps)} 集试跑"
    a.eps = eps
    if not eps:
        print(f"停：{why}")
        return 2
    log = load_log(a.drama)
    log.setdefault("runs", []).append({"started_at": _now(), "upto": a.upto, "only": a.only})
    save_log(a.drama, log)

    print(f"=== 无人值守：{a.drama} ===")
    print(f"解析范围：{len(eps)} 集（{why}）")
    print(f"打开方式：{'只跑 ' + ','.join(a.only) if a.only else '从第一步跑到 ' + (a.upto or '最后一步')}")

    # 顺手把进度网页拉起来：挂一晚上，人第二天要能一眼看出跑到哪、有没有半夜死掉。
    # 起好之后还要替人**打开**——以前只打印一行地址，谁也不会有耐心去复制（11-16 补）。
    ui_url = ""
    if a.ui:
        ui_url, ui_state, ui_why = _ensure_ui(a.ui_port)
        if ui_state in ("new", "ok"):
            print(f"进度网页：{ui_url}" + ("（本来就在跑）" if ui_state == "ok" else "（刚拉起来）"))
            print("  页面每 3 秒自己刷，不用手动按。跑完这份记录也不会删，随时能回看。")
            if getattr(a, "open_ui", True):
                if _open_browser(ui_url):
                    print("  已经在这台机器的浏览器里打开了。")
                else:
                    print("  浏览器没能自动打开，把上面的地址复制到浏览器里就行。")
        else:
            print(f"进度网页没起起来（不影响干活）：{ui_why or '原因不明'}")
            print(f"  服务日志：{_ui_log()}")
            print("  跑完看报告一样。")
        if ui_url:
            log["ui_url"] = ui_url
            save_log(a.drama, log)
    print("")

    stop_at = ORDER.index(a.upto) if a.upto else len(ORDER) - 1
    t_all = time.time()
    stopped_reason = ""
    for idx, (name, label, kind) in enumerate(STAGES):
        if name == "report":
            continue
        if idx > stop_at:
            break
        if a.only and name not in a.only:
            continue
        print(f"[{idx+1}/{len(STAGES)-1}] {label} …", flush=True)
        _beat(a.drama, "run", "start", total=len(STAGES) - 1)
        _beat(a.drama, "run", "update", note=f"第 {idx+1} 步 {label}",
              idx=idx + 1, total=len(STAGES) - 1)
        t0 = time.time()
        stop_hb = _hb(a.drama, f"第 {idx+1} 步 {label}")
        try:
            res = AUTO[name](a)
        except Exception as e:
            res = dict(status="fail", detail=f"{type(e).__name__}: {e}")
        finally:
            stop_hb()
        sec = round(time.time() - t0, 1)
        rec = {"status": res.get("status"), "detail": res.get("detail", ""),
               "seconds": sec, "at": _now(),
               "artifacts": res.get("artifacts") or []}
        log.setdefault("stages", {})[name] = rec
        save_log(a.drama, log)
        icon = {"ok": "好", "skip": "跳过", "fail": "出错", "needs_model": "等你定"}.get(rec["status"], "?")
        print(f"      {icon}（{sec}s）{rec['detail'][:150]}", flush=True)

        if rec["status"] == "needs_model":
            stopped_reason = f"{label}——{rec['detail']}"
            break
        if rec["status"] == "fail" and (a.on_error == "stop" or name in HARD):
            why_hard = "这一步挂了后面全废" if name in HARD else ""
            stopped_reason = f"{label}出错——{rec['detail']}{('（' + why_hard + '）') if why_hard else ''}"
            break

    total = round(time.time() - t_all, 1)
    _beat(a.drama, "run", "finish",
          note=("停在 " + stopped_reason.split("——")[0]) if stopped_reason else "全部走完")
    print(f"\n这一趟跑了 {total} 秒。")
    if stopped_reason:
        print(f"停在：{stopped_reason}")
    else:
        print("这一段全部走完。")
    cmd_report(a)
    return 0


def cmd_report(a) -> int:
    log = load_log(a.drama)
    stages = log.get("stages") or {}
    lines = [f"# {a.drama} · 无人值守汇总报告", "",
             f"生成时间：{_now().replace('T', ' ')}", ""]

    lines += ["## 一、跑到哪了", "", "| 步骤 | 结果 | 耗时 | 说明 |", "|---|---|---|---|"]
    total = 0.0
    for name, label, kind in STAGES:
        if name == "report":
            continue
        rec = stages.get(name)
        if not rec:
            lines.append(f"| {label} | 没开始 | — | |")
            continue
        total += rec.get("seconds") or 0
        mark = {"ok": "好", "skip": "本来就在", "fail": "出错",
                "needs_model": "等人定"}.get(rec.get("status"), rec.get("status"))
        lines.append(f"| {label} | {mark} | {rec.get('seconds')}s | {rec.get('detail', '')} |")
    lines += ["", f"脚本耗时合计：{round(total,1)} 秒（不含我读片、写清单的时间）。", ""]

    lines += ["## 二、机器替你做的决定", ""]
    auto_notes = []
    if stages.get("check", {}).get("detail"):
        auto_notes.append(f"解析范围：{stages['check']['detail']}")
    if stages.get("segment", {}).get("status") == "ok":
        auto_notes.append("段落按脚本规则机械切（12–26 秒一段），没有逐段调")
    if stages.get("tag", {}).get("status") == "ok":
        auto_notes.append("画面打标按模型判断走，没有逐条人工核")
    if not auto_notes:
        auto_notes.append("这一趟没有替你做决定。")
    lines += [f"- {n}" for n in auto_notes] + [""]

    lines += ["## 三、需要你拍板的", ""]
    pending = [(LABEL.get(k, k), v) for k, v in stages.items()
               if v.get("status") == "needs_model"]
    if pending:
        lines += [f"- **{label}**：{v.get('detail')}" for label, v in pending]
    else:
        lines.append("- 这一趟没有需要你拍板的地方。")
    lines.append("")

    lines += ["## 四、真出错的地方", ""]
    broken = [(LABEL.get(k, k), v) for k, v in stages.items()
              if v.get("status") == "fail"]
    if broken:
        lines += [f"- **{label}**：{v.get('detail')}" for label, v in broken]
    else:
        lines.append("- 没有报错。")
    lines.append("")

    lines += ["## 五、钱花了多少", ""]
    tagrec = stages.get("tag") or {}
    tag_detail = str(tagrec.get("detail") or "")
    if tag_mode(a) == "off" or "没调接口" in tag_detail:
        lines.append("- 画面打标：**这一趟没开**（默认关，纯剪用不上）。"
                     "代价说清楚：句级表的「谁在说」只能按台词上下文推，"
                     "画内/画外会偏保守，选段主要靠台词文本。"
                     "要精确就加 `--tag` 单独补这一步。")
    elif tagrec.get("status") in ("ok", "skip"):
        lines.append("- 画面打标：按集调模型，是唯一花钱的一步。"
                     "具体金额看接口后台，本机只记「跑了几集」，不估金额。")
    lines.append("- 纯剪装配：0 元，本机 ffmpeg，不调任何接口。")
    lines.append("")

    lines += ["## 六、产物在哪", ""]
    made = []
    for v in stages.values():
        made += v.get("artifacts") or []
    if made:
        lines += [f"- `{m}`" for m in made]
    else:
        lines.append("- 还没有成片。")
    lines.append("")
    if log.get("ui_url"):
        lines += ["## 七、进度网页", "",
                  f"- {log['ui_url']}（跑完不关也能回看，页面每 3 秒自己刷）", ""]

    # 11-16 张老师指出：跑完了东西只在本机，项目资产里还是空的，换台机器得重跑一遍分析。
    # 所以报告里必须写清「该传什么」——传的动作要人点头，脚本不自己往网盘写。
    lines += ["## 八、该同步到项目资产的东西", ""]
    try:
        import sync_assets  # 同目录，只是个清单算法，不上传
        r = sync_assets.one(a.drama)
        pk, ix = r["pack"], r["index"]
        lines.append(f"- 剧集包 → `剧集包/{a.drama}/`："
                     + ("可传" if pk["ready"] else "还不能传")
                     + f"（{len(pk['files'])} 个文件）")
        for n in pk["notes"]:
            lines.append(f"  - {n}")
        lines.append(f"- 素材索引 → `素材索引/{a.drama}/`："
                     + ("齐" if ix["ready"] else "不齐")
                     + f"（{len(ix['dirs'])} 个目录 / {len(ix['files'])} 个单文件）")
        for n in ix["notes"]:
            lines.append(f"  - {n}")
        lines.append("- 不传：原片 mp4、抽帧图 edge_frames、ai_assets、build、.env。")
        lines.append("- 传之前要张老师点头；清单随时可复算："
                     f"`sync_assets.py --drama {a.drama}`。")
    except Exception as e:
        lines.append(f"- 清单没算出来：{type(e).__name__}: {e}")
    lines.append("")

    out = ROOT / "runs" / a.drama / "_autopilot" / "汇总报告.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n汇总报告：{out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="无人值守驾驶舱")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("status", "run", "report"):
        p = sub.add_parser(name)
        p.add_argument("--drama", required=True, help="剧名（素材目录名）")
        if name == "run":
            p.add_argument("--upto", choices=ORDER, help="跑到哪一步为止")
            p.add_argument("--only", nargs="+", choices=ORDER, help="只跑这几步")
            p.add_argument("--episodes", type=int, default=0, help="最多跑几集（测试用，0=全部）")
            p.add_argument("--jobs", type=int, default=4, help="台词抽取并行集数")
            p.add_argument("--tag-jobs", type=int, default=3, help="画面打标并行集数（默认 3，太多容易被限流）")
            p.add_argument("--tag", action="store_true",
                           help="这一趟调接口做画面打标（默认不做：纯剪用不上，白花钱）")
            p.add_argument("--force", action="store_true", help="已有产物也重跑（会烧钱，慎用）")
            p.add_argument("--on-error", choices=("continue", "stop"), default="continue")
            p.add_argument("--ui", action="store_true", default=True,
                           help="顺手把进度网页拉起来（默认开）")
            p.add_argument("--no-ui", dest="ui", action="store_false",
                           help="不启动网页，只看命令行输出")
            p.add_argument("--ui-port", type=int, default=8765)
            p.add_argument("--no-open", dest="open_ui", action="store_false", default=True,
                           help="起了网页但不自动打开浏览器（默认会替你打开）")
    a = ap.parse_args()
    return {"status": cmd_status, "run": cmd_run, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
