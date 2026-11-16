# -*- coding: utf-8 -*-
"""不放整片也能看文字效果：把字幕叠到真画面上，出几张单帧图。

为什么要它：整片装配一次十几分钟，而「字有没有压脸、长句有没有被切、字号合不合适」
拿单帧看就够。改一版字幕就跑一遍这个，几十秒出结果。

做法：按成片时间轴反查到原片的第几集第几秒 → 抽那一帧 → 把 ASS 里那个时刻附近
的事件裁成一个短窗口（时间平移到窗口内同一位置）→ 叠上去。
短窗口是为了不用渲整片：libass 只处理窗口内的事件，1.5 秒的东西 1 秒多就渲完。

用法：
    python3 preview_text.py --drama X --task Y                 # 每处文字各出一张
    python3 preview_text.py --drama X --task Y --at 8.5        # 只看第 8.5 秒
    python3 preview_text.py --drama X --task Y --pre 0.6       # 早 0.6 秒看（默认 1.2，动效已展开）
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: E402

PRE = 1.2          # 目标时刻落在窗口的第几秒（留给入场动效展开）
WIN = 1.6          # 窗口总长


def sec(x: str) -> float:
    h, m, s = x.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def stamp(x: float) -> str:
    x = max(0.0, x)
    return f"0:{int(x // 60):02d}:{x % 60:05.2f}"


def window_ass(src: Path, dst: Path, at: float) -> int:
    """把 at 时刻附近的 Dialogue 裁成 1.6 秒小窗，时间平移到窗口内。返回保留条数。"""
    off = at - PRE
    keep = 0
    out = []
    for ln in src.read_text(encoding="utf-8").splitlines():
        if ln.startswith("Dialogue:"):
            p = ln.split(",", 9)
            a, b = sec(p[1]), sec(p[2])
            if b - off <= 0 or a - off >= WIN:
                continue
            p[1], p[2] = stamp(a - off), stamp(b - off)
            ln = ",".join(p)
            keep += 1
        out.append(ln)
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    return keep


def ep_file(src_dir: Path, ep: int) -> Path | None:
    """按文件名里的数字认集号，不猜命名规则（1.mp4 / 第1集.mp4 / ep01.mp4 都认）。"""
    cands = []
    for p in sorted(src_dir.glob("*.mp4")):
        m = re.search(r"\d+", p.stem)
        if m and int(m.group()) == ep:
            cands.append(p)
    return cands[0] if cands else None


def main() -> int:
    global PRE
    ap = argparse.ArgumentParser(description="抽帧看文字效果（叠真画面）")
    ap.add_argument("--drama", help="剧名")
    ap.add_argument("--task", help="片子名")
    ap.add_argument("--plan", help="剪辑清单，默认 runs/<剧>/<任务>/plan.json")
    ap.add_argument("--ass", help="字幕文件，默认 <清单目录>/build/修饰/全片文字.ass")
    ap.add_argument("--out", help="输出目录，默认 <清单目录>/build/修饰/预览")
    ap.add_argument("--at", type=float, action="append",
                    help="只看某个成片时刻（可给多次）")
    ap.add_argument("--pre", type=float, default=1.2, help="目标时刻落在窗口第几秒（默认 1.2，动效已展开）")
    ap.add_argument("--src-dir", help="原片目录，默认按清单里的 src_dir 推")
    args = ap.parse_args()

    PRE = args.pre

    root = _env.data_root()
    if args.plan:
        plan_path = Path(args.plan).expanduser()
        if not plan_path.is_absolute():
            plan_path = (Path.cwd() / plan_path).resolve()
    else:
        if not args.drama or not args.task:
            sys.exit("给 --plan，或者 --drama 和 --task")
        plan_path = root / "runs" / args.drama / args.task / "plan.json"
        if not plan_path.exists():
            alt = sorted((root / "runs" / args.drama / args.task).glob("plan*.json"))
            if not alt:
                sys.exit(f"找不到清单：{plan_path}")
            plan_path = alt[0]
    if not plan_path.exists():
        sys.exit(f"找不到清单：{plan_path}")

    run = plan_path.parent
    ass = Path(args.ass) if args.ass else run / "build" / "修饰" / "全片文字.ass"
    if not ass.is_absolute():
        ass = (Path.cwd() / ass).resolve()
    if not ass.exists():
        sys.exit(f"找不到字幕：{ass}\n先跑 make_ass.py 出字幕")
    outdir = Path(args.out) if args.out else ass.parent / "预览"
    if not outdir.is_absolute():
        outdir = (Path.cwd() / outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    segs, t = [], 0.0
    for s in plan.get("segs") or []:
        d = (float(s["b"]) - float(s["a"])) if s.get("type") == "ep" else float(s.get("d") or 0)
        segs.append({**s, "t0": t, "t1": t + d, "dur": d})
        t += d
    TOTAL = t

    src_dir = args.src_dir
    if not src_dir:
        sd = plan.get("src_dir")
        if sd:
            p = Path(str(sd)).expanduser()
            src_dir = p if p.is_absolute() else (run / p).resolve()
    if not src_dir or not Path(src_dir).is_dir():
        env = _env.read_env(root)
        src_dir = env.get("SHORT_DRAMA_ASSET_ROOT")
    if not src_dir or not Path(src_dir).is_dir():
        sys.exit("不知道原片在哪：清单里没有 src_dir，.env 也没配 SHORT_DRAMA_ASSET_ROOT")
    src_dir = Path(src_dir)

    fdir = ass.parent / "字体"
    fdir_arg = f":fontsdir={fdir}" if fdir.is_dir() else ""

    # 要看的时刻：从字幕里认出上屏事件，取事件开始后 PRE 秒
    if args.at:
        times = [(x, f"手选 {x:.2f}s") for x in args.at]
    else:
        # 逐字动效是一条事件一个字，得先把「同一处文字」串起来，否则一句话渲十几张
        evs = []
        for ln in ass.read_text(encoding="utf-8").splitlines():
            if not ln.startswith("Dialogue: 2,"):     # layer 2 = 上屏文字
                continue
            q = ln.split(",", 9)
            evs.append((sec(q[1]), re.sub(r"\{[^}]*\}", "", q[9] if len(q) > 9 else "")))
        evs.sort()
        times, grp, last = [], [], None
        for a, lb in evs:
            if last is not None and a - last > 0.4:   # 隔开 0.4 秒以上才算另一处
                times.append((last + PRE, "".join(x[1] for x in grp) or "文字"))
                grp = []
            grp.append((a, lb))
            last = a
        if grp:
            times.append((last + PRE, "".join(x[1] for x in grp) or "文字"))
        if not times:
            print("字幕里没有上屏文字（layer 2），没什么可看的")
            return 0

    ff = _env.tool("ffmpeg")
    made = 0
    for i, (at, label) in enumerate(times, 1):
        if at > TOTAL:
            print(f"跳过 {at:.2f}s：超出成片时长 {TOTAL:.2f}s")
            continue
        seg = next((s for s in segs if s["t0"] <= at <= s["t1"]), segs[-1])
        f, src_t = None, 0.0
        if seg.get("type") == "ep":
            f = ep_file(src_dir, int(seg["ep"]))
            if not f:
                print(f"跳过 {at:.2f}s：原片目录里找不到第 {seg['ep']} 集")
                continue
            src_t = float(seg["a"]) + (at - seg["t0"])
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            still = td / "f.png"
            W, H = int(plan.get("w") or 1080), int(plan.get("h") or 1920)
            if f is None:
                # 字幕卡 / 黑场段：没有原片帧可抽，用纯黑底，只为看字排得对不对
                subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi",
                                "-i", f"color=c=black:s={W}x{H}:d=0.1:r=30",
                                "-frames:v", "1", str(still)], capture_output=True)
                note = "黑场/字幕卡段，只有字"
            else:
                r = subprocess.run([ff, "-v", "error", "-y", "-ss", f"{src_t:.3f}", "-i", str(f),
                                    "-frames:v", "1", str(still)], capture_output=True, text=True)
                if r.returncode != 0 or not still.exists():
                    print(f"跳过 {at:.2f}s：抽帧失败 {r.stderr.strip()[:80]}")
                    continue
                note = f"第 {seg['ep']} 集 {src_t:.2f}s"
            wa = td / "w.ass"
            window_ass(ass, wa, at)
            dst = outdir / f"{i:02d}_{at:06.2f}s_{re.sub(r'[^0-9A-Za-z一-龥]', '', label)[:10]}.png"
            r = subprocess.run([
                ff, "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", str(still),
                "-f", "lavfi", "-i", f"color=c=black:s={plan.get('w', 1080)}x{plan.get('h', 1920)}"
                                     f":d={WIN}:r=30",
                "-filter_complex",
                f"[1:v][0:v]overlay=0:0:shortest=1,ass='{wa}'{fdir_arg}[v]",
                "-map", "[v]", "-ss", f"{PRE:.2f}", "-frames:v", "1", str(dst),
            ], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"  渲染失败（{at:.2f}s）：{r.stderr.strip()[:120]}")
                continue
            made += 1
            print(f"  ✓ 第 {seg.get('pos')} 段 {seg.get('label')}｜成片 {at:.2f}s = {note} → {dst.name}")

    print(f"\n出了 {made} 张，在 {outdir}")
    print("看三件事：字有没有压脸、长句有没有被切、字号够不够。看不过去就改配置再跑。")
    return 0 if made else 1


if __name__ == "__main__":
    sys.exit(main())
