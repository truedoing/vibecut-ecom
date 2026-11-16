#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交人之前的自审。分两层：选题层（select）与清单层（plan）。

为什么要有这个：技能原来的「自检六问」是自己问自己，答什么都行。
自审必须能算出数、能判不过、能给出改法，否则就是对自己说好话。

用法：
  选题层  python3 selfcheck.py --select select.json --drama 以真心守护至亲
  清单层  python3 selfcheck.py --plan plan.json

退出码：0 = 全过（可以交人）；1 = 有不过项（改完再来，最多 3 轮）；
        2 = 连跑都跑不起来（缺文件/格式错）。

判据阈值集中在 RULES，改阈值只改这一处。
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys

# --fresh：把这部剧当新剧处理，老片子的素材占用一律不计。
# 用途：每轮测试清空历史，只验本轮；也用于「同剧换角度再出几条」的场景。
FRESH = False

RULES = {
    # 时长档位（秒）。主线五幕齐，副线只取三幕。
    "main_len": (240, 360),
    "sub_len": (110, 190),
    # 与已出片子的素材重叠上限。超过就是同一批画面再剪一遍。
    # 注意：同一批镜头换角度重排是允许的（一部剧能出 10 条），
    # 所以这条只在「不按新剧处理」时才查；--fresh 时整项跳过。
    "overlap_max": 0.30,
    # 同一批里三条选题互相的重叠上限。角度不同可以共用，但别糊成一条。
    "cross_max": 0.40,
    # 同一集最多连着用几段原片（平台禁大段拼接）。
    "consec_same_ep": 2,
    # 钩子检查看前多少秒。
    "hook_window": 45.0,
    "max_rounds": 3,
}

# 「有人对主角说不」的信号。不是找喜剧反差，是找对抗与拒绝。
REJECT = re.compile(
    r"(滚|凭什么|你也配|没门|做梦|闭嘴|少来|出去|打断|别(?=再|想|来|碰|管|怪|给)|"
    r"不行|不给|不算|不管|不认|不记得|不嫁|不结|不退|没完|谁再|少管|用不着|轮不到)"
)
# 「反击」的信号：主角把话顶回去。
FIGHT_BACK = re.compile(r"(我(就|偏|不|没|说了|问)|凭(什么|啥)|合同|写的是我|你(敢|试试)|谁怕谁)")


def load_drama(name):
    """优先读 JSON 台词库（唯一真源），没有再退回 sqlite。"""
    base = os.path.join("assets", "short-drama", name)
    lines = {}
    ldir = os.path.join(base, "lines")
    if os.path.isdir(ldir):
        for f in sorted(os.listdir(ldir)):
            m = re.match(r"lines_ep(\d+)\.json$", f)
            if not m:
                continue
            ep = int(m.group(1))
            d = json.load(open(os.path.join(ldir, f), encoding="utf-8"))
            lines[ep] = [(float(x["t"]), x["text"]) for x in d.get("lines", [])]
    if lines:
        return lines
    db = os.path.join(base, "index.sqlite")
    if os.path.isfile(db):
        c = sqlite3.connect(db)
        for ep, t, txt in c.execute("select ep, t, text from lines order by ep, t"):
            lines.setdefault(ep, []).append((float(t), txt))
    return lines


def _ffprobe_first():
    """统一走 _env（数据目录 bin/ → 系统 PATH）。不再自己猜本机路径。"""
    try:
        from _env import ffprobe
        p = ffprobe()
        if os.path.isabs(p) and os.path.exists(p):
            return [p, "ffprobe"]
    except Exception:
        pass
    return ["ffprobe"]


def video_dur(path):
    for exe in _ffprobe_first():
        try:
            r = subprocess.run(
                [exe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=duration", "-of", "csv=p=0", path],
                capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                return float(r.stdout.strip().splitlines()[0])
        except FileNotFoundError:
            continue
    return None


def relocate_src(src_dir):
    """素材整目录被搬走时，按「路径末段＝剧名」查 config/drama-sources.json 找回。
    与 mix_trailer.py 同一套规则，免得两个脚本对同一份清单给出不同答案。"""
    if not src_dir or os.path.isdir(src_dir):
        return src_dir
    key = os.path.basename(os.path.normpath(src_dir))
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        p = os.path.join(d, "config", "drama-sources.json")
        if os.path.isfile(p):
            try:
                alt = json.load(open(p, encoding="utf-8")).get("dramas", {}).get(key)
            except Exception:
                return src_dir
            if alt and os.path.isdir(os.path.expandvars(alt)):
                return os.path.expandvars(alt)
            return src_dir
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return src_dir


def ep_index(src_dir):
    """集号 -> 文件路径。兼容 第N集 / ep_N / 纯数字 N。"""
    out = {}
    if not src_dir or not os.path.isdir(src_dir):
        return out
    exts = (".mp4", ".mov", ".mkv", ".m4v")
    for f in sorted(os.listdir(src_dir)):
        if not f.lower().endswith(exts):
            continue
        stem = os.path.splitext(f)[0].strip()
        if stem.isdigit():
            out[int(stem)] = os.path.join(src_dir, f)
            continue
        m = re.search(r"第\s*0*(\d+)集", f) or re.search(r"(?i)ep(?:isode)?[\s_.-]*0*(\d+)", f)
        if m:
            out[int(m.group(1))] = os.path.join(src_dir, f)
    return out


def merged_len(spans):
    """把 [[a,b],...] 合并成不重叠区间后求总长。"""
    if not spans:
        return 0.0
    ss = sorted(spans)
    out = [list(ss[0])]
    for a, b in ss[1:]:
        if a <= out[-1][1] + 0.05:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return sum(b - a for a, b in out)


def total_len(segs):
    """(ep,a,b) 的素材总长。必须按集分组再合并——不同集的同一秒不是重复。
    11-12 踩过：不分组直接合并，三条选题的素材量全被算成 50 秒左右。"""
    from collections import defaultdict
    by = defaultdict(list)
    for ep, a, b in segs:
        by[ep].append((a, b))
    return sum(merged_len(v) for v in by.values())


def overlap_len(xs, ys):
    """两组 (ep,a,b) 的重叠总长。两侧都先并区间，避免同一段被重复计入。"""
    from collections import defaultdict
    by = defaultdict(list)
    for ep, a, b in ys:
        by[ep].append((a, b))
    total = 0.0
    for ep, a, b in xs:
        pieces = []
        for c, d in by.get(ep, []):
            lo, hi = max(a, c), min(b, d)
            if hi - lo > 0.05:
                pieces.append((lo, hi))
        total += merged_len(pieces)
    return total


def hook_window_segs(segs, window):
    """按装配后的时间轴取前 window 秒，返回 [(ep, a, b_take)]。
    不能拿每段自己的原片时间跟 45 秒比——那样后面集的段也会被当成前 45 秒。"""
    out, elapsed = [], 0.0
    for ep, a, b in segs:
        if elapsed >= window:
            break
        take = min(b - a, window - elapsed)
        if take > 0.05:
            out.append((ep, a, a + take))
        elapsed += take
    return out


def scan_hook(lines, segs, window):
    """前 window 秒里，是否存在「有人说不」或「主角顶回去」。"""
    hits = []
    for ep, a, b in hook_window_segs(segs, window):
        for t, txt in lines.get(ep, []):
            if a <= t <= b:
                tag = "拒绝" if REJECT.search(txt) else ("反击" if FIGHT_BACK.search(txt) else None)
                if tag:
                    hits.append((ep, round(t, 1), tag, txt))
    return hits


def collect_segs(items):
    out = []
    for it in items:
        if it.get("type") and it["type"] != "ep":
            continue
        if "segs" in it:  # 选题里的候选段 [[ep,a,b],...]
            for s in it["segs"]:
                out.append((int(s[0]), float(s[1]), float(s[2])))
        elif "ep" in it:
            out.append((int(it["ep"]), float(it["a"]), float(it["b"])))
    return out


def load_emitted(runs_root, exclude_dirs=()):
    """已出成片用过的素材段。目录名以 D 开头视为成品。
    一个目录里有多个 plan 时只取最新改动的那个——同目录常有废弃的旧版，
    把新旧版一起算会把重叠率算高（11-12 踩过：算出 107% 这种不可能的数）。"""
    out = {}
    if not runs_root or not os.path.isdir(runs_root):
        return out
    for d in sorted(os.listdir(runs_root)):
        p = os.path.join(runs_root, d)
        if not os.path.isdir(p) or d in exclude_dirs:
            continue
        if not re.match(r"^D\d", d):
            continue
        cands = [os.path.join(p, f) for f in os.listdir(p) if re.match(r"plan.*\.json$", f)]
        if not cands:
            continue
        newest = max(cands, key=os.path.getmtime)
        try:
            pl = json.load(open(newest, encoding="utf-8"))
        except Exception:
            continue
        segs = [(int(s["ep"]), float(s["a"]), float(s["b"]))
                for s in pl.get("segs", []) if s.get("type") == "ep"]
        if segs:
            out[d] = segs
    return out


def fmt(sec):
    return f"{int(sec // 60)}分{sec % 60:04.1f}秒"


class Report:
    def __init__(self):
        self.rows = []

    def add(self, item, ok, detail, fix=""):
        self.rows.append({"项": item, "结果": "通过" if ok else "不过", "说明": detail, "改法": fix})
        return ok

    @property
    def ok(self):
        return all(r["结果"] == "通过" for r in self.rows)

    def show(self, title):
        print(f"\n===== 自审 · {title} =====")
        for r in self.rows:
            print(f"[{r['结果']}] {r['项']}：{r['说明']}")
            if r["改法"]:
                print(f"        → {r['改法']}")
        print(f"\n结论：{'全部通过，可以交人' if self.ok else '有不过项，改完再自审（最多 3 轮）'}")


def check_select(doc, drama, runs_root):
    rep = Report()
    lines = load_drama(drama)
    if not lines:
        print(f"读不到《{drama}》的台词库，自审做不了", file=sys.stderr)
        sys.exit(2)
    emitted = load_emitted(runs_root)
    topics = doc["topics"]

    # 1. 每条选题的素材量够不够它自己声明的档位
    for t in topics:
        segs = collect_segs([t])
        need = RULES["main_len"] if t.get("tier") == "main" else RULES["sub_len"]
        got = total_len(segs)
        ok = got >= need[0]
        rep.add(f"{t['id']} 素材量够档（{t.get('tier')}档要 ≥{need[0]}s）", ok,
                f"可用 {got:.0f}s（{fmt(got)}），{len(segs)} 段",
                "" if ok else f"补段到 {need[0]}s 以上，或如实降档为副线并在清单里写明「素材所限」")

    # 2. 与已出成片的画面重叠（--fresh 时整项跳过：把这部剧当新剧）
    for t in (topics if not FRESH else []):
        segs = collect_segs([t])
        tot = total_len(segs)
        worst, worst_name = 0.0, ""
        for name, old in emitted.items():
            ov = overlap_len(segs, old) / tot if tot else 0
            if ov > worst or not worst_name:
                worst, worst_name = ov, name
        ok = worst <= RULES["overlap_max"]
        rep.add(f"{t['id']} 与已出片子重叠 ≤{RULES['overlap_max']*100:.0f}%", ok,
                f"最高 {worst*100:.0f}%（与 {worst_name}）" if worst_name else "无已出片子可比",
                "" if ok else "换角度或换素材；换不动就如实标「重复率高」，别当新片交")

    # 3. 三条之间别撞成一条
    #    --fresh 时只报重叠率、不判死：同剧多条本来就共用镜头，
    #    判据是「角度/主线/落点是否不同」，那是人判的，机器不该越权判。
    for i in range(len(topics)):
        for j in range(i + 1, len(topics)):
            a = collect_segs([topics[i]])
            b = collect_segs([topics[j]])
            ta = merged_len([(x, y) for _, x, y in a])
            ov = overlap_len(a, b) / ta if ta else 0
            if FRESH:
                rep.add(f"{topics[i]['id']} 与 {topics[j]['id']} 素材重叠", True,
                        f"共用 {ov*100:.0f}%（本轮允许复用，角度差异需人判）", "")
            else:
                ok = ov <= RULES["cross_max"]
                rep.add(f"{topics[i]['id']} 与 {topics[j]['id']} 别撞车 ≤{RULES['cross_max']*100:.0f}%", ok,
                        f"重叠 {ov*100:.0f}%",
                        "" if ok else "两条选题取段太近，换一条的切入集或换角度")

    # 4. 每条选题前 45 秒有没有人说不
    for t in topics:
        segs = sorted(collect_segs([t]))
        hits = scan_hook(lines, segs, RULES["hook_window"])
        ok = bool(hits)
        detail = "；".join(f"ep{e} {t_}s {tag}「{txt[:14]}」" for e, t_, tag, txt in hits[:3]) if hits else "前 45 秒没有任何拒绝或反击"
        rep.add(f"{t['id']} 前 45 秒有人说不", ok, detail,
                "" if ok else "换一段带对抗的开场；换不出来说明这条线本身没钩子，退回选题")

    return rep


def check_plan(doc, drama, src_dir, runs_root, plan_dir):
    rep = Report()
    lines = load_drama(drama)
    segs = [(int(s["ep"]), float(s["a"]), float(s["b"]), s.get("label", ""))
            for s in doc.get("segs", []) if s.get("type") == "ep"]
    if not segs:
        print("这份清单里没有原片段", file=sys.stderr)
        sys.exit(2)

    # 1. 出点不超视频流（铁律）
    src_dir = relocate_src(src_dir)
    idx = ep_index(src_dir)
    cache, bad = {}, []
    for ep, a, b, lab in segs:
        if ep not in idx:
            bad.append(f"ep{ep} 找不到素材文件")
            continue
        if ep not in cache:
            cache[ep] = video_dur(idx[ep])
        vd = cache[ep]
        if vd is not None and b > vd + 0.001:
            bad.append(f"{lab} 出点 {b} > 视频流 {vd:.2f}")
    rep.add("出点不超视频流时长", not bad, "全部在内" if not bad else "；".join(bad),
            "" if not bad else "把出点收到视频流时长以内 0.1 秒，别用音频流长度")

    # 2. 时长对档
    tier = doc.get("tier", "main")
    need = RULES["main_len"] if tier == "main" else RULES["sub_len"]
    total = sum(b - a for _, a, b, _ in segs) + sum(s.get("d", 0) for s in doc.get("segs", []) if s.get("type") != "ep")
    ok = need[0] <= total <= need[1]
    rep.add(f"总时长落在{tier}档 {need[0]}–{need[1]}s", ok, f"{total:.1f}s（{fmt(total)}）",
            "" if ok else ("素材不够，如实标「素材所限」别靠字卡注水" if total < need[0]
                           else "超档了，砍掉信息重复的段"))

    # 3. 三拍完整（收尾/回撤/急停/字卡/插入位是例外，不算缺）
    EXEMPT = ("收尾", "回撤", "急停", "字卡", "插入位", "黑场")
    missing = []
    for s in doc.get("segs", []):
        if s.get("type") != "ep":
            continue
        if any(k in (s.get("label") or "") for k in EXEMPT):
            continue
        beats = s.get("beats") or ""
        if not all(k in beats for k in ("挑起", "交锋", "落点")):
            missing.append(s.get("label") or f"ep{s.get('ep')}")
    rep.add("每段三拍齐全（挑起/交锋/落点）", not missing,
            "全齐" if not missing else f"缺：{', '.join(missing[:4])}",
            "" if not missing else "补 beats 或删掉不成单元的段（回撤/急停/收尾/字卡为例外）")

    # 4. 同集连续段数
    over = []
    run, prev = 1, None
    for ep, a, b, lab in segs:
        run = run + 1 if prev == ep else 1
        prev = ep
        if run > RULES["consec_same_ep"]:
            over.append(f"ep{ep} 连了 {run} 段（{lab}）")
    rep.add(f"同集连续 ≤{RULES['consec_same_ep']} 段", not over,
            "未超" if not over else "；".join(over[:3]),
            "" if not over else "中间插字卡/黑场/别集片段断开，否则算大段拼接原片")

    # 5. 前 45 秒有钩子
    hits = scan_hook(lines, [(e, a, b) for e, a, b, _ in segs], RULES["hook_window"])
    rep.add("前 45 秒有人说不", bool(hits),
            "；".join(f"ep{e} {t}s {tag}「{txt[:14]}」" for e, t, tag, txt in hits[:3]) if hits else "前 45 秒没有拒绝或反击",
            "" if hits else "把最强那句提前，或换开场段")

    # 6. 与已出片子重叠（--fresh 时跳过：把这部剧当新剧，老片子的占用不算）
    if FRESH:
        rep.add("与已出片子重叠", True, "本轮按新剧处理，不计老片子占用", "")
        return rep
    tot = total_len([(e, a, b) for e, a, b, _ in segs])
    emitted = load_emitted(runs_root, exclude_dirs=(os.path.basename(os.path.dirname(plan_dir)),))
    worst, worst_name = 0.0, ""
    for name, old in emitted.items():
        ov = overlap_len([(e, a, b) for e, a, b, _ in segs], old) / tot if tot else 0
        if ov > worst or not worst_name:
            worst, worst_name = ov, name
    ok = worst <= RULES["overlap_max"]
    rep.add(f"与已出片子重叠 ≤{RULES['overlap_max']*100:.0f}%", ok,
            f"最高 {worst*100:.0f}%（与 {worst_name}）" if worst_name else "无已出片子可比",
            "" if ok else "换素材或换角度")
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--select", help="选题层自审：选题 JSON")
    ap.add_argument("--plan", help="清单层自审：plan.json")
    ap.add_argument("--drama", default=None, help="剧名（不填则从文件里读）")
    ap.add_argument("--runs", default=None, help="该剧的 runs 目录，用于算与已出片子的重叠")
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    ap.add_argument("--fresh", action="store_true",
                    help="把这部剧当新剧：不查与已出片子的素材重叠")
    args = ap.parse_args()

    global FRESH
    FRESH = args.fresh

    if not args.select and not args.plan:
        print("要么 --select 要么 --plan", file=sys.stderr)
        sys.exit(2)

    if args.select:
        doc = json.load(open(args.select, encoding="utf-8"))
        drama = args.drama or doc.get("drama") or ""
        rep = check_select(doc, drama, args.runs or doc.get("runs"))
        title = f"选题层 · {drama}"
    else:
        doc = json.load(open(args.plan, encoding="utf-8"))
        drama = args.drama or doc.get("drama") or ""
        base = os.path.dirname(os.path.abspath(args.plan))
        src = doc.get("src_dir") or ""
        if src and not os.path.isabs(src):
            src = os.path.normpath(os.path.join(base, src))
        runs = args.runs or doc.get("runs")
        rep = check_plan(doc, drama, src, runs, args.plan)
        title = f"清单层 · {os.path.basename(base)}"

    rep.show(title)
    if args.json:
        print(json.dumps({"ok": rep.ok, "rows": rep.rows}, ensure_ascii=False, indent=1))
    sys.exit(0 if rep.ok else 1)


if __name__ == "__main__":
    main()
