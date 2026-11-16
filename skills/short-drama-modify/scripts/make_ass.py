# -*- coding: utf-8 -*-
"""生成画面文字：点睛句表 + 全片 ASS —— 修饰技能的「准备」那半。

它只描述「在画上写什么字、第几秒写、怎么动」，一个剪辑决策都不做：
不选段、不改段序、不改段长、不改台词、不动音频，也不为凑一句漂亮字幕多选一段戏。

装配器 short-drama-trailer/scripts/mix_trailer.py 从画面参数表的 decorate 段
读到 ass 与 fontsdir，在组滤镜图时挂上去，全程只编码一次。

用法：
    python3 make_ass.py --drama 以真心守护至亲 --task D3_线一认不认这个妈
    python3 make_ass.py --drama X --task Y --dry              # 只看挑了几句、不写文件
    python3 make_ass.py --drama X --task Y --into 画面参数表.json   # 顺手挂进参数表
    python3 make_ass.py --drama X --task Y --at-from-end 8    # 收尾句提前到末尾前 8 秒
    python3 make_ass.py --drama X --task Y --no 人名,刻度       # 关掉某几组文字
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: E402

HERE = Path(__file__).resolve().parent
CONFIG = HERE.parent / "config" / "文字样式_默认.json"
BASE_W = 540.0          # 样式表的基准宽度：v4 定版是 540×960

# 系统字体文件：字体包不在时用它算字宽，保证排版还是准的
SYS_FONT = {
    "darwin": ["/System/Library/Fonts/PingFang.ttc",
               "/System/Library/Fonts/Supplemental/Songti.ttc",
               "/Library/Fonts/Arial Unicode.ttf"],
    "nt": [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simsun.ttc",
           r"C:\Windows\Fonts\msyhbd.ttc"],
    "posix": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
              "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"],
}


# ---------------------------------------------------------------- 读配置 / 输入
def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def resolve_inputs(args) -> tuple[Path, Path, Path]:
    """返回 (清单路径, 剧集包路径, 输出目录)。全都按数据目录算，不认当前工作目录。"""
    root = _env.data_root()
    if args.plan:
        plan = Path(args.plan).expanduser()
        if not plan.is_absolute():
            plan = (Path.cwd() / plan).resolve()
        if not plan.exists():
            sys.exit(f"找不到清单：{plan}")
        # 剧名 / 任务名从 runs/<剧>/<任务>/ 这一层反推
        drama = args.drama or plan.parent.parent.name
        task = args.task or plan.parent.name
    else:
        if not args.drama or not args.task:
            sys.exit("要么给 --plan <清单.json>，要么给 --drama 和 --task")
        drama, task = args.drama, args.task
        plan = root / "runs" / drama / task / "plan.json"
        if not plan.exists():
            alt = sorted((root / "runs" / drama / task).glob("plan*.json"))
            if alt:
                plan = alt[0]
            else:
                sys.exit(f"找不到清单：{plan}")

    pack = Path(args.pack) if args.pack else root / "packs" / "short-drama" / drama / "drama-pack.json"
    if not pack.is_absolute():
        pack = (Path.cwd() / pack).resolve()
    if not pack.exists():
        sys.exit(f"找不到剧集包：{pack}\n文字要用的金句、开场句、人物名、收尾句全在剧集包里，"
                 f"缺了写不出来。先跑 short-drama-pack 建包。")

    outdir = Path(args.out) if args.out else plan.parent / "build" / "修饰"
    if not outdir.is_absolute():
        outdir = (Path.cwd() / outdir).resolve()
    return plan, pack, outdir


def build_timeline(plan: dict) -> tuple[list[dict], float]:
    """按清单算每段在成片里的起止。段长只用清单给的，不重新测量。"""
    out, t = [], 0.0
    for i, s in enumerate(plan.get("segs") or [], 1):
        if s.get("type") == "ep":
            if s.get("a") is None or s.get("b") is None:
                sys.exit(f"第 {i} 段（{s.get('label')}）缺 a/b，清单不完整，先修清单")
            d = float(s["b"]) - float(s["a"])
        else:
            d = float(s.get("d") or 0)
            if d <= 0:
                sys.exit(f"第 {i} 段（{s.get('label')}）不是原片段又没给 d（时长），清单不完整")
        out.append({**s, "t0": round(t, 3), "t1": round(t + d, 3), "dur": round(d, 3)})
        t += d
    if not out:
        sys.exit("清单里一段都没有")
    return out, round(t, 3)


# ---------------------------------------------------------------- 字体
def find_fonts(cfg: dict) -> tuple[Path | None, dict, list[str]]:
    """返回 (字体目录 或 None, {key: ASS 家族名}, 说明行)。

    优先共用层字体包（能精确排版、观感和定版一致）；没有就退系统字体并如实报。
    """
    root = _env.data_root()
    fdir = Path(cfg["字体"]["dir"])
    if not fdir.is_absolute():
        fdir = root / fdir
    want = cfg["字体"]["list"]
    notes = []
    if fdir.is_dir() and all((fdir / v["file"]).exists() for v in want.values()):
        fam = {k: v["family"] for k, v in want.items()}
        notes.append(f"字体包齐全（{fdir}），观感与定版一致")
        return fdir, fam, notes

    miss = [v["file"] for v in want.values() if not (fdir / v["file"]).exists()]
    plat = "darwin" if sys.platform == "darwin" else ("nt" if os.name == "nt" else "posix")
    fam = dict(cfg["字体"]["降级"][plat])
    notes.append(f"字体包不全，缺 {'、'.join(miss)}")
    notes.append(f"已降级到这台机器自带的字体（{plat}）：{'、'.join(sorted(set(fam.values())))}")
    notes.append("**观感跟定版不一样**，字号粗细都不同，别当成定版效果交付")
    notes.append(cfg["字体"]["拿不到怎么办"])
    return None, fam, notes


def sys_font_file() -> str | None:
    plat = "darwin" if sys.platform == "darwin" else ("nt" if os.name == "nt" else "posix")
    for p in SYS_FONT.get(plat, []):
        if os.path.exists(p):
            return p
    return None


class Measurer:
    """量字宽。有字体文件就用真字体量（准），实在没有就按字数估（rough 会标出来）。"""

    def __init__(self, fdir: Path | None, want: dict):
        self.fdir, self.want = fdir, want
        self._cache = {}
        self.rough = False
        if fdir is None:
            self._fallback = sys_font_file()
            if not self._fallback:
                self.rough = True

    def font(self, key: str, size: float):
        size = int(round(size))
        ck = (key, size)
        if ck in self._cache:
            return self._cache[ck]
        from PIL import ImageFont
        if self.fdir is not None:
            f = ImageFont.truetype(str(self.fdir / self.want[key]["file"]), size)
        elif not self.rough:
            f = ImageFont.truetype(self._fallback, size)
        else:
            f = None
        self._cache[ck] = f
        return f

    def width(self, key: str, text: str, size: float) -> float:
        f = self.font(key, size)
        if f is None:
            # 估：中文按一个字号宽，ASCII 按半个
            return sum(size if ord(c) > 0x2E80 else size * 0.5 for c in text)
        return float(f.getlength(text))


# ---------------------------------------------------------------- ASS 基础
class Ass:
    def __init__(self, W: int, H: int, colors: dict, family: dict):
        self.W, self.H = W, H
        self.S = W / BASE_W
        self.c = {k: tuple(v) for k, v in colors.items() if isinstance(v, list)}
        self.fam = family
        self.L: list[str] = []

    def s(self, v: float) -> float:
        return v * self.S

    def ac(self, name: str, a: int = 0) -> str:
        r, g, b = self.c[name]
        return f"&H{a:02X}{b:02X}{g:02X}{r:02X}&"

    def ts(self, x: float) -> str:
        x = max(0.0, x)
        m = int(x // 60)
        return f"0:{m:02d}:{x - m * 60:05.2f}"

    def ev(self, start: float, end: float, text: str, layer: int = 0) -> None:
        self.L.append(f"Dialogue: {layer},{self.ts(start)},{self.ts(end)},Base,,0,0,0,,{text}")

    def rect(self, x, y, w, h, color, alpha=0) -> str:
        return (f"{{\\p1\\an7\\pos({x:.0f},{y:.0f})\\c{self.ac(color, alpha)}\\bord0\\shad0}}"
                f"m 0 0 l {w:.0f} 0 l {w:.0f} {h:.0f} l 0 {h:.0f}{{\\p0}}")

    def header(self) -> str:
        return (f"[Script Info]\nScriptType: v4.00+\n"
                f"PlayResX: {self.W}\nPlayResY: {self.H}\n"
                f"WrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
                f"[V4+ Styles]\n"
                f"Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                f"OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
                f"ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
                f"MarginR, MarginV, Encoding\n"
                f"Style: Base,{self.fam['heavy']},{self.s(48):.0f},{self.ac('cream')},"
                f"&H000000FF&,{self.ac('dark')},&H800C0F12&,0,0,0,0,100,100,0,0,1,"
                f"{self.s(3):.0f},{self.s(2):.0f},5,20,20,0,1\n\n"
                f"[Events]\n"
                f"Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
                f"Effect, Text\n")


def clean(text: str) -> str:
    """ASS 里 {} 是覆盖标签，正文里出现会把后续全吃掉。"""
    return re.sub(r"[{}\\\r\n]", "", str(text or "")).strip()


# ---------------------------------------------------------------- 四种动效
def fx_pop(a: Ass, m: Measurer, cfg: dict, text: str, t0: float) -> None:
    p = cfg["开场悬念句"]["pop"]
    size = a.s(p["size"])
    ws = [m.width(p["font"], ch, size) for ch in text]
    x = (a.W - sum(ws)) / 2
    fo, fd = p["淡出"]
    for i, ch in enumerate(text):
        cx = x + sum(ws[:i]) + ws[i] / 2
        st = t0 + i * p["delay"]
        gold = i >= len(text) - p["tail_gold"]
        sz = size * 1.18 if gold else size
        col = "gold" if gold else "cream"
        tags = (f"\\an5\\pos({cx:.0f},{a.s(p['y']):.0f})\\fn{a.fam[p['font']]}\\fs{sz:.0f}"
                f"\\c{a.ac(col)}\\3c{a.ac('dark')}\\bord{a.s(4):.0f}\\shad{a.s(3):.0f}"
                f"\\alpha&HFF&\\fscx55\\fscy55\\frz{-7 + (i % 5) * 3}"
                f"\\t(0,170,\\alpha&H00&\\fscx120\\fscy120\\frz0)"
                f"\\t(170,310,\\fscx94\\fscy94)\\t(310,440,\\fscx100\\fscy100)"
                f"\\t({int(fo * 1000)},{int(fd * 1000)},\\alpha&HFF&)")
        a.ev(st, st + p["时长"], "{" + tags + "}" + clean(ch), layer=2)


def fx_names(a: Ass, m: Measurer, cfg: dict, names: list[str], t0: float) -> None:
    p = cfg["人物名标签"]["names"]
    size = a.s(p["size"])
    ws = [m.width(p["font"], n, size) for n in names]
    gap = a.s(p["gap"])
    x = (a.W - (sum(ws) + gap * (len(names) - 1))) / 2
    end_all = t0 + p["总时长"]
    for i, n in enumerate(names):
        cx = x + sum(ws[:i]) + gap * i + ws[i] / 2
        st = t0 + i * p["步进"]
        col = "gold" if (i == 0 and p["首字金色"]) else "cream"
        f1 = int((end_all - st) * 1000) - 500
        f2 = int((end_all - st) * 1000)
        tags = (f"\\an5\\fn{a.fam[p['font']]}\\fs{size:.0f}\\c{a.ac(col)}\\3c{a.ac('dark')}"
                f"\\bord{a.s(4):.0f}\\shad{a.s(3):.0f}\\alpha&HFF&\\frz-14\\fscx65\\fscy65"
                f"\\move({cx - a.s(p['滑入距离']):.0f},{a.s(p['y'] + 26):.0f},"
                f"{cx:.0f},{a.s(p['y']):.0f},0,340)"
                f"\\t(0,240,\\alpha&H00&\\fscx115\\fscy115\\frz0)"
                f"\\t(240,400,\\fscx100\\fscy100)"
                f"\\t({f1},{f2},\\alpha&HFF&)")
        a.ev(st, end_all, "{" + tags + "}" + clean(n), layer=2)


def fx_quote(a: Ass, m: Measurer, cfg: dict, text: str, t0: float, dur: float) -> None:
    p = cfg["点睛句"]["quote"]
    base = a.s(p["size"])
    text = clean(text)
    # 可用宽度 = 画面宽 − 左右边距。定版用 70（540 基准），长句能排满但不贴边。
    avail = a.W - a.s(p["左右边距"])
    width = m.width(p["font"], text, base)
    size = base if width <= avail else max(a.s(p["最小字号"]), base * avail / width)
    # 数字、年龄、金额提亮：一句话里最硬的那个信息给金
    body = text
    mt = re.search(r"\d+[^，。？！]*[年前天岁块万]?", text)
    if mt:
        i, j = mt.span()
        body = (text[:i] + "{\\fs" + f"{size * p['数字提亮倍数']:.0f}" + "\\c" + a.ac("gold") + "}"
                + text[i:j] + "{\\fs" + f"{size:.0f}" + "\\c" + a.ac("cream") + "}" + text[j:])
    y = a.s(p["y"])
    fo, fd = dur - 0.7, dur - 0.1
    tags = (f"\\an5\\fn{a.fam[p['font']]}\\fs{size:.0f}\\c{a.ac('cream')}\\3c{a.ac('dark')}"
            f"\\bord{a.s(4):.0f}\\shad{a.s(3):.0f}\\alpha&HFF&\\fscx100\\fscy100"
            f"\\move({a.W / 2 - a.s(p['滑入距离']):.0f},{y + a.s(18):.0f},"
            f"{a.W / 2:.0f},{y:.0f},0,420)"
            f"\\t(0,380,\\alpha&H00&)"
            f"\\t(380,650,\\fscx106\\fscy106)\\t(650,860,\\fscx100\\fscy100)"
            f"\\t({int(fo * 1000)},{int(fd * 1000)},\\alpha&HFF&)")
    a.ev(t0, t0 + dur, "{" + tags + "}" + body, layer=2)


def fx_type(a: Ass, m: Measurer, cfg: dict, text: str, t0: float) -> None:
    p = cfg["收尾句"]["type"]
    size = a.s(p["size"])
    text = clean(text)
    ws = [m.width(p["font"], ch, size) for ch in text]
    x = (a.W - sum(ws)) / 2
    for i, ch in enumerate(text):
        cx = x + sum(ws[:i]) + ws[i] / 2
        st = t0 + i * p["步进"]
        tags = (f"\\an5\\pos({cx:.0f},{a.s(p['y']):.0f})\\fn{a.fam[p['font']]}\\fs{size:.0f}"
                f"\\c{a.ac('white')}\\3c{a.ac('dark')}\\bord{a.s(4):.0f}\\shad{a.s(3):.0f}"
                f"\\alpha&HFF&\\t(0,90,\\alpha&H00&)"
                f"\\t({int((p['时长'] - 0.7) * 1000)},{int((p['时长'] - 0.1) * 1000)},\\alpha&HFF&)")
        a.ev(st, st + p["时长"], "{" + tags + "}" + clean(ch), layer=2)


# ---------------------------------------------------------------- 点睛句表
def pick_quotes(cfg: dict, quotes: list[dict], segs: list[dict], drops: set[str]) -> list[dict]:
    """金句按「集号 + 原剧时间点」落到已选段里；落不到的不硬塞。"""
    qc = cfg["点睛句"]
    hit_all = []
    for q in quotes:
        qi = str(q.get("id") or "")
        t_in = q.get("t", q.get("t_start"))
        if qi in drops:
            hit_all.append({"id": qi, "命中": False, "原因": "命令行 --drop 手工不上屏"})
            continue
        if t_in is None:
            hit_all.append({"id": qi, "命中": False, "原因": "剧集包里这条金句没写原剧时间点"})
            continue
        seg = next((s for s in segs if s.get("type") == "ep"
                    and s.get("ep") == q.get("ep")
                    and float(s["a"]) <= float(t_in) <= float(s["b"])), None)
        if not seg:
            hit_all.append({"id": qi, "命中": False,
                            "原因": "这条故事线没选到该时间点所在的段，不是对齐算法漏了"})
            continue
        at = round(seg["t0"] + (float(t_in) - float(seg["a"])), 3)
        show = max(seg["t0"] + qc["最早开始"],
                   min(at - qc["提前量"], seg["t1"] - qc["时长"] - 0.2))
        hit_all.append({
            "id": qi, "命中": True, "ep": q.get("ep"), "t_in_ep": t_in,
            "seg": seg.get("label"), "pos": seg.get("pos"),
            "seg_start": seg["t0"], "seg_end": seg["t1"],
            "at_in_trailer": at, "quote": q.get("quote"),
            "fx": qc["fx"], "show_at": round(show, 3), "show_dur": qc["时长"],
        })

    # 上屏筛选：同段只留一句；与已上屏的间隔太近也不上。理由写进表，方便人复核。
    ok = sorted([q for q in hit_all if q.get("命中")], key=lambda x: x["at_in_trailer"])
    shown, last_at, used_seg = [], None, set()
    for q in ok:
        if qc["同一段只留一句"] and q["seg"] in used_seg:
            q["on_screen"] = False
            q["原因"] = f"同一段（{q['seg']}）已经上过一句，叠字会糊成一片"
            continue
        if last_at is not None and q["at_in_trailer"] - last_at < qc["最短间隔"]:
            q["on_screen"] = False
            q["原因"] = (f"与上一条只隔 {q['at_in_trailer'] - last_at:.2f} 秒"
                         f"（下限 {qc['最短间隔']} 秒），同属一拍，两条一起出会糊")
            continue
        q["on_screen"] = True
        q["原因"] = "按集号+时间点自动落到已选段；该段本来就在清单里，没有为字幕加戏"
        used_seg.add(q["seg"])
        last_at = q["at_in_trailer"]
        shown.append(q)
    for q in hit_all:
        q.setdefault("on_screen", False)
        if q.get("命中") and not q.get("原因"):
            q["原因"] = "已选段内"
    return hit_all


# ---------------------------------------------------------------- 幕边界
def act_bands(cfg: dict, segs: list[dict], plan: dict, cli: str | None) -> tuple[list, str]:
    """五幕各自的 pos 区间。优先级：命令行 > 清单里写的 > 配置文件 > 按段数等分。

    返回 (区间列表, 这次是从哪来的) —— 「从哪来的」要打进产物里，换片子时才知道
    这串边界是这部片子自己的，还是复用了别人的。
    """
    def parse(s: str):
        out = []
        for part in s.replace("，", ",").split(","):
            part = part.strip()
            if not part:
                continue
            a, _, b = part.partition("-")
            out.append((int(a), int(b or a)))
        return out

    if cli:
        return parse(cli), "命令行 --acts 人工指定"
    if plan.get("acts"):
        raw = plan["acts"]
        if isinstance(raw, str):
            return parse(raw), "剪辑清单里的 acts 字段"
        return [(int(a), int(b)) for a, b in raw], "剪辑清单里的 acts 字段"
    raw = (cfg.get("刻度条") or {}).get("幕边界")
    if raw:
        return [(int(a), int(b)) for a, b in raw], "配置文件"
    pos = sorted(int(s.get("pos") or 0) for s in segs if s.get("pos"))
    if not pos:
        n = len(segs)
        step = (n + 4) // 5
        return [(i + 1, min(n, i + step)) for i in range(1, n + 1, step)][:5], "按段序号等分五组"
    lo, hi = pos[0], pos[-1]
    step = ((hi - lo + 1) + 4) // 5
    out = [(lo + b * step, min(hi, lo + b * step + step - 1))
           for b in range(5) if lo + b * step <= hi]
    return out, "按段数等分五组（清单没写 acts）"


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="生成画面文字（点睛句表 + 全片 ASS）")
    ap.add_argument("--drama", help="剧名")
    ap.add_argument("--task", help="片子名（runs/<剧>/<任务>/）")
    ap.add_argument("--plan", help="直接指定剪辑清单，默认 runs/<剧>/<任务>/plan.json")
    ap.add_argument("--pack", help="直接指定剧集包，默认 packs/short-drama/<剧>/drama-pack.json")
    ap.add_argument("--out", help="输出目录，默认 runs/<剧>/<任务>/build/修饰")
    ap.add_argument("--into", help="顺手把 decorate 段写进这张画面参数表（去重技能产的那份）")
    ap.add_argument("--at-from-end", type=float, help="收尾句提前多少秒（默认取配置 3.73）")
    ap.add_argument("--names-at", type=float, help="人物名标签出现时刻（默认取配置 32.0）")
    ap.add_argument("--names", type=int, help="上屏几个人名（默认 3）")
    ap.add_argument("--no", default="", help="关掉某几组文字，逗号分开：人名,刻度,角标,开场,收尾,金句")
    ap.add_argument("--drop", default="", help="手工不让某几条金句上屏，逗号分开金句 id")
    ap.add_argument("--acts", help='五幕的 pos 区间，如 "1-4,5-8,9-12,13-18,19-23"；'
                                  '不写就按清单段数等分（清单有 acts 字段则优先用）')
    ap.add_argument("--dry", action="store_true", help="只算不写文件")
    args = ap.parse_args()

    cfg = load_config()
    off = {x.strip() for x in args.no.split(",") if x.strip()}
    if args.at_from_end is not None:
        cfg["收尾句"]["at_from_end"] = args.at_from_end
    if args.names_at is not None:
        cfg["人物名标签"]["at"] = args.names_at
    if args.names is not None:
        cfg["人物名标签"]["数量"] = args.names
    drops = {x.strip() for x in args.drop.split(",") if x.strip()}

    plan_path, pack_path, outdir = resolve_inputs(args)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    segs, TOTAL = build_timeline(plan)
    W = int(plan.get("w") or 1080)
    H = int(plan.get("h") or 1920)

    fdir, family, font_notes = find_fonts(cfg)
    meas = Measurer(fdir, cfg["字体"]["list"])

    print(f"清单 {plan_path.name}｜{len(segs)} 段｜成片 {TOTAL:.2f}s｜{W}×{H}")
    for n in font_notes:
        print(f"  · {n}")
    if meas.rough:
        print("  · 没有可用字体文件量字宽，改用按字数估算——位置可能偏，务必抽帧看一眼")

    # ---- 文案来源：剧集包 ----
    facts = {x.get("field"): x.get("value") for x in (pack.get("drama_facts") or [])}
    title = str(facts.get("title") or args.drama or "")
    hook_map = pack.get("hook_map") or []
    chars = [c.get("name") for c in (pack.get("characters") or []) if c.get("name")]
    els = pack.get("elements") or {}
    quotes = els.get("golden_quotes") or []
    ctas = els.get("ctas") or []

    hook_full = ""
    if hook_map and (hook_map[0].get("opening_lines") or []):
        hook_full = str(hook_map[0]["opening_lines"][0])
    hook = hook_full.split("，")[-1].rstrip("。") if hook_full else ""
    names = chars[: max(0, int(cfg["人物名标签"]["数量"]))]
    endline = str(ctas[0].get("text") or "") if ctas else ""

    missing = [k for k, v in (("开场句", hook), ("人物名", names), ("收尾句", endline)) if not v]
    if missing:
        print(f"  · 剧集包缺 {'、'.join(missing)}，这几组文字会跳过（其余照写）")

    # ---- 点睛句 ----
    quote_table = pick_quotes(cfg, quotes, segs, drops)
    shown = [q for q in quote_table if q.get("on_screen")]

    # ---- ASS：先写常驻层（角标、刻度），再写上屏文字，跟定版文件同序，方便逐行对照 ----
    a = Ass(W, H, cfg["颜色"], family)

    cg = cfg["角标"]
    if cg["enabled"] and "角标" not in off and title:
        b = cg["竖条"]
        a.ev(0, TOTAL, a.rect(a.s(b["x"]), a.s(b["y"]), a.s(b["w"]), a.s(b["h"]),
                              b["color"], int(b["alpha"], 16)), layer=0)
        a.ev(0, TOTAL,
             f"{{\\an7\\pos({a.s(cg['pos'][0]):.0f},{a.s(cg['pos'][1]):.0f})"
             f"\\fn{a.fam[cg['font']]}\\fs{a.s(cg['size']):.0f}\\c{a.ac('cream')}"
             f"\\bord{a.s(2):.0f}\\shad{a.s(1):.0f}\\alpha&H{cg['alpha']}&}}{clean(title)}",
             layer=1)

    bands, band_src = act_bands(cfg, segs, plan, args.acts)
    sw = cfg["刻度条"]
    if sw["enabled"] and "刻度" not in off:
        x0, ybar = a.s(sw["x0"]), a.H - a.s(sw["y_base"])
        segw = (a.W - 2 * a.s(sw["x0"])) / sw["格数"]
        for i in range(sw["格数"]):
            a.ev(0, TOTAL, a.rect(x0 + segw * i + a.s(sw["间隙"]), ybar,
                                  segw - a.s(2 * sw["间隙"]), a.s(sw["高"]), "white",
                                  0xB0), layer=0)
        for i, (p0, p1) in enumerate(bands[: sw["格数"]]):
            ss = [s for s in segs if p0 <= int(s.get("pos") or 0) <= p1]
            if not ss:
                continue
            a.ev(min(s["t0"] for s in ss), max(s["t1"] for s in ss),
                 a.rect(x0 + segw * i + a.s(sw["间隙"]), ybar - a.s(1),
                        segw - a.s(2 * sw["间隙"]), a.s(sw["高亮高"]), "gold", 0), layer=1)

    if cfg["开场悬念句"]["enabled"] and hook and "开场" not in off:
        fx_pop(a, meas, cfg, hook, cfg["开场悬念句"]["at"])
    if cfg["人物名标签"]["enabled"] and names and "人名" not in off:
        fx_names(a, meas, cfg, names, cfg["人物名标签"]["at"])
    if cfg["点睛句"]["enabled"] and shown and "金句" not in off:
        for q in shown:
            fx_quote(a, meas, cfg, str(q["quote"]), q["show_at"], q["show_dur"])
    end_at = TOTAL - float(cfg["收尾句"]["at_from_end"])
    if cfg["收尾句"]["enabled"] and endline and "收尾" not in off:
        fx_type(a, meas, cfg, endline, max(float(cfg["收尾句"]["at_min"]), end_at))

    # ---- 汇总 ----
    print(f"\n点睛句：金句库 {len(quotes)} 条｜落到已选段 {sum(1 for q in quote_table if q.get('命中'))} 条"
          f"｜上屏 {len(shown)} 条")
    for q in quote_table:
        if not q.get("命中"):
            print(f"  — {q['id']}  {q.get('原因', '')}")
            continue
        mark = "✓" if q.get("on_screen") else "—"
        when = f"{q['show_at']:7.2f}s" if q.get("on_screen") else "        "
        print(f"  {mark} {when}  {str(q.get('quote'))[:16]:<18} → {q.get('seg')}"
              + ("" if q.get("on_screen") else f"（{q.get('原因')}）"))
    print(f"收尾句：{'末尾前 %.2f 秒' % float(cfg['收尾句']['at_from_end'])}"
          f"（第 {end_at:.2f} 秒）" if endline else "收尾句：剧集包没给，跳过")
    if cfg["刻度条"]["enabled"] and "刻度" not in off:
        print(f"五幕刻度：{band_src} → " + "｜".join(f"{a0}-{b0}" for a0, b0 in bands))
        if band_src.startswith("按段数等分"):
            print("  注意：等分只是没更好依据时的兜底。要精确对应这条片子的五幕，"
                  "在剪辑清单里写 acts 字段，或跑时加 --acts")
    print(f"ASS：{len(a.L)} 条事件，覆盖 0–{TOTAL:.2f}s")

    table = {
        "_说明": "文案编排阶段应产出的独立产物。只描述哪句该上屏、在第几秒；不进剪辑清单，不改段序段长。",
        "_来源": {"剧集包": str(pack_path), "清单": plan_path.name},
        "_对齐规矩": "金句 ep 与段 ep 相同，且金句时间点落在该段 a–b 内才算命中；命中后按成片时间轴换算。",
        "_边界": "点睛句只能由选段结果推出，不能反过来影响选段；不为凑一句漂亮字幕去多选一段戏。",
        "统计": {
            "金句库总数": len(quotes), "命中已选段": sum(1 for q in quote_table if q.get("命中")),
            "命中但不上屏": sum(1 for q in quote_table if q.get("命中") and not q.get("on_screen")),
            "实际上屏": len(shown), "成片总时长": TOTAL,
            "字体": "共用层字体包" if fdir else "系统字体降级",
        },
        "开场悬念句": {"text": hook, "at": cfg["开场悬念句"]["at"], "fx": "pop",
                     "来源": "hook_map[0].opening_lines[0] 落点半句"} if hook else {},
        "人物名标签": {"text": names, "at": cfg["人物名标签"]["at"], "fx": "names",
                     "来源": "characters[].name 前几位"} if names else {},
        "收尾句": {"text": endline, "at": round(max(0.0, end_at), 3), "fx": "type",
                  "来源": "elements.ctas[0].text"} if endline else {},
        "点睛句": quote_table,
        "五幕刻度": {"边界": [list(b) for b in bands], "来源": band_src},
        "未上屏原因统计": {},
    }
    for q in quote_table:
        if not q.get("on_screen"):
            r = re.sub(r"[\d.]+ 秒|（[^）]*）|%s" % re.escape(str(q.get("seg") or "")), "", str(q.get("原因")))
            table["未上屏原因统计"][r[:24]] = table["未上屏原因统计"].get(r[:24], 0) + 1

    if args.dry:
        print("\n--dry：没写文件。")
        return 0

    outdir.mkdir(parents=True, exist_ok=True)
    ass_path = outdir / "全片文字.ass"
    ass_path.write_text(a.header() + "\n".join(a.L) + "\n", encoding="utf-8")
    (outdir / "点睛句表.json").write_text(
        json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")

    fonts_out = outdir / "字体"
    if fdir is not None:
        fonts_out.mkdir(exist_ok=True)
        for v in cfg["字体"]["list"].values():
            src, dst = fdir / v["file"], fonts_out / v["file"]
            if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                dst.write_bytes(src.read_bytes())
        fontsdir = "字体"
    else:
        fontsdir = "."      # 降级时不需要 fontsdir，给个存在的目录免得装配器报错

    decorate = {
        "_说明": "修饰技能产出的文字段。装配器只负责挂上去，不做任何内容判断。",
        "_样式版本": "v4 定版（四种动效、四种字体、无卡片底框）",
        "_字体": "共用层字体包" if fdir else "系统字体降级（观感与定版不同）",
        "ass": ass_path.name,
        "fontsdir": fontsdir,
        "table": "点睛句表.json",
    }
    (outdir / "decorate.json").write_text(
        json.dumps(decorate, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写 {ass_path}")
    print(f"已写 {outdir / '点睛句表.json'}")
    print(f"已写 {outdir / 'decorate.json'}")

    if args.into:
        sp = Path(args.into).expanduser()
        if not sp.is_absolute():
            sp = (Path.cwd() / sp).resolve()
        if not sp.exists():
            sys.exit(f"--into 指定的参数表不存在：{sp}（先跑去重技能的 make_spec.py）")
        spec = json.loads(sp.read_text(encoding="utf-8"))
        # 装配器按「相对参数表所在目录」解析，所以这里要写成相对路径
        rel_ass = os.path.relpath(ass_path, sp.parent)
        rel_fonts = os.path.relpath(outdir / fontsdir, sp.parent) if fontsdir != "." else "."
        spec["decorate"] = {**decorate, "ass": rel_ass, "fontsdir": rel_fonts}
        sp.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已挂进 {sp}（ass={rel_ass}，fontsdir={rel_fonts}）")
    else:
        print(f"\n装配时这样挂：先把它写进去重技能那份参数表，再装配")
        print(f"  python3 make_ass.py ... --into <画面参数表.json>")
        print(f"  或手工把 decorate.json 的内容并进参数表的 decorate 段")
    print("下一步必做：抽帧看一眼字的位置、有没有压脸、长句有没有被切。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
