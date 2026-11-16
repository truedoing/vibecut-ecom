#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按「台词句子」定切点，一次出一整批清单。

为什么是它：手写 a/b 秒数一定切半句（句子长度不固定，你以为 12.0 是句尾，实际念到 13.2），
切半句只能靠事后听辨发现，一次几分钟。这个工具把切点算在两句之间的空隙里，物理上切不半句。

10-11 版只做了一件事：算秒数。清单里的编号、三拍说明还得一段一段手写，
一条 13-15 段、10 条就是 140 段纯机械转录。这一版把那些都收进来：

  你只写：这一集，从哪句，到哪句。
  工具给：编号、三拍说明、秒数（切在句间空隙）、出点收在视频流内、结构检查、完整 plan。

有意为之的一条：**结构错误在这一步就出不来**。同一集里一段被另一段整段包含
（等于同一段播两遍）、同集连着拼三段（平台禁大段拼接原片）、段与段部分重叠——
这三种以前是排完清单再靠自审事后抓，抓到已经浪费一轮。现在生成时就报，退出码非 0。

spec 最小形态（label / beats 都可省，省了自动生成）：
  {"drama": "以真心守护至亲", "title": "我妹妹", "tier": "main",
   "angle": "一句话说这条线从谁的眼睛看",
   "items": [
     {"ep": 1, "from": "我把50斤黄金", "to": "还认不认我这个穷妈"},
     {"ep": 3, "from": "这婚我不结了", "to": "你也配"}
   ]}

用法：
  # 单条，只算切点看结果
  python3 pickpoints.py --spec spec.json
  # 单条，直接出完整 plan
  python3 pickpoints.py --spec spec.json --plan runs/<剧>/T0101/plan.json
  # 一批 (10 条一起算，一次报完所有问题)
  python3 pickpoints.py --spec-dir runs/<剧>/specs --plan-dir runs/<剧>
"""
import argparse
import glob
import json
import os
import re
import sys

import epdur

LEAD = 0.35       # 入点比句子起点提前多少秒（留一点气口）
TAIL = 0.30       # 出点比下一句起点提前多少秒
LAST_PAD = 1.6    # 最后一句没有下一句时，往后放多少秒
MAX_RUN = 2       # 同一集在清单里最多连着拼几段（平台禁大段拼接原片）
PUNCT = r"[，。？！、,.?!…\s\"“”‘’：；:;（）()「」《》\-—]"


# ---------- 基础 ----------

def lines_path(drama, ep):
    return os.path.join(epdur.ROOT, "assets", "short-drama", drama, "lines",
                        f"lines_ep{ep:02d}.json")


def load_lines(drama, ep):
    p = lines_path(drama, ep)
    if not os.path.exists(p):
        raise SystemExit(f"读不到台词库：{os.path.relpath(p, epdur.ROOT)}")
    return json.load(open(p, encoding="utf-8"))


def locate(lines, frag, which, ep):
    hits = [i for i, l in enumerate(lines) if frag in l["text"]]
    if not hits:
        raise SystemExit(f"ep{ep} {which}「{frag}」在台词库里找不到。"
                         f"　（写台词里的任意片段，能唯一定位一句就行）")
    if len(hits) > 1:
        raise SystemExit(f"ep{ep} {which}「{frag}」有 {len(hits)} 句都能匹配：\n  "
                         + "\n  ".join(f"[{h}] {lines[h]['text']}" for h in hits)
                         + "\n　→ 多写几个字")
    return hits[0]


# ---------- 一条段 ----------

def pick(drama, item, vd=None):
    ep = int(item["ep"])
    doc = load_lines(drama, ep)
    lines = doc["lines"]
    i = item.get("i")
    j = item.get("j")
    if i is None:
        i = locate(lines, item["from"], "入点", ep)
    if j is None:
        j = locate(lines, item["to"], "出点", ep)
    i, j = int(i), int(j)
    if j < i:
        raise SystemExit(f"ep{ep} 出点在第 {j} 句、入点在第 {i} 句，顺序反了")

    a = max(0.0, lines[i]["t"] - LEAD)
    if j + 1 < len(lines):
        b = lines[j + 1]["t"] - TAIL
    else:
        b = lines[j]["t"] + LAST_PAD

    vd = vd if vd is not None else epdur.of(drama, ep)
    clamped = False
    if vd is not None and b > vd:
        b = round(vd - 0.1, 2)
        clamped = True

    texts = [l["text"] for l in lines[i:j + 1]]
    return {
        "ep": ep,
        "a": round(a, 2),
        "b": round(b, 2),
        "dur": round(b - a, 2),
        "from_idx": i,
        "to_idx": j,
        "from_text": lines[i]["text"],
        "to_text": lines[j]["text"],
        "gap_in": round(lines[i]["t"] - a, 2),
        "gap_out": round(b - lines[j]["t"], 2),
        "video_dur": vd,
        "clamped": clamped,
        "tail_gap": round(vd - b, 3) if vd is not None else None,
        "_texts": texts,
    }


def auto_label(k, item, r):
    if item.get("label"):
        return item["label"]
    s = re.sub(PUNCT, "", r["from_text"])
    return f"S{k:02d}_{s[:9]}"


def auto_beats(item, r):
    if item.get("beats"):
        return item["beats"]
    t = r["_texts"]
    if len(t) == 1:
        return f"落点={t[0]}"
    mid = max(t[1:-1], key=len) if len(t) > 2 else t[-1]
    return f"挑起={t[0]}｜交锋={mid}｜落点={t[-1]}"


# ---------- 结构检查（生成时就拦，不留给事后自审） ----------

def check_struct(segs):
    errs, warns = [], []
    n = len(segs)

    # 1. 同集：一段被另一段整段包含 → 同一段会播两遍
    for x in range(n):
        for y in range(x + 1, n):
            u, v = segs[x], segs[y]
            if u["ep"] != v["ep"]:
                continue
            lo, hi = (u, v) if u["dur"] >= v["dur"] else (v, u)
            if lo["a"] <= hi["a"] + 1e-6 and lo["b"] >= hi["b"] - 1e-6:
                if abs(lo["dur"] - hi["dur"]) < 1e-6:
                    errs.append(f"ep{hi['ep']}：{hi['label']} 与 {lo['label']} 是同一段（取了两遍）")
                else:
                    errs.append(f"ep{hi['ep']}：{hi['label']}（{hi['a']}–{hi['b']}）"
                                f"被 {lo['label']}（{lo['a']}–{lo['b']}）整段包含，同一段会播两遍")

    # 2. 同集：部分重叠
    for x in range(n):
        for y in range(x + 1, n):
            u, v = segs[x], segs[y]
            if u["ep"] != v["ep"]:
                continue
            ov = min(u["b"], v["b"]) - max(u["a"], v["a"])
            if ov > 0.05:
                contained = ((u["a"] <= v["a"] and u["b"] >= v["b"]) or
                             (v["a"] <= u["a"] and v["b"] >= u["b"]))
                # 相邻两段首尾相接时天然咬合一两帧（入点留气口、出点提前收），
                # 那不是"同一段播两遍"，别误判。
                touching = (abs(u["a"] - v["b"]) <= 0.2 or abs(v["a"] - u["b"]) <= 0.2)
                if not contained and not touching:
                    errs.append(f"ep{u['ep']}：{u['label']} 与 {v['label']} 重叠 {ov:.1f}s")

    # 3. 同集连着拼太多段（平台禁大段拼接原片）
    run = 1
    for k in range(1, n):
        prev, cur = segs[k - 1], segs[k]
        if cur["ep"] == prev["ep"] and abs(cur["a"] - prev["b"]) <= 1.2:
            run += 1
            if run > MAX_RUN:
                errs.append(f"ep{cur['ep']}：连着拼了 {run} 段（上限 {MAX_RUN}），"
                            f"平台禁大段拼接原片　→ 中间插一段别的集，或合并成一段")
        else:
            run = 1

    # 4. 出点被收到视频流内 / 贴着视频流尾（这些只报，不拦）
    for s in segs:
        if s["clamped"]:
            warns.append(f"{s['label']}：出点超过视频流 {s['video_dur']:.2f}s，已收到 {s['b']}")
        elif s["tail_gap"] is not None and s["tail_gap"] < 0.2:
            warns.append(f"{s['label']}：出点贴视频流尾（差 {s['tail_gap']:.2f}s），出片要看末帧")

    # 5. 段本身太短，观众看不清一次冲突
    for s in segs:
        if s["dur"] < 4:
            warns.append(f"{s['label']}：只有 {s['dur']:.1f}s，可能不够一次完整的交锋")
    return errs, warns


# ---------- 一整条 ----------

def build(spec, spec_stem=""):
    drama = spec["drama"]
    items = spec["items"]
    # 一次把这条用到的集全取齐，别每段重读一遍缓存
    durs = epdur.ensure(drama, spec.get("src_dir"),
                        eps={int(it["ep"]) for it in items})
    segs, seg_errs = [], []
    for k, item in enumerate(items, 1):
        try:
            r = pick(drama, item, durs.get(int(item["ep"])))
        except SystemExit as e:
            # 一段取不到，别的段照算：一次把所有问题报全，
            # 否则修一个跑一遍，一条 spec 三个错就要跑三遍。
            seg_errs.append(f"第 {k} 段（ep{item.get('ep')}）：{str(e).splitlines()[0]}")
            continue
        r["label"] = auto_label(k, item, r)
        r["beats"] = auto_beats(item, r)
        r["note"] = item.get("note", "")
        segs.append(r)
    errs, warns = check_struct(segs)
    errs = seg_errs + errs
    total = sum(s["dur"] for s in segs)
    return {
        "drama": drama,
        "title": spec.get("title") or spec_stem,
        "tier": spec.get("tier", "main"),
        "angle": spec.get("angle", ""),
        "spec_stem": spec_stem,
        "segs": segs,
        "total": total,
        "errors": errs,
        "warnings": warns,
    }


def plan_doc(b):
    """转成装配器能直接吃的 plan。素材目录只写剧名，搬家不影响。"""
    return {
        "drama": b["drama"],
        "title": b["title"],
        "tier": b["tier"],
        "angle": b["angle"],
        "ai_strategy": "none",
        "src_dir": b["drama"],
        "card": "build/card.png",
        "w": 1080, "h": 1920, "fps": 30,
        "notes_doc": "../送审说明.md",
        "review_doc": "清单过目.md",
        "segs": [{"label": s["label"], "type": "ep", "ep": s["ep"],
                  "a": s["a"], "b": s["b"], "beats": s["beats"]} for s in b["segs"]],
    }


def show(b):
    tid = b["spec_stem"] or b["title"]
    print(f"\n=== {tid}「{b['title']}」  {len(b['segs'])} 段  "
          f"{b['total']:.1f}s = {int(b['total']//60)}分{b['total']%60:.0f}秒 ===")
    if b["angle"]:
        print(f"    角度：{b['angle']}")
    for s in b["segs"]:
        flag = "  ⚠已收" if s["clamped"] else ("  ⚠贴尾" if (s["tail_gap"] is not None and s["tail_gap"] < 0.2) else "")
        print(f"    {s['label']:<24} ep{s['ep']:>2} {s['a']:>7.2f}-{s['b']:>7.2f} "
              f"({s['dur']:>5.1f}s){flag}")
    for e in b["errors"]:
        print(f"    ✗ {e}")
    for w in b["warnings"]:
        print(f"    · {w}")
    if not b["errors"]:
        print("    ✓ 结构检查通过（无包含/重叠/连续超标）")


def load_specs(args):
    paths = list(args.spec or [])
    if args.spec_dir:
        paths += sorted(glob.glob(os.path.join(args.spec_dir, "*.json")))
    if not paths:
        raise SystemExit("要 --spec 或 --spec-dir")
    out = []
    for p in paths:
        spec = json.load(open(p, encoding="utf-8"))
        if "items" not in spec:
            raise SystemExit(f"{p} 里没有 items")
        out.append((p, spec))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", action="append", help="spec JSON，可给多个")
    ap.add_argument("--spec-dir", help="一次算一个目录下所有 spec（10 条一起）")
    ap.add_argument("--plan", help="单条时写出的 plan 路径")
    ap.add_argument("--plan-dir", help="批量时 plan 写到这里：<plan-dir>/<spec 文件名去扩展名>/plan.json")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    specs = load_specs(args)
    built, fails, all_err = [], [], 0
    for path, spec in specs:
        stem = os.path.splitext(os.path.basename(path))[0]
        try:
            b = build(spec, stem)
        except SystemExit as e:
            # 一条写错不该把整批带停：10 条里坏一条，另外 9 条照出，
            # 最后一起报。否则光在等前面 9 条白跑。
            print(f"\n=== {stem}  ✗ {e}")
            fails.append((stem, str(e)))
            all_err += 1
            continue
        except Exception as e:
            print(f"\n=== {stem}  ✗ {type(e).__name__}: {e}")
            fails.append((stem, f"{type(e).__name__}: {e}"))
            all_err += 1
            continue
        built.append((path, b))
        show(b)
        all_err += len(b["errors"])

        plan_path = None
        if args.plan and len(specs) == 1:
            plan_path = args.plan
        elif args.plan_dir:
            plan_path = os.path.join(args.plan_dir, stem, "plan.json")
        if plan_path:
            if b["errors"]:
                # 有错就不落 plan：残缺的清单流到装配环节比报错更贵。
                # 但也不能把上一版留在原地——那会让人以为它是最新的，
                # 拿一条已经跟 spec 对不上的清单去装配。
                print(f"    （有 {len(b['errors'])} 处错，不写 plan）")
                if os.path.exists(plan_path):
                    bad = plan_path.replace(".json", ".invalid.json")
                    os.replace(plan_path, bad)
                    print(f"    （上一版已改名为 {os.path.basename(bad)}，别拿去装配）")
            else:
                os.makedirs(os.path.dirname(os.path.abspath(plan_path)), exist_ok=True)
                json.dump(plan_doc(b), open(plan_path, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)
                print(f"    → {os.path.relpath(plan_path, epdur.ROOT)}")

    print(f"\n---- 成功 {len(built)} 条 / 没出来 {len(fails)} 条，"
          f"{sum(b['total'] for _, b in built):.0f}s，"
          f"{sum(len(b['errors']) for _, b in built)} 处结构错误 ----")
    for stem, msg in fails:
        print(f"  ✗ {stem}：{msg.splitlines()[0][:110]}")
    if args.json:
        print(json.dumps([{"spec": os.path.basename(p), "title": b["title"],
                           "total": round(b["total"], 1),
                           "segs": len(b["segs"]),
                           "errors": b["errors"], "warnings": b["warnings"]}
                          for p, b in built], ensure_ascii=False, indent=1))
    sys.exit(1 if all_err else 0)


if __name__ == "__main__":
    main()
