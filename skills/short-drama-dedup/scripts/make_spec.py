# -*- coding: utf-8 -*-
"""生成画面参数表 dedup_spec.json —— 去重技能的「准备」那半。

它只描述「画面该怎么改」，一个剪辑决策都不做：
不选段、不改段序、不改段长、不改台词、不动音频。

装配器 short-drama-trailer/scripts/mix_trailer.py 带 --dedup 读这份表，
在组滤镜图时挂上去，全程只编码一次。

用法：
    python3 make_spec.py --drama 以真心守护至亲 --task D3_线一认不认这个妈
    python3 make_spec.py --drama X --task Y --plan 别的清单.json --out 输出目录
    python3 make_spec.py --drama X --task Y --a1 "S04_那道疤,S07_独白"   # 手工指定，覆盖自动挑选
    python3 make_spec.py --drama X --task Y --no-sweep --dry             # 不叠扫光、只看结论
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: E402

HERE = Path(__file__).resolve().parent
CONFIG = HERE.parent / "config" / "视觉参数_默认.json"

CLOSE_SIZES = ("特写", "近景")


# ---------------------------------------------------------------- 读数据
def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def find_plan(root: Path, drama: str, task: str, plan_arg: str | None) -> Path:
    if plan_arg:
        p = Path(plan_arg).expanduser()
        if not p.is_absolute():
            p = (root / p) if (root / p).exists() else p.resolve()
        if not p.exists():
            sys.exit(f"清单不存在：{p}")
        return p
    task_dir = root / "runs" / drama / task
    for name in ("plan.json", "plan.silence.json"):
        if (task_dir / name).exists():
            return task_dir / name
    hits = sorted(task_dir.glob("plan*.json")) if task_dir.is_dir() else []
    if hits:
        return hits[0]
    sys.exit(f"在 {task_dir} 找不到清单。用 --plan 指一个。")


def load_sentences(root: Path, drama: str, eps: list[int]) -> dict[int, list[dict]]:
    """句级表按集读。缺了不影响出参数，只是挑段精度下降。"""
    out: dict[int, list[dict]] = {}
    base = root / "assets" / "short-drama" / drama / "sentences"
    for ep in eps:
        p = base / f"sentences_ep{ep:02d}.json"
        if p.exists():
            try:
                out[ep] = json.loads(p.read_text(encoding="utf-8")).get("rows") or []
            except Exception:
                out[ep] = []
    return out


# ---------------------------------------------------------------- 算指标
def cjk_len(s: str) -> int:
    return len(re.sub(r"\s", "", s or ""))


def seg_features(seg: dict, rows: list[dict]) -> dict:
    """一段的「画面语言」看三件事：镜头贴不贴人脸、话说得多密、句间有没有空隙。

    注意这里**不看「几个人说话」**。11-16 实测过：人工挑中的 5 段里 3 段都是两个人在说，
    拿说话人数判「是不是吵架」会把人想强调的段全排除掉。抢话的特征是句间空隙小，
    不是人多。
    """
    a, b = float(seg["a"]), float(seg["b"])
    dur = max(0.01, b - a)
    inside = [r for r in rows if a - 0.001 <= float(r.get("t") or 0) <= b + 0.001]

    speakers = {str(r.get("name") or "").strip() for r in inside}
    speakers.discard("")
    speakers.discard("none")
    speakers.discard("?")

    chars = sum(cjk_len(r.get("text") or "") for r in inside)
    sizes = [str(r.get("size") or "") for r in inside]
    close = sum(1 for s in sizes if any(k in s for k in CLOSE_SIZES))

    ticks = sorted(float(r.get("t") or 0) for r in inside)
    gaps = [ticks[i + 1] - ticks[i] for i in range(len(ticks) - 1)]

    return {
        "label": seg.get("label") or "",
        "ep": seg.get("ep"),
        "a": round(a, 3), "b": round(b, 3),
        "dur": round(dur, 3),
        "n_lines": len(inside),
        "n_spk": len(speakers),
        "speakers": sorted(speakers),
        "chars": chars,
        "density": round(chars / dur, 2),
        "line_rate": round(len(inside) / dur, 2),
        "gap_median": round(statistics.median(gaps), 2) if gaps else 0.0,
        "close_ratio": round(close / len(inside), 2) if inside else 0.0,
        # 句级表整集没有景别也没名字 = 这剧没开画面打标，判据要退化
        "no_tag": (not inside) or all(
            not str(r.get("size") or "") and not str(r.get("name") or "") for r in inside),
    }


def score_seg(f: dict, pick: dict) -> tuple[float, list[str]]:
    """给「套框适不适合」，并给出人话理由。

    **它判的是「套了会不会伤画面」，不是「叙事上最该强调哪一段」。**
    后者是人的活：11-16 实测，近景占 100% 的 5 段里人工只挑了 2 段，
    另外 3 段是动作或宣言，镜头贴着人脸但叙事重心不在那儿。机器不装懂。
    """
    if f["dur"] < pick["min_dur"]:
        return -99, [f"段长只有 {f['dur']:.1f}s，套框会一跳一跳，直接排除"]
    if f["dur"] > float(pick["long_dur"]) * 2:
        # 只扣分不够：D1 里有个 35.9s 的段扣完分照样入选，套框三十六秒人早麻了
        return -99, [f"段长 {f['dur']:.1f}s，超过 {float(pick['long_dur']) * 2:.0f}s，"
                     f"套框这么久会闷，直接排除"]

    if f["no_tag"]:
        # 没打标：没有景别，只能按台词节奏判。不装懂，写明降级。
        if f["line_rate"] <= pick["rate_quiet"]:
            return 1.5, [f"没开画面打标，看不出景别，只按节奏判：每秒 {f['line_rate']:.1f} 句，话稀"]
        return 0.0, [f"没开画面打标，看不出景别，只按节奏判：每秒 {f['line_rate']:.1f} 句，话密"]

    if f["close_ratio"] < float(pick["close_min"]):
        return -99, [f"近景特写只占 {f['close_ratio']:.0%}，中景全景居多——"
                     f"套框后画面变小，场面反而看不全，直接排除"]

    score, why = f["close_ratio"] * 3, [f"近景特写占 {f['close_ratio']:.0%}，镜头本来就贴着人脸"]
    if f["gap_median"] >= float(pick["gap_slow"]):
        score += 1.5
        why.append(f"句间中位间隔 {f['gap_median']:.1f}s，不是抢话")
    if f["line_rate"] <= float(pick["rate_quiet"]):
        score += 1.0
        why.append(f"每秒只 {f['line_rate']:.1f} 句，节奏慢")
    if f["dur"] > float(pick["long_dur"]):
        score -= 1.0
        why.append(f"段长 {f['dur']:.1f}s 偏长，套久了发闷，扣分")
    return round(score, 2), why


def pick_a1(segs: list[dict], feats: dict[str, dict], cfg: dict,
            forced: list[str], manual: list[str] | None) -> tuple[list[str], list[dict]]:
    pick = cfg["pick"]
    ep_segs = [s for s in segs if s.get("type") == "ep"]
    n_ep = len(ep_segs)
    cap = min(int(pick["max_count"]), max(1, math.ceil(n_ep * float(pick["ratio_max"]))))

    detail: list[dict] = []
    if manual is not None:
        # 人指定了就照人的来，但仍然逐条校验有没有这一段
        labels = [x for x in manual if x]
        bad = [x for x in labels if x not in {s.get("label") for s in ep_segs}]
        for x in labels:
            f = feats.get(x)
            detail.append({
                "label": x, "选中": x not in bad, "分数": None,
                "理由": "人手工指定" if x not in bad else "清单里没有这一段",
                "_指标": f,
                "_来源": "人手工指定",
            })
        if bad:
            sys.exit("这些段名在清单里找不到，检查一下：" + "、".join(bad))
        return labels, detail

    scored = []
    for s in ep_segs:
        lb = s.get("label") or ""
        f = feats.get(lb)
        if not f:
            continue
        sc, why = score_seg(f, pick)
        scored.append((sc, f["dur"], lb, f, why))
    scored.sort(key=lambda x: (-x[0], -x[1]))

    chosen: list[str] = []
    # 清单里标了 dedup:required 的段是硬要求：装配器会拦，必须进名单，不看分数，
    # 也不占下面的自动名额。
    for s in ep_segs:
        if str(s.get("dedup", "")).strip().lower() == "required":
            lb = s.get("label") or ""
            if lb and lb not in chosen:
                chosen.append(lb)

    auto_slots = max(0, cap - len(chosen))
    for sc, dur, lb, f, why in scored:
        if auto_slots <= 0:
            break
        if lb in chosen or sc < float(pick["score_threshold"]):
            continue
        chosen.append(lb)
        auto_slots -= 1

    picked = set(chosen)
    for sc, dur, lb, f, why in scored:
        detail.append({
            "label": lb,
            "选中": lb in picked,
            "分数": sc,
            "理由": "；".join(why) if why else "指标不足，不套",
            "_指标": f,
            "_来源": "清单标了必须去重" if lb in forced else "自动挑选",
        })
    # 保持片子顺序，便于人对着清单看
    order = {s.get("label"): i for i, s in enumerate(segs)}
    chosen.sort(key=lambda x: order.get(x, 999))
    return chosen, detail


# ---------------------------------------------------------------- 红线
def guard_check(cfg: dict, a1_count: int, n_ep: int) -> None:
    g = cfg["guard"]
    scale = float(cfg["a1"]["fg_scale"])
    if scale < float(g["fg_scale_min"]):
        sys.exit(f"配置里的主画面缩放 {scale} 低于红线 {g['fg_scale_min']}："
                 f"人物会被切、原剧字幕会糊。这条不许调，要改先讨论。")
    if n_ep:
        ratio = a1_count / n_ep
        if ratio > float(g["a1_ratio_max"]) + 1e-9:
            sys.exit(f"套框段数 {a1_count}/{n_ep} = {ratio:.0%}，超过红线 "
                     f"{float(g['a1_ratio_max']):.0%}：全片套框观感发闷。")


# ---------------------------------------------------------------- 扫光帧
def gen_sweep(outdir: Path, cfg: dict, W: int, H: int, fps: int) -> str | None:
    """渲染一段无缝循环的斜扫光，叠在整片最上面。

    尺寸按成片一半出（1080×1920 出 540×960），装配器会放大回去——省一半渲染时间，
    光斑本来就是软的，放大看不出。
    """
    sw = cfg.get("sweep") or {}
    if not sw.get("enabled", True):
        return None
    seq = outdir / "扫光帧"
    period = float(sw.get("period", 5.0))
    steps = max(2, int(fps * period))
    if sw.get("cache", True) and seq.is_dir():
        have = [p for p in seq.glob("*.png")]
        if len(have) == steps:
            print(f"  扫光帧已存在（{steps} 帧），跳过重画")
            return "扫光帧/%04d.png"

    import numpy as np
    from PIL import Image

    seq.mkdir(parents=True, exist_ok=True)
    for old in seq.glob("*.png"):
        old.unlink()

    lw, lh = max(2, W // 2), max(2, H // 2)
    peak = float(sw.get("peak", 0.12))
    wf = float(sw.get("width", 0.05))
    ang = math.radians(float(sw.get("angle", 32)))

    yy, xx = np.mgrid[0:lh, 0:lw].astype(np.float32)
    nx, ny = -math.sin(ang), math.cos(ang)
    tx, ty = math.cos(ang), math.sin(ang)
    proj = nx * xx + ny * yy
    pmin, pmax = float(proj.min()), float(proj.max())
    span = pmax - pmin
    along = tx * xx + ty * yy
    band = span * wf

    for k in range(steps):
        phase = k / steps
        center = pmin - band * 2 + (span + band * 4) * phase
        wobble = 16.0 * np.sin(along / 150.0 + phase * 2 * math.pi)
        g = np.exp(-((proj + wobble - center) / band) ** 2)
        rgba = np.zeros((lh, lw, 4), np.uint8)
        rgba[..., 0], rgba[..., 1], rgba[..., 2] = 255, 250, 235
        rgba[..., 3] = (g * peak * 255).astype(np.uint8)
        Image.fromarray(rgba, "RGBA").save(seq / f"{k:04d}.png")
    print(f"  扫光帧 {steps} 帧 / {period:g}s 一循环 / 峰值 {peak:.0%} / 宽 {wf:.0%} / 倾角 {sw.get('angle')}°")
    return "扫光帧/%04d.png"


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="生成画面参数表 dedup_spec.json（只改画面，不碰剪辑决策）")
    ap.add_argument("--drama", required=True, help="剧名，和 runs/ 下目录同名")
    ap.add_argument("--task", required=True, help="片子名，和 runs/<剧>/ 下目录同名")
    ap.add_argument("--plan", default=None, help="清单路径，默认自动找 runs/<剧>/<任务>/plan*.json")
    ap.add_argument("--out", default=None, help="输出目录，默认清单所在目录")
    ap.add_argument("--a1", default=None, help="手工指定套框段名，逗号分隔；给了就不用自动挑")
    ap.add_argument("--no-sweep", action="store_true", help="不叠扫光")
    ap.add_argument("--dry", action="store_true", help="只算不写文件")
    args = ap.parse_args()

    root = _env.data_root(create=True)
    cfg = load_config()
    if args.no_sweep:
        cfg.setdefault("sweep", {})["enabled"] = False

    plan_path = find_plan(root, args.drama, args.task, args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    segs = plan.get("segs") or []
    outdir = Path(args.out).expanduser() if args.out else plan_path.parent
    outdir.mkdir(parents=True, exist_ok=True)
    W, H, FPS = int(plan.get("w", 720)), int(plan.get("h", 1280)), int(plan.get("fps", 24))

    eps = sorted({int(s["ep"]) for s in segs if s.get("type") == "ep" and s.get("ep")})
    sents = load_sentences(root, args.drama, eps)
    missing = [e for e in eps if e not in sents]
    print(f"清单：{plan_path}")
    print(f"  {len(segs)} 段，其中原片段 {len([s for s in segs if s.get('type') == 'ep'])} 段，"
          f"涉及 {len(eps)} 集；画面 {W}×{H} @{FPS}fps")

    feats = {}
    for s in segs:
        if s.get("type") != "ep" or not s.get("ep"):
            continue
        f = seg_features(s, sents.get(int(s["ep"]), []))
        feats[f["label"]] = f
    notag = [lk for lk, f in feats.items() if f["no_tag"]]
    if missing or notag:
        print(f"  ⚠ 这些集没有句级表，挑段只能看段长：{missing}" if missing else "")
        if notag:
            print(f"  ⚠ {len(notag)} 段所在集没开画面打标，只按台词密度判，精度会低。"
                  f"要判准就把 .env 的 SHORT_DRAMA_TAG 改成 on 重跑索引。")

    forced = [s.get("label") or "" for s in segs
              if str(s.get("dedup", "")).strip().lower() == "required"]
    manual = None
    if args.a1:
        manual = [x.strip() for x in re.split(r"[,，\s]+", args.a1) if x.strip()]
    labels, detail = pick_a1(segs, feats, cfg, forced, manual)
    n_ep = len([s for s in segs if s.get("type") == "ep"])
    guard_check(cfg, len(labels), n_ep)
    cap = min(int(cfg["pick"]["max_count"]),
              max(1, math.ceil(n_ep * float(cfg["pick"]["ratio_max"]))))

    # ---- 先报告挑了什么、为什么。--dry 也要看到这张表，不然空跑没意义 ----
    if labels:
        print(f"\n套框 {len(labels)} 段（上限 {cap} 段）：")
    else:
        print("\n套框 0 段。")
        if notag and manual is None:
            print("  原因：这剧没开画面打标，看不出景别。硬套框可能把大场面压小、反而伤画面，")
            print("        所以一段都没套——宁可少做一层，也不拿叙事质量换去重读数。")
            print("  这次仍有：全局参数包（裁 8px、暗角、亮度抖动）＋ "
                  f"{'扫光' if cfg.get('sweep', {}).get('enabled', True) else '无扫光'}，"
                  "这两样对画面指纹也有实际作用。")
            print("  想要画中画，两条路：")
            print("    ① .env 写 SHORT_DRAMA_TAG=on 重跑素材索引，让机器看得出景别；")
            print('    ② 自己过一遍片子，用 --a1 "段名,段名" 指定哪几段套。')
        else:
            print("  没有段达到条件（段太短，或中景全景居多）。")
    for d in detail:
        mark = "✓" if d["选中"] else "·"
        print(f"  {mark} {d['label']:<22} {d['理由']}")

    if args.dry:
        # --dry 什么都不写，扫光帧也不画：那是 150 张 PNG，不算「只看结论」
        if cfg.get("sweep", {}).get("enabled", True):
            steps = int(FPS * float(cfg["sweep"].get("period", 5.0)))
            print(f"\n（--dry 未画扫光帧；真跑会画 {steps} 张到 {outdir / '扫光帧'}）")
        print("\n--dry：没写文件。")
        return 0

    sweep_pattern = gen_sweep(outdir, cfg, W, H, FPS)

    spec = {
        "_说明": "去重技能产出的画面参数表。装配器只负责挂上去，不做任何剪辑决策。",
        "_来源": {"技能": "short-drama-dedup", "清单": plan_path.name,
                  "配置": "config/视觉参数_默认.json"},
        "_红线": "不改段序、段长、台词、五幕、原剧字幕；音频不动；不因去重降低剪辑质量。",
        "_挑选方式": ("人手工指定" if manual is not None
                      else "自动挑选（景别占比 + 句间间隔 + 语速）；只判套了会不会伤画面，"
                           "判不出叙事上最该强调哪段，要按故事线挑请用 --a1 指定"),
        "a1_segments": labels,
        "a1": {k: v for k, v in cfg["a1"].items() if not k.startswith("_")},
        "global": {k: v for k, v in cfg["global"].items() if not k.startswith("_")},
        # 扫光只吐装配器认的字段。enabled/cache 是技能自己的开关，不该漏进参数表。
        "sweep": ({"peak": cfg["sweep"].get("peak"), "period": cfg["sweep"].get("period"),
                   "width": cfg["sweep"].get("width"), "angle": cfg["sweep"].get("angle"),
                   "pattern": sweep_pattern, "framerate": FPS} if sweep_pattern else {}),
        "decorate": {},
        "_挑选明细": detail,
        "_说明_挑选明细": "每段为什么套框/不套框，给人过目用；装配器不读这一段。",
    }
    # decorate 留给修饰技能填，本技能不产任何文字

    sp = outdir / "dedup_spec.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写 {sp}")
    print(f"装配时加：--dedup {sp}")
    print("下一步必做：装配前先 --dry 空跑一次，再看一眼对照图确认字幕清干净、人物没被切。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
