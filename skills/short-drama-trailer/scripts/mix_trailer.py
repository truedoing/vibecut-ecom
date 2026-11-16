#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯剪预告装配器 —— short-drama-trailer 技能脚本。

plan.json 驱动的 ffmpeg concat 装配。铁律内置：
  1) 出点上限 = 视频流时长（不是 format 时长）——装配前自动校验，超限报错不装配；
  2) 分段 loudnorm 后全局 alimiter；setsar=1 放 scale 之后；48k/fltp 统一；
  3) 黑场/字幕卡先落地成图片文件再进 concat（lavfi 源不能直接进 concat）。

段类型（segs 数组，每段一个 dict）：
  ep    原剧集片段   {"label","type":"ep","ep":1,"a":117.8,"b":130.8,"beats":"挑起..交锋..落点"}
                     集文件按「第N集_」匹配 src_dir 下文件；也可用 "path" 直接指定。
                     可选 "dedup":"required"（11-14 加）＝这段画面与原片完全一致，必须进去重包装；
                     典型场景是拿原片第一集开场当钩子。标了就必须挂 --dedup 且 A1 名单覆盖它，
                     否则装配器拒装——这条约束由编排环节写在清单里，跨环节传递，不靠人记。
                     可选 "hook":"<模板编号>" 记这段用的是哪个开头钩子，供以后统计哪个钩子数据好。
  video 整段外部视频 {"label","type":"video","path":"wangshen_v1.mp4"}（a/b 可选，不给则整段）
  black 黑场         {"label","type":"black","d":0.5}
  card  字幕卡       {"label","type":"card","d":4.0,"path":"build/card.png"}
                     path 可省，用 plan 级 "card"。

plan 级字段：src_dir（剧集目录）、card（默认字幕卡图）、w/h/fps（默认 720/1280/24）。
相对路径一律相对 plan.json 所在目录解析；src_dir 支持 ${环境变量} 展开。
换机器时不必改 plan：设置环境变量 SHORT_DRAMA_SRC_DIR 指向剧集目录即可覆盖 src_dir。
素材被人挪了目录也不必改 plan：src_dir 解析后若不存在，会取路径末段当剧名，
查仓库根 config/drama-sources.json 的登记表自动纠正（2026-11-12 素材整体搬家后加）。
集文件命名兼容「第N集_」「第N集 」「ep_N_」「episode_0N_」「EPN_」等，N 后紧跟数字不算命中
（避免找第 1 集时误取第 10 集）。

用法：
  python3 mix_trailer.py --plan plan.json --out 成片.mp4 [--ff /path/ffmpeg] [--dry]
  python3 mix_trailer.py --plan plan.json --out 成片.mp4 --allow-mid-speech
  python3 mix_trailer.py --plan plan.json --out 成片.mp4 --dedup dedup_spec.json

--dry 只跑校验和段落表打印，不执行 ffmpeg。

画面层参数表（2026-11-14 加，可选）
--------------------------------
`--dedup <spec.json>` 挂技能产出的「画面参数表」：逐段可挑几段做 A1 包装、
concat 之后挂一次全局参数包 + 循环扫光 + ASS 文字，全部并进同一张滤镜图，
**全程只编码一次**，不对已经拼好的成片二次压制。

spec 由两个可选技能共同产出：去重技能写 dedup 段（a1 / global / sweep），
修饰技能写 decorate 段（ass 字幕文件）。装配器只负责「挂上去」，不做任何
剪辑决策，不改段序、段长、清单字段，音频链一行不动。

**不给 --dedup 就完全不挂**，行为跟加这个开关之前一模一样，存量片子不受影响。

刀口体检（2026-11-14 加）
------------------------
段表的两头原来只按镜头切，不管这句话说完没有，于是出片里常出现「半句被切」。
现在装配前读素材解析产出的停顿表 `assets/short-drama/<剧>/cuts/epNN.json`
（由 short-drama-asset-index 的 cut_index.py --build 生成），逐个刀口判：
落在停顿里过；差 0.30 秒以内算贴边，警告放行；**出点**再远就是切在说话中间，拒装。
入点切在连读中间只警告不拦——两句之间没静音是原片常态，剪进去听不出断，真正难听的是
「这句没说完画面就走了」，那只有出点。判据与 cut_index.py 的 judge_cut 同源，改一处要同步。
没有停顿表时只提示、不拦（老剧没算过也能装）；--no-cut-check 可整体跳过。
--allow-mid-speech 明知有切半句也照装（给人自己定的时候用）。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
try:  # 数据目录只认 _env 一个出口（11-16 修，别再手数 parents）
    from _env import HOST as _HOST, data_root as _data_root
except Exception:  # 极端情况兜底：至少别悄悄指到当前目录去
    _HOST = Path(__file__).resolve().parents[3]

    def _data_root() -> Path:
        _docs = Path.home() / "Documents"
        return (_docs if _docs.is_dir() else Path.home()) / "短剧工作区"


def repo_root() -> Path:
    """数据目录（放素材、清单、成片的那个），不是技能目录。

    以前这里找不到工作台就 `return Path.cwd()`，不报错、不提示，装成插件后
    必然走到这一步。
    """
    return _data_root()


# 进度上报给主管的网页看；找不到模块就降级，不影响装配本身。
# 主管技能住在插件的 skills/ 下，不在数据目录里——按 repo_root()/skills 找必然落空，
# 落空后静默降级成空壳，于是「装配成片」这一步的进度永远不出现在网页上。
# 所以问 _env 要宿主位置（写法跟 segment.py 一致，11-16 修）。
_SUP = _HOST / "skills" / "short-drama-supervisor" / "scripts"
try:
    if _SUP.is_dir() and str(_SUP) not in sys.path:
        sys.path.insert(0, str(_SUP))
    import progress
except Exception:
    class _NoProgress:
        def __getattr__(self, _name):
            return lambda *a, **k: None
    progress = _NoProgress()


def find_ff(override):
    if override:
        return override
    try:  # 统一问 _env：数据目录 bin/ 优先，其次系统 PATH
        from _env import ffmpeg as _ff
        cand = _ff()
        if os.path.isabs(cand) and os.path.exists(cand):
            return cand
    except Exception:
        pass
    p = shutil.which("ffmpeg")
    if p:
        return p
    sys.exit("找不到 ffmpeg：跑一次开工自检会自动下；或用 --ff 指定绝对路径")


def find_ffprobe(ff):
    # 必须是 ffprobe.exe —— Windows 上写 "ffprobe" 拼出来的路径 os.path.exists 判不出来，
    # 会一路掉到 PATH 去找，缓存里那份就用不上（实测踩到）。
    exe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    cand = os.path.join(os.path.dirname(ff), exe)
    if os.path.exists(cand):
        return cand
    try:
        from _env import ffprobe as _fp
        p = _fp()
        if os.path.isabs(p) and os.path.exists(p):
            return p
    except Exception:
        pass
    p = shutil.which("ffprobe")
    if p:
        return p
    sys.exit("找不到 ffprobe（与 ffmpeg 同目录应有）")


def stream_dur(fp, path, sel):
    r = subprocess.run(
        [fp, "-v", "error", "-select_streams", sel,
         "-show_entries", "stream=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return float(r.stdout.strip().splitlines()[0])


def find_sources_map():
    """向上找 config/drama-sources.json，返回 {剧名: 素材目录}；找不到返回空表。"""
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        p = os.path.join(d, "config", "drama-sources.json")
        if os.path.isfile(p):
            try:
                return json.load(open(p, encoding="utf-8")).get("dramas", {})
            except Exception:
                return {}
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return {}


def relocate_src(src_dir):
    """素材被人整目录搬走时，按「路径末段＝剧名」查登记表找回，存量清单不必改路径。"""
    if not src_dir or os.path.isdir(src_dir):
        return src_dir
    key = os.path.basename(os.path.normpath(src_dir))
    alt = find_sources_map().get(key)
    if alt:
        alt = os.path.expandvars(alt)
        if os.path.isdir(alt):
            print(f"素材目录已变动，按剧名「{key}」查登记表改用：{alt}")
            return alt
    return src_dir


def ep_file(src_dir, n):
    """按集号找素材文件；兼容中文「第N集」与英文 ep/episode 命名。"""
    if not src_dir or not os.path.isdir(src_dir):
        raise FileNotFoundError(
            f"剧集目录不存在，或 plan 没给 src_dir：{src_dir!r}。"
            "可用环境变量 SHORT_DRAMA_SRC_DIR 指定。")
    exts = (".mp4", ".mov", ".mkv", ".m4v", ".ts", ".avi")
    files = [f for f in sorted(os.listdir(src_dir)) if f.lower().endswith(exts)]
    pat = re.compile(
        rf"(?:第\s*0*{n}集|(?<![A-Za-z0-9])ep(?:isode)?[\s_.-]*0*{n})(?![0-9])",
        re.I)
    for f in files:
        if pat.search(f):
            return os.path.join(src_dir, f)
    # 纯数字命名（1.mp4 / 01.mp4 / 第01集.mp4 之类没有的场合）：
    # 11-11 以真心守护至亲实测遇到，文件名就是 1.mp4–60.mp4，上面两条都不命中。
    # 用「去扩展名后整串是数字且等于集号」精确比对，避免找第 1 集时吃到 10.mp4。
    for f in files:
        stem = os.path.splitext(f)[0].strip()
        if stem.isdigit() and int(stem) == n:
            return os.path.join(src_dir, f)
    raise FileNotFoundError(
        f"src_dir 下找不到第 {n} 集文件（支持 第N集 / ep_N / episode_N / 纯数字 N 命名）：{src_dir}")


# ---------- 刀口体检：别切在人家说话中间 ----------
# 判据与 short-drama-asset-index/scripts/cut_index.py 的 judge_cut 同源。
CUT_EDGE_TOL = 0.03      # 刀口落在停顿边界外的容差
CUT_WARN_DIST = 0.30     # 距最近停顿多远以内算贴边
CUT_TAIL_GUARD = 0.15    # 集末视频流最后这么多秒，刀口一律放行


def plan_drama(plan, plan_path, src_dir):
    if src_dir:
        n = os.path.basename(os.path.normpath(src_dir))
        if n:
            return n
    return os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(plan_path))))


def load_cuts(drama, cuts_dir=None):
    d = cuts_dir or os.path.join(str(repo_root()), "assets", "short-drama", drama, "cuts")
    if not os.path.isdir(d):
        return {}, d
    out = {}
    for f in sorted(os.listdir(d)):
        m = re.match(r"ep(\d+)\.json$", f)
        if not m:
            continue
        try:
            out[int(m.group(1))] = json.load(open(os.path.join(d, f), encoding="utf-8"))
        except Exception:
            continue
    return out, d


def judge_cut(pauses, t, video_dur, is_in):
    """返回 (级别, 说明, 建议)。级别 error / warn / ok。"""
    if t <= 0.05:
        return "ok", "集开头", None
    if video_dur and t >= video_dur - CUT_TAIL_GUARD:
        return "ok", "贴集末", None
    best_d, best = 1e9, None
    for a, b in pauses:
        if a - CUT_EDGE_TOL - 1e-9 <= t <= b + CUT_EDGE_TOL + 1e-9:
            return "ok", "", None
        d = a - t if t < a else t - b
        if d < best_d:
            best_d, best = d, [a, b]
    if best is None:
        return "warn", "整集没测到停顿，无法判断", None
    seg = f"{best[0]:.2f}–{best[1]:.2f}"
    if best_d <= CUT_WARN_DIST:
        return "warn", f"差 {best_d:.2f}s（最近停顿 {seg}）", None
    # 给两个候选：往后到下一个停顿、往前退到上一个停顿
    nxt = [(a, b) for a, b in pauses if a > t - 0.06]
    prv = [(a, b) for a, b in pauses if b < t + 0.06]
    hints = []
    if nxt:
        a, b = nxt[0]
        hints.append(f"往后到 {(a + b) / 2:.2f}（多带 {a - t:+.2f}s）")
    if prv:
        a, b = prv[-1]
        hints.append(f"往前到 {(a + b) / 2:.2f}（少带 {b - t:+.2f}s）")
    return ("error", f"切在说话中间，距最近停顿 {best_d:.2f}s（{seg}）",
            "；".join(hints) if hints else None)


def cut_check(plan, plan_path, src_dir, cuts_dir=None, allow=False):
    """逐刀口体检。返回 True 表示可以装配。"""
    drama = plan_drama(plan, plan_path, src_dir)
    cuts, found = load_cuts(drama, cuts_dir)
    print(f"\n刀口体检（{drama}）：")
    if not cuts:
        print(f"  没有停顿表（{found} 不存在），跳过。"
              f"要体检先跑：cut_index.py --build --drama {drama}")
        return True
    errors, warns, n, no_table = [], [], 0, []
    for s in plan["segs"]:
        if s.get("type") != "ep":
            continue
        c = cuts.get(s["ep"])
        if not c:
            no_table.append(s["ep"])
            continue
        vd = c.get("video_duration")
        pauses = c.get("pauses") or []
        for is_in, t, tag in ((True, s["a"], "入点"), (False, s["b"], "出点")):
            n += 1
            lvl, why, hint = judge_cut(pauses, t, vd, is_in)
            if lvl == "error" and is_in:
                # 入点落在连读中间（两句之间没静音）是真实现场常见切法，只警告不拦；
                # 出点切在话中间才是「说完之前画面走了」。用户指出的三处全是出点。
                lvl, why = "warn", "入点在连读中间：" + why
            if lvl == "error":
                errors.append((s["ep"], s["label"], tag, t, why, hint))
            elif lvl == "warn" and why:
                warns.append(f"ep{s['ep']:>2} {tag} {t:>6.2f}  {s['label']}  {why}")
    print(f"  {n} 个刀口：切在说话中间 {len(errors)} 处，贴边 {len(warns)} 处")
    for w in warns:
        print("  ⚠ " + w)
    if no_table:
        print(f"  ⚠ 这些集没有停顿表，未判：{sorted(set(no_table))}")
    if not errors:
        return True
    print("\n切在说话中间（会听到半句被削掉）：")
    for ep, label, tag, t, why, hint in errors:
        print(f"  ✗ ep{ep:>2} {tag} {t:>6.2f}  {label}")
        print(f"      {why}")
        if hint:
            print(f"      可选：{hint}")
    if allow:
        print("\n--allow-mid-speech：明知有切半句，照装。")
        return True
    print("\n拒绝装配。改清单里的 a/b 后重来，或用 --allow-mid-speech 强行装。")
    return False


# ---------- 画面参数表：技能只「准备」，装配器负责「挂」 ----------
# 说明：这里不写死任何去重/修饰手段的具体数值，全部从 spec 读。
# 装配器只认结构，不认审美；换一套参数不用改本文件。
def vhead(idx, a, b, W, H, FPS):
    """段首链：切段 → 统一画面规格 → 锁帧率 → 收口。

    最后那一刀（fps 之后再 trim=end）不是多余的：2018 版 ffmpeg（N-92722）的 fps
    滤镜在 trim 起点非 0 时，会把末帧复制「起点秒数」那么长补在尾巴上（实测
    52.23s 的段变成 57.04s）。画轨、声轨是分开 concat 的，画轨每段多几秒，
    后面所有画面就相对声音整体前移，误差逐段累加——成片播到 1 分钟就音画不同步。
    新版 ffmpeg（8.x）实测不补帧，这一刀对它是零成本，留着防旧 bin 再被打进包。
    """
    dur = round(b - a, 3)
    return (f"[{idx}:v]trim=start={a}:end={b},setpts=PTS-STARTPTS,"
            f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"setsar=1,fps={FPS},trim=end={dur},setpts=PTS-STARTPTS")


def dedup_ep_chain(idx, i, a, b, W, H, FPS, cfg):
    """逐段链：挑中的段套 A1（缩放前景 + 模糊底 + 边框），其余段保持原样。"""
    head = vhead(idx, a, b, W, H, FPS)
    a1 = (cfg or {}).get("a1") or {}
    fw = int(W * float(a1.get("fg_scale", 0.90)))
    fh = int(H * float(a1.get("fg_scale", 0.90)))
    bw, bh = int(W / 4), int(H / 4)      # 先缩到 1/4 再模糊，比全分辨率糊快得多
    blur = a1.get("bg_blur", "10:1")
    # 底层先裁掉底部字幕带再拉伸：模糊底不留字影；主画面 fg 保持完整、原剧字幕照旧在
    bg_nosub = f"crop={W}:{int(H * 0.88)}:0:0," if a1.get("bg_drop_subtitle", True) else ""
    return (f"{head},split=2[bg{i}][fg{i}];"
            f"[bg{i}]{bg_nosub}scale={bw}:{bh},boxblur={blur},"
            f"eq=brightness={a1.get('bg_brightness', -0.08)},scale={W}:{H}[bg2{i}];"
            f"[fg{i}]scale={fw}:{fh},"
            f"drawbox=x=0:y=0:w={fw}:h={fh}:color={a1.get('border', 'white@0.30')}:"
            f"t={a1.get('border_t', 6)},setsar=1[fg2{i}];"
            f"[bg2{i}][fg2{i}]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2,"
            f"setsar=1,format=yuv420p[v{i}]")


def dedup_tail_chains(cfg, W, H, sweep_idx, ass_path, fontsdir):
    """concat 之后的全局链：参数包 → 循环扫光 → ASS 文字，一次过。"""
    parts, vmap = [], "vcat"
    g = (cfg or {}).get("global") or {}
    if g:
        c = int(g.get("crop", 8))
        chain = (f"[vcat]eq=brightness={g.get('brightness', 0.03)}:"
                 f"contrast={g.get('contrast', 1.03)}")
        if g.get("vignette", True):
            chain += ",vignette"
        if c:
            chain += f",crop={W - 2 * c}:{H - 2 * c}:{c}:{c},scale={W}:{H}"
        chain += ",format=yuv420p[vbase]"
        parts.append(chain)
        vmap = "vbase"
    sw = (cfg or {}).get("sweep") or {}
    if sw and sweep_idx is not None:
        parts.append(f"[{sweep_idx}:v]scale={W}:{H},format=rgba[sw]")
        parts.append(f"[{vmap}][sw]overlay=0:0:shortest=1:eval=frame[vsw]")
        vmap = "vsw"
    if ass_path:
        parts.append(f"[{vmap}]ass={ass_path}:fontsdir={fontsdir}[vout]")
        vmap = "vout"
    return parts, f"[{vmap}]"


def freeze_check(path, ff, min_sec=1.0, sample_fps=4, w=32, h=56):
    """装完自检：找连续一模一样超过 min_sec 的静止段，报出时间点。

    为什么要有：画轨补尾帧这类事故，人眼要看到 1 分钟后才发现；解码成 32×56 灰度、
    每秒 4 帧比一遍，几秒钟就能把「静止 4.8 秒」的位置摆出来。
    它不判内容好坏，只把可疑段列出来给人看一眼——别拿它当验收结论。
    """
    try:
        p = subprocess.run(
            [ff, "-v", "error", "-i", str(path),
             "-vf", f"fps={sample_fps},scale={w}:{h},format=gray",
             "-f", "rawvideo", "-"],
            capture_output=True,
        )
    except Exception as e:
        print(f"\n音画自检跳过：{e.__class__.__name__}")
        return []
    if p.returncode != 0 or not p.stdout:
        print("\n音画自检跳过：解码失败")
        return []

    size = w * h
    n = len(p.stdout) // size
    if n <= 1:
        return []
    spans, start = [], 0
    for i in range(1, n):
        if p.stdout[i * size:(i + 1) * size] != p.stdout[(i - 1) * size:i * size]:
            if (i - start) / sample_fps >= min_sec:
                spans.append((start / sample_fps, i / sample_fps))
            start = i
    if (n - start) / sample_fps >= min_sec:
        spans.append((start / sample_fps, n / sample_fps))

    if spans:
        print("\n音画自检：发现静止段（连续画面完全一样）")
        for a, b in spans:
            print(f"  {a:6.1f}s → {b:6.1f}s  静止 {b - a:.1f}s")
        print("  静止段若正好落在某段结尾、时长≈该段起点秒数，就是 fps 补尾帧——查 ffmpeg 版本")
    else:
        print("\n音画自检：没发现超过 1 秒的静止段")
    return spans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ff", default=None)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--dedup", default=None,
                    help="画面参数表 dedup_spec.json；不给则完全不挂画面处理")
    ap.add_argument("--preset", default=None,
                    help="覆盖 libx264 预设（默认 medium；全片带滤镜时可用 fast 提速）")
    ap.add_argument("--allow-mid-speech", action="store_true",
                    help="明知刀口切在说话中间也照装")
    ap.add_argument("--no-cut-check", action="store_true",
                    help="跳过刀口体检")
    ap.add_argument("--cuts-dir", default=None,
                    help="停顿表目录（默认 assets/short-drama/<剧>/cuts）")
    args = ap.parse_args()

    plan_path = os.path.abspath(args.plan)
    base = os.path.dirname(plan_path)
    plan = json.load(open(plan_path, encoding="utf-8"))
    # 相对路径按「当前工作目录」解析。以前拼在清单目录下：在剧目录里传
    # T2_x/build/xx.mp4，会变成 T2_x/T2_x/build/xx.mp4，ffmpeg 因目录不存在失败（11-16 修）。
    out = os.path.abspath(os.path.expanduser(args.out))

    def resolve(p):
        return p if os.path.isabs(p) else os.path.join(base, p)

    # spec 里的路径一律相对 spec 自己所在目录解析，方便整套预览产物搬家
    spec = None
    spec_base = base
    a1_labels = set()
    sweep_pattern = None
    sweep_fr = None
    ass_path = None
    fontsdir = None
    if args.dedup:
        # spec 是命令行传进来的，习惯上相对「当前目录」写；兼容老规矩相对 plan 目录
        sp = args.dedup if os.path.isabs(args.dedup) else (
            args.dedup if os.path.exists(args.dedup) else resolve(args.dedup))
        spec = json.load(open(sp, encoding="utf-8"))
        spec_base = os.path.dirname(sp)

        def sresolve(p):
            return p if os.path.isabs(p) else os.path.join(spec_base, p)

        a1_labels = set(spec.get("a1_segments") or [])
        sw = spec.get("sweep") or {}
        sweep_pattern = sresolve(sw["pattern"]) if sw.get("pattern") else None
        sweep_fr = sw.get("framerate", 30)
        dec = spec.get("decorate") or {}
        if dec.get("ass"):
            ass_path = sresolve(dec["ass"])
            fontsdir = sresolve(dec.get("fontsdir") or spec_base)
        print(f"画面参数表：{sp}")
        print(f"  A1 包装 {len(a1_labels)} 段；扫光 {'开' if sweep_pattern else '关'}；"
              f"ASS {'开' if ass_path else '关'}；全局参数包 "
              f"{'开' if spec.get('global') else '关'}")

    W = plan.get("w", 720)
    H = plan.get("h", 1280)
    FPS = plan.get("fps", 24)
    src_dir = os.environ.get("SHORT_DRAMA_SRC_DIR") or plan.get("src_dir")
    if src_dir:
        src_dir = relocate_src(resolve(os.path.expandvars(src_dir)))
    card_default = plan.get("card")

    # ---------- 跨环节约束：原片开头必须去重（11-14 加） ----------
    # 编排环节在清单里给段打 "dedup":"required"，表示这段画面与原片完全一致
    # （典型是拿原片第一集开场当钩子），必须进去重包装，否则平台比对容易判搬运。
    # 这条约束跨环节：编排只管标记，装配只管执行，中间不靠人记。
    # 硬拦：标了 required 却没挂参数表，或参数表的 A1 名单里没有这一段，一律拒装。
    need_dedup = [s.get("label", f"第{i+1}段") for i, s in enumerate(plan["segs"])
                  if str(s.get("dedup", "")).strip().lower() == "required"]
    if need_dedup:
        if not a1_labels:
            print("\n这些段标了必须去重，但没有挂画面参数表（--dedup），拒绝装配：")
            for l in need_dedup:
                print(f"  ✗ {l}")
            print("  改法：产一份 dedup_spec.json，把这几个 label 写进 a1_segments，再带 --dedup 装。")
            sys.exit(1)
        miss = [l for l in need_dedup if l not in a1_labels]
        if miss:
            print("\n这些段标了必须去重，但参数表的 a1_segments 里没有它们，拒绝装配：")
            for l in miss:
                print(f"  ✗ {l}")
            print(f"  参数表 A1 名单：{sorted(a1_labels)}")
            sys.exit(1)
        print(f"去重义务：{len(need_dedup)} 段已进 A1 名单，通过")

    ff = find_ff(args.ff)
    fp = find_ffprobe(ff)

    # ---------- 预检：出点上限 = 视频流时长（铁律） ----------
    ep_cache = {}
    print("预检（出点上限 = 视频流时长）：")
    problems = []
    for s in plan["segs"]:
        t = s["type"]
        if t == "ep":
            path = resolve(s["path"]) if s.get("path") else ep_file(src_dir, s["ep"])
            if path not in ep_cache:
                vd = stream_dur(fp, path, "v")
                ad = stream_dur(fp, path, "a")
                ep_cache[path] = (vd, ad)
            vd, ad = ep_cache[path]
            flag = ""
            if vd is not None and s["b"] > vd + 0.001:
                problems.append(
                    f"{s['label']}: 出点 {s['b']} > 视频流 {vd:.2f}（音频流 {ad:.2f}）——段尾会带黑帧")
                flag = "  <-- 超限"
            elif vd is not None and vd - s["b"] < 0.1:
                flag = "  （贴视频流尾，注意帧级复核）"
            print(f"  {s['label']:<14} ep{s['ep']}  video={vd}  audio={ad}{flag}")
        elif t == "video":
            path = resolve(s["path"])
            vd = stream_dur(fp, path, "v")
            print(f"  {s['label']:<14} video  video={vd}")
        elif t in ("black", "card"):
            print(f"  {s['label']:<14} {t}  d={s['d']}")
    if problems:
        print("\n出点超限，拒绝装配：")
        for p in problems:
            print("  " + p)
        sys.exit(1)

    # ---------- 预检 2：刀口体检（切半句） ----------
    if not args.no_cut_check:
        if not cut_check(plan, plan_path, src_dir, args.cuts_dir, args.allow_mid_speech):
            sys.exit(1)

    if args.dry:
        print("--dry：校验通过，未装配。")
        return

    # ---------- 黑场落地成文件（lavfi 不能直接进 concat） ----------
    black_png = os.path.join(base, f"build_black_{W}x{H}.png")
    if not os.path.exists(black_png):
        os.makedirs(os.path.dirname(black_png), exist_ok=True)
        subprocess.run(
            [ff, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=black:s={W}x{H}",
             "-frames:v", "1", black_png], check=True)

    # ---------- 组滤镜图 ----------
    inputs, parts = [], []
    special = {}  # input idx -> 展示时长（图片类）
    loop_input = {}  # input idx -> 无限循环标记（扫光帧序列）

    def add_input(p):
        inputs.append(p)
        return len(inputs) - 1

    durations = []
    for i, s in enumerate(plan["segs"]):
        t = s["type"]
        if t == "ep":
            path = resolve(s["path"]) if s.get("path") else ep_file(src_dir, s["ep"])
            a, b = s["a"], s["b"]
            durations.append(round(b - a, 3))
            idx = add_input(path)
            if spec and s.get("label") in a1_labels:
                parts.append(dedup_ep_chain(idx, i, a, b, W, H, FPS, spec))
            else:
                parts.append(
                    f"{vhead(idx, a, b, W, H, FPS)},format=yuv420p[v{i}]")
            parts.append(
                f"[{idx}:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS,"
                f"aresample=48000,aformat=channel_layouts=stereo,"
                f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        elif t == "video":
            path = resolve(s["path"])
            if "a" in s and "b" in s:
                a, b = s["a"], s["b"]
                seg_dur = round(b - a, 3)
                durations.append(seg_dur)
                vtrim = f"trim=start={a}:end={b},setpts=PTS-STARTPTS,"
                atrim = f"atrim=start={a}:end={b},asetpts=PTS-STARTPTS,"
            else:
                d = stream_dur(fp, path, "v")
                seg_dur = 0.0
                durations.append(round(d, 3) if d else 0.0)
                vtrim = atrim = ""
            idx = add_input(path)
            # 只有「从中间切」的段需要收口：整段从 0 开始，旧版 fps 不会补尾帧
            tail = f",trim=end={seg_dur},setpts=PTS-STARTPTS" if seg_dur else ""
            parts.append(
                f"[{idx}:v]{vtrim}scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},setsar=1,fps={FPS}{tail},format=yuv420p[v{i}]")
            parts.append(
                f"[{idx}:a]{atrim}aresample=48000,aformat=channel_layouts=stereo,"
                f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        elif t in ("black", "card"):
            d = s["d"]
            durations.append(round(d, 3))
            img = black_png if t == "black" else resolve(s.get("path") or card_default)
            if not os.path.exists(img):
                sys.exit(f"图片不存在：{img}")
            idx = add_input(img)
            special[idx] = d
            parts.append(
                f"[{idx}:v]scale={W}:{H},setsar=1,fps={FPS},format=yuv420p[v{i}]")
            parts.append(
                f"anullsrc=r=48000:cl=stereo:d={d},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        else:
            sys.exit(f"未知段类型 {t}")

    # 扫光帧序列作为额外输入挂在最后；-stream_loop -1 让它循环到全片结束
    sweep_idx = None
    if sweep_pattern:
        sweep_idx = add_input(sweep_pattern)
        loop_input[sweep_idx] = sweep_fr

    n = len(plan["segs"])
    vlist = "".join(f"[v{i}]" for i in range(n))
    alist = "".join(f"[a{i}]" for i in range(n))
    parts.append(f"{vlist}concat=n={n}:v=1:a=0[vcat]")
    parts.append(f"{alist}concat=n={n}:v=0:a=1[acat]")
    parts.append("[acat]alimiter=limit=0.891:attack=5:release=80[aout]")

    vmap = "[vcat]"
    if spec:
        tail, vmap = dedup_tail_chains(spec, W, H, sweep_idx, ass_path, fontsdir)
        parts.extend(tail)

    total = sum(durations)
    cmd = [ff, "-y", "-v", "warning", "-stats"]
    for j, p in enumerate(inputs):
        if j in special:
            cmd += ["-loop", "1", "-t", f"{special[j]:.3f}", "-i", p]
        elif j in loop_input:
            # 帧序列循环输入容易被主线程队列卡住（ffmpeg 会提示 thread message queue
            # blocking），给足缓冲，不然全片里每 5 秒都要白等一次
            cmd += ["-stream_loop", "-1", "-thread_queue_size", "1024",
                    "-framerate", str(loop_input[j]), "-i", p]
        else:
            cmd += ["-i", p]
    cmd += ["-filter_complex", ";".join(parts), "-map", vmap, "-map", "[aout]",
            "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", args.preset or "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", out]

    print("\n段落表：")
    t = 0.0
    for s, d in zip(plan["segs"], durations):
        beats = s.get("beats", "")
        print(f"  {s['label']:<14} {t:>8.2f} +{d:>6.2f}  {beats}")
        t += d
    print(f"  合计 {t:.2f}s")

    plan_path = Path(args.plan).resolve()
    drama = plan_path.parent.parent.name
    task = plan_path.parent.name
    progress.start("mix_trailer", drama, task, total=0, unit="次",
                   note=f"{n} 段 / {total:.1f}s")
    # ffmpeg 可能跑 3–5 分钟；每 30 秒 touch 一次，页面不会误判成卡死
    stop_beat = progress.heartbeat("mix_trailer", drama, task,
                                   every=30, note="ffmpeg 装配中")
    try:
        r = subprocess.run(cmd)
    except Exception as e:
        progress.fail("mix_trailer", drama, task,
                      note=f"{e.__class__.__name__}: {str(e)[:100]}")
        raise
    finally:
        if stop_beat:
            stop_beat()
    if r.returncode != 0:
        progress.fail("mix_trailer", drama, task, note=f"ffmpeg 退出码 {r.returncode}")
        # 11-14：ffmpeg 有时把错因吞在 -v warning 之外（比如输入序列/滤镜图报错被管道截掉），
        # 失败时把完整命令落盘，人可以直接复制重跑看原始报错，不用猜。
        cmd_log = Path(out).with_suffix(".ffmpeg_cmd.txt")
        try:
            cmd_log.parent.mkdir(parents=True, exist_ok=True)
            cmd_log.write_text(" ".join(cmd), encoding="utf-8")
            print(f"\n装配命令已存：{cmd_log}（复制它单独跑，能看到 ffmpeg 原始报错）")
        except Exception:
            pass
        sys.exit("ffmpeg 失败")
    progress.finish("mix_trailer", drama, task, note=f"{total:.1f}s")
    print(f"完成 {out}  {total:.2f}s")
    freeze_check(out, ff)


if __name__ == "__main__":
    main()
