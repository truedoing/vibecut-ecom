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
  python3 mix_trailer.py --plan plan.json --out 成片.mp4 --jobs 4

--dry 只跑校验和段落表打印，不执行 ffmpeg。

并行装配（2026-11-17 加）
------------------------
实测慢的根源不在编码（720p medium 和 360p veryfast 全片耗时几乎一样），
在「几十路源片解码 + 滤镜链」挤在一个 ffmpeg 进程里串行走。
--jobs N（默认 1 = 单进程）把段表按顺序切成 N*2 块，N 个 ffmpeg 进程并行渲染
中间文件，再合并。分块必须连续，concat 顺序不能乱。
11-17 在当前这台机器（16 vCPU、无 AVX2）实测：单路 x264 medium 就吃满 CPU，
两路并行每路慢 1.7 倍、总吞吐只涨 15%；分块 veryfast + medium 终程加起来
反而比单进程慢。**在本机上真正的提速开关是 --preset veryfast（2.1 倍）**；
--jobs 留给核真的多、或上了硬编码器的机器。
  · 挂了画面参数表（全局链/扫光/字幕）：中间块用 crf 10 veryfast 近无损画质，
    终程统一过全局链再压定版 medium crf 19；音频中间块走无损 PCM，
    alimiter/AAC 留给终程，跟老路径同源。
  · 没挂参数表：中间块直接压定版参数，合并时纯复制，零再编码。
--jobs 1 走原来的单进程单命令路径（行为与以前完全一致）。

段间转场（2026-11-17 加，清单层决策、装配层执行）
--------------------------------------------------
段上写 `"out"` 字段选这一段结束后接什么转场（缺省 = "cut" 硬切，行为与以前一致）：
  · "cut"  硬切，什么都不加。
  · "black" 黑场一闪：前段尾淡出（视频 0.25s / 音频 0.30s）→ 0.45s 纯黑（静音）→
           后段淡入。分章信号最明确。**切点被台词腰斩时，清单层应同时顺切点**
           （把 b 延到台词说完，那是改清单数值，不是装配器的活）。
  · "card"  文字卡：1.2s 黑底两行字（剧名 + 悬念句）整卡淡入淡出，顺便报剧名。
plan 级配置（可选，全有默认值）：
  "transitions": {
    "black": {"d":0.45, "fade":0.25, "afade":0.30},
    "card":  {"d":1.2, "title":"《剧名》", "sub":"默认副句", "font":"修饰/字体/xx.otf"},
    "font":  "修饰/字体/xx.otf"      # card 字体也可放这层
  }
段级 `"out_sub"` 覆盖该接缝的文字卡副句。文字卡由 drawtext 预渲染成 PNG
（临时目录 + 相对路径，规避盘符冒号转义坑），内容相同的卡自动复用。
用哪种转场是排清单时的创作决策，见清单技能的决策规则；装配器只照单执行。

画面层参数表（2026-11-14 加，可选）
--------------------------------
`--dedup <spec.json>` 挂技能产出的「画面参数表」：逐段可挑几段做 A1 包装、
concat 之后挂一次全局参数包 + 循环扫光 + ASS 文字，全部并进同一张滤镜图，
**单进程路径只编码一次**，不对已经拼好的成片二次压制
（--jobs 并行时中间块近无损 + 终程定版，两道，见文末）。

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
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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


def _ffp(p):
    """ffmpeg 滤镜里的路径：反斜杠换正斜杠、冒号转义。
    否则 Windows 绝对路径 C:\\Users\\... 的冒号会被当参数分隔符、反斜杠被吃掉，
    报 "Could not create a libass track" / "Unable to parse option value ... as
    image size"（11-17 实测）。"""
    return str(p).replace("\\", "/").replace(":", "\\\\:")


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
        # ep 段：整段检查入点/出点
        # broll 段：每个镜头也要查（垫画面是静音的，但切在话中间画面会跳）
        if s.get("type") == "ep":
            checks = [(s["ep"], s["a"], s["b"], s.get("label", ""))]
        elif s.get("type") == "broll":
            checks = [(c["ep"], float(c["a"]), float(c["b"]),
                       f"{s.get('label', '')}·镜头{i + 1}")
                      for i, c in enumerate(s.get("clips") or [])]
        else:
            continue
        for ep, a, b, lab in checks:
            c = cuts.get(ep)
            if not c:
                no_table.append(ep)
                continue
            vd = c.get("video_duration")
            pauses = c.get("pauses") or []
            for is_in, t, tag in ((True, a, "入点"), (False, b, "出点")):
                n += 1
                lvl, why, hint = judge_cut(pauses, t, vd, is_in)
                if lvl == "error" and is_in:
                    # 入点落在连读中间（两句之间没静音）是真实现场常见切法，只警告不拦；
                    # 出点切在话中间才是「说完之前画面走了」。用户指出的三处全是出点。
                    lvl, why = "warn", "入点在连读中间：" + why
                if lvl == "error":
                    errors.append((ep, lab, tag, t, why, hint))
                elif lvl == "warn" and why:
                    warns.append(f"ep{ep:>2} {tag} {t:>6.2f}  {lab}  {why}")
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
        _fd = f":fontsdir={_ffp(fontsdir)}" if fontsdir else ""
        parts.append(f"[{vmap}]ass={_ffp(ass_path)}{_fd}[vout]")
        vmap = "vout"
    return parts, f"[{vmap}]"


def shift_ass_events(src_ass, trans_offsets, out_ass):
    """段间转场会把成片时间轴整体推后，ASS 字幕事件是按无转场时间轴写的，
    得按每个转场的位置和时长平移，否则字幕全部错位（11-17 实测踩中）。
    trans_offsets = [(转场在原时间轴的位置, 转场时长), ...]，
    事件起始时间在哪个转场之后，就累加它前面的全部转场时长。"""
    import re

    def parse(ts):  # "0:00:09.02" → 秒
        h, m, s = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    def fmt(t):
        h = int(t // 3600)
        m = int(t % 3600 // 60)
        return f"{h}:{m:02d}:{t % 60:05.2f}"

    def offset_of(t):
        return sum(d for (tt, d) in trans_offsets if tt <= t + 1e-6)

    n = 0
    with open(src_ass, encoding="utf-8-sig") as fi, \
            open(out_ass, "w", encoding="utf-8") as fo:
        for line in fi:
            if line.startswith("Dialogue:"):
                head, _, rest = line.partition(",")
                t0s, _, rest2 = rest.partition(",")
                t1s, _, _ = rest2.partition(",")
                try:
                    off = offset_of(parse(t0s))
                    if off > 0:
                        fo.write(f"{head},{fmt(parse(t0s) + off)},{fmt(parse(t1s) + off)},{rest2}")
                        n += 1
                        continue
                except ValueError:
                    pass
            fo.write(line)
    return n


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
    ap.add_argument("--jobs", type=int, default=1,
                    help="并行装配的 ffmpeg 进程数（默认 1 = 单进程老路径。"
                         "11-17 实测：本机单路 x264 已吃满 CPU，4 进程并行总吞吐只涨"
                         " ~1.7 倍但终程还是串行，反而不划算；留给核多/硬编码的机器用）")
    ap.add_argument("--vcodec", default=None,
                    help="视频编码器（默认 libx264）。可用 h264_nvenc 等硬编码："
                         "需要渲染机驱动 ≥570 且带 N 卡，本机（GTX 1050 + 560.94）实测"
                         "带不动，未验证，仅在渲染机上启用）")
    ap.add_argument("--vq", type=int, default=19,
                    help="画质参数：libx264 时作 -crf，nvenc 时作 -cq（默认 19）")
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
        elif t == "broll":
            # 垫画面段：每个镜头都要查出点上限（超出就是段尾带黑帧）
            clips = s.get("clips") or []
            total = 0.0
            for j, c in enumerate(clips):
                cpath = resolve(c["path"]) if c.get("path") else ep_file(src_dir, c["ep"])
                if cpath not in ep_cache:
                    ep_cache[cpath] = (stream_dur(fp, cpath, "v"),
                                       stream_dur(fp, cpath, "a"))
                vd, ad = ep_cache[cpath]
                total += float(c["b"]) - float(c["a"])
                flag = ""
                if vd is not None and float(c["b"]) > vd + 0.001:
                    problems.append(
                        f"{s['label']} 第{j + 1}个镜头: 出点 {c['b']} > 视频流 {vd:.2f}"
                        f"（{Path(cpath).name}）——段尾会带黑帧")
                    flag = "  <-- 超限"
                print(f"  {s['label']:<14} broll[{j+1}] ep{c.get('ep')} "
                      f"{c['a']}-{c['b']} video={vd}{flag}")
            print(f"  {'':<14} └─ 垫画面合计 {total:.2f}s（静音，解说垫在上面）")
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

    # ---------- 转场时间轴 + 段落时长表（两条装配路径共用；11-17 加段间转场） ----------
    # 段上写 "out": "black" / "card" 表示这一段结束后插转场（缺省 = "cut" 硬切）。
    # 用哪种转场是清单层的创作决策（排清单时定），装配器只照单执行；
    # 顺切点（把切点延到台词说完）改的是清单里的切点数值，也不在这里做。
    # plan.transitions = {
    #   "black": {"d":0.45, "fade":0.25, "afade":0.30},
    #   "card":  {"d":1.2, "title":"《剧名》", "sub":"默认副句", "font":"修饰/字体/xx.otf"},
    #   "font": "修饰/字体/xx.otf"   # card 字体也可放这层
    # }
    # 段级：seg["out"]="card" 可加 seg["out_sub"]="这一接缝的悬念句" 覆盖默认副句。
    TR = plan.get("transitions") or {}
    bcfg = TR.get("black") or {}
    BLACK_D = float(bcfg.get("d", 0.45))
    V_FADE = float(bcfg.get("fade", 0.25))
    A_FADE = float(bcfg.get("afade", 0.30))
    ccfg = TR.get("card") or {}
    CARD_D = float(ccfg.get("d", 1.2))
    CARD_FADE_IN = float(ccfg.get("fade_in", 0.22))
    CARD_FADE_OUT = float(ccfg.get("fade_out", 0.25))

    from timeline import seg_duration  # 时间轴唯一真源：段时长只准有一份算法

    def seg_dur_of(s):
        d = seg_duration(s)
        # 唯一例外：整段外部视频没给 a/b。真源不碰文件系统，这里探一次文件时长
        # （装配器本来就要读这个文件），其余类型一律听真源的。
        if s["type"] == "video" and "a" not in s and "b" not in s:
            vd = stream_dur(fp, resolve(s["path"]), "v")
            return round(vd, 3) if vd else 0.0
        return d

    timeline = []
    for s in plan["segs"]:
        t_out = s.get("out") or "cut"
        if t_out not in ("cut", "black", "card"):
            sys.exit(f"段 {s['label']} 的 out=\"{t_out}\" 无效（只认 cut/black/card）")
        timeline.append({"kind": "seg", "seg": s, "dur": seg_dur_of(s),
                         "fade_in": bool(timeline) and timeline[-1]["kind"] == "trans",
                         "fade_out": t_out != "cut"})
        if t_out == "black":
            timeline.append({"kind": "trans", "type": "black",
                             "dur": BLACK_D, "label": "转场黑场"})
        elif t_out == "card":
            # 段级可覆盖：out_card_d（这张卡停多久）、out_title（这张卡的大标题）
            cd = float(s["out_card_d"]) if s.get("out_card_d") is not None else CARD_D
            timeline.append({"kind": "trans", "type": "card", "dur": cd,
                             "sub": s.get("out_sub") or ccfg.get("sub", ""),
                             "title": s.get("out_title") or ccfg.get("title", ""),
                             "seg_title": s.get("out_title"),
                             "title_size": s.get("out_title_size"),
                             "sub_size": s.get("out_sub_size"),
                             "keep_sub": s.get("out_keep_sub"),
                             "label": "转场文字卡"})
    if plan["segs"] and (plan["segs"][-1].get("out") or "cut") != "cut":
        print("注意：最后一段的 out 转场不生效（后面没有段落了），已忽略")

    # ASS 字幕事件是按无转场时间轴写的，插了转场后必须平移，否则全部错位
    trans_offsets = []
    _t = 0.0
    for it in timeline:
        if it["kind"] == "trans":
            trans_offsets.append((_t, it["dur"]))
        else:
            _t += it["dur"]
    if ass_path and trans_offsets:
        # 文件名带偏移指纹：多条片子并发装配时各自的平移副本不互相覆盖
        import hashlib
        fp = hashlib.md5(str(trans_offsets).encode()).hexdigest()[:8]
        shifted = os.path.join(base, f"build_ass_转场平移_{fp}.ass")
        n = shift_ass_events(ass_path, trans_offsets, shifted)
        print(f"字幕随转场平移：{n} 条事件整体后移 "
              f"（累计 {sum(d for _, d in trans_offsets):.2f}s），副本 {shifted}")
        ass_path = shifted

    # 文字卡预渲染成 PNG（一段一张，内容相同的复用）。
    # drawtext 的 fontfile/textfile 用绝对路径时，盘符冒号的转义在两层解析下
    # 吃不全（11-17 实测报 "No option name"）；拷进临时目录用相对路径最稳。
    if any(it["kind"] == "trans" and it["type"] == "card" for it in timeline):
        font = TR.get("font") or ccfg.get("font")
        if font:
            font = resolve(font)
        elif fontsdir:
            cand = os.path.join(fontsdir, "SourceHanSansSC-Heavy.otf")
            font = cand if os.path.exists(cand) else None
        if not font or not os.path.exists(font):
            sys.exit("文字卡转场需要字体：plan.transitions.font 指到 .otf/.ttf 文件，"
                     "或保证 fontsdir 里有 SourceHanSansSC-Heavy.otf")

        import hashlib

        def render_card_png(card_title, sub, tsize=54, ssize=40):
            """渲染一张文字卡。标题在上、副句在下；没有副句时标题垂直居中。

            用 PIL 画字，不依赖 ffmpeg 的 drawtext 滤镜——本机 ffmpeg 没编这个滤镜。
            """
            key = f"{card_title}|{sub}|{tsize}|{ssize}"
            h = hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
            out_png = os.path.join(base, f"build_card_{h}.png")
            if os.path.exists(out_png):
                return out_png
            from PIL import Image, ImageDraw, ImageFont
            img = Image.new("RGB", (W, H), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            f_t = ImageFont.truetype(font, tsize)
            if sub:
                y_t = int(H / 2 - tsize * 1.6)
                y_s = int(H / 2 + tsize * 0.5)
            else:
                y_t = int(H / 2 - tsize * 0.7)
            box = draw.textbbox((0, 0), card_title, font=f_t)
            tw = box[2] - box[0]
            draw.text(((W - tw) / 2, y_t), card_title, font=f_t, fill=(255, 255, 255))
            if sub:
                f_s = ImageFont.truetype(font, ssize)
                box = draw.textbbox((0, 0), sub, font=f_s)
                sw = box[2] - box[0]
                draw.text(((W - sw) / 2, y_s), sub, font=f_s, fill=(216, 216, 216))
            img.save(out_png)
            return out_png

        for it in timeline:
            if it["kind"] == "trans" and it["type"] == "card":
                card_title = it.get("title") or ""
                if not card_title:
                    sys.exit("用了文字卡转场，但 plan.transitions.card.title 没写"
                             "（一般是剧名），段级可用 out_title 覆盖")
                # 段级标题存在时，字号放大、副句省略——描述转场要一眼看清
                is_seg_title = bool(it.get("seg_title"))
                tsize = int(it.get("title_size") or (72 if is_seg_title else 54))
                ssize = int(it.get("sub_size") or 40)
                sub = it.get("sub") or ""
                if is_seg_title and not it.get("keep_sub"):
                    sub = ""
                it["png"] = render_card_png(card_title, sub, tsize, ssize)

    durations = [it["dur"] for it in timeline]
    total = sum(durations)
    n = len(timeline)

    print("\n段落表：")
    t = 0.0
    for it in timeline:
        if it["kind"] == "seg":
            beats = it["seg"].get("beats", "")
            tag = "垫" if it["seg"].get("type") == "broll" else "  "
            print(f" {tag}{it['seg']['label']:<16} {t:>8.2f} +{it['dur']:>6.2f}  {beats}")
        else:
            note = "黑场" if it["type"] == "black" else f"文字卡：{it['sub'] or '（仅剧名）'}"
            if it["type"] == "card" and it.get("seg_title"):
                note = f"文字卡：{it['seg_title']}"
            print(f"  <{it['label']}>        {t:>8.2f} +{it['dur']:>6.2f}  {note}")
        t += it["dur"]
    print(f"  合计 {t:.2f}s")

    def add_segment(k, item, inputs, parts, special):
        """把一个时间轴项（段落或转场）的滤镜链追加进 inputs/parts/special，
        返回 (视频标签, 音频标签)（k 是本图局部序号）。
        单进程老路径和并行分块路径共用这一份，改一处两边都改到。"""
        def add_input(p):
            inputs.append(p)
            return len(inputs) - 1

        if item["kind"] == "trans":
            # 段间转场：黑场或文字卡，无声。文字卡整卡淡入淡出。
            d = item["dur"]
            img = black_png if item["type"] == "black" else item["png"]
            idx = add_input(img)
            special[idx] = d
            if item["type"] == "card":
                # 渐变时长可配（plan.transitions.card.fade_in / fade_out）。
                # 描述转场（"几天前……"）要看得清，渐变就该比默认长。
                fi = min(float(item.get("fade_in") or CARD_FADE_IN), d / 2)
                fo = min(float(item.get("fade_out") or CARD_FADE_OUT), d / 2)
                parts.append(
                    f"[{idx}:v]scale={W}:{H},setsar=1,fps={FPS},"
                    f"fade=t=in:d={fi:.3f},fade=t=out:st={max(d - fo, 0):.3f}:d={fo:.3f},"
                    f"format=yuv420p[v{k}]")
            else:
                parts.append(
                    f"[{idx}:v]scale={W}:{H},setsar=1,fps={FPS},format=yuv420p[v{k}]")
            parts.append(
                f"anullsrc=r=48000:cl=stereo:d={d},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{k}]")
            return f"[v{k}]", f"[a{k}]"

        s = item["seg"]
        typ = s["type"]
        if typ == "ep":
            path = resolve(s["path"]) if s.get("path") else ep_file(src_dir, s["ep"])
            a, b = s["a"], s["b"]
            idx = add_input(path)
            if spec and s.get("label") in a1_labels:
                parts.append(dedup_ep_chain(idx, k, a, b, W, H, FPS, spec))
            else:
                parts.append(
                    f"{vhead(idx, a, b, W, H, FPS)},format=yuv420p[v{k}]")
            parts.append(
                f"[{idx}:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS,"
                f"aresample=48000,aformat=channel_layouts=stereo,"
                f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,"
                # 收口：loudnorm/重采样每段会多出十几毫秒，几十段叠起来音画能差半秒多。
                # 每段音频强制截到清单段长，跟视频的规划时间轴对齐（11-17 实测修）。
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"apad,atrim=duration={round(b - a, 3):.3f}[a{k}]")
        elif typ == "video":
            path = resolve(s["path"])
            if "a" in s and "b" in s:
                a, b = s["a"], s["b"]
                seg_dur = round(b - a, 3)
                vtrim = f"trim=start={a}:end={b},setpts=PTS-STARTPTS,"
                atrim = f"atrim=start={a}:end={b},asetpts=PTS-STARTPTS,"
            else:
                seg_dur = 0.0
                vtrim = atrim = ""
            idx = add_input(path)
            # 只有「从中间切」的段需要收口：整段从 0 开始，旧版 fps 不会补尾帧
            tail = f",trim=end={seg_dur},setpts=PTS-STARTPTS" if seg_dur else ""
            parts.append(
                f"[{idx}:v]{vtrim}scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},setsar=1,fps={FPS}{tail},format=yuv420p[v{k}]")
            adur = f",apad,atrim=duration={seg_dur:.3f}" if seg_dur else ""
            parts.append(
                f"[{idx}:a]{atrim}aresample=48000,aformat=channel_layouts=stereo,"
                f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo{adur}[a{k}]")
        elif typ == "broll":
            # 垫画面段：一段里剪几个相关镜头拼起来放，**原声静音**，解说垫在上面。
            # 为什么要有这个（2026-11-24）：
            #   解说叠上去时原声会被压掉（duck=0），等于那段原声在成片里被删了。
            #   用户的解法：既然要静音，那就**另外剪几个相关镜头**当垫画面，
            #   主线原声一句不丢，解说还能写长写丰满。
            clips = s.get("clips") or []
            if not clips:
                sys.exit(f"垫画面段 {s['label']} 没写 clips（至少要一个镜头）")
            vid_labels = []
            total_d = 0.0
            for j, c in enumerate(clips):
                cpath = resolve(c["path"]) if c.get("path") else ep_file(src_dir, c["ep"])
                ca, cb = float(c["a"]), float(c["b"])
                cdur = round(cb - ca, 3)
                total_d += cdur
                cidx = add_input(cpath)
                parts.append(
                    f"{vhead(cidx, ca, cb, W, H, FPS)},format=yuv420p[v{k}c{j}]")
                vid_labels.append(f"[v{k}c{j}]")
            parts.append(
                f"{''.join(vid_labels)}concat=n={len(clips)}:v=1:a=0[v{k}]")
            # 音轨：整段静音（这就是「反正也是静音」那句的落点）
            parts.append(
                f"anullsrc=r=48000:cl=stereo:d={round(total_d, 3):.3f},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{k}]")
        elif typ in ("black", "card"):
            d = s["d"]
            img = black_png if typ == "black" else resolve(s.get("path") or card_default)
            if not os.path.exists(img):
                sys.exit(f"图片不存在：{img}")
            idx = add_input(img)
            special[idx] = d
            parts.append(
                f"[{idx}:v]scale={W}:{H},setsar=1,fps={FPS},format=yuv420p[v{k}]")
            parts.append(
                f"anullsrc=r=48000:cl=stereo:d={d},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{k}]")
        else:
            sys.exit(f"未知段类型 {typ}")

        # 段间淡入淡出（前一段 out=black/card 时本段淡入；本段 out!=cut 时本段淡出）。
        # fade 比段长一半还长时收敛到一半，避免超短段整段都在渐变。
        vl, al = f"[v{k}]", f"[a{k}]"
        if item["fade_in"]:
            parts.append(f"{vl}fade=t=in:d={V_FADE:.3f},format=yuv420p[vfi{k}]")
            parts.append(f"{al}afade=t=in:d={A_FADE:.3f}[afi{k}]")
            vl, al = f"[vfi{k}]", f"[afi{k}]"
        if item["fade_out"]:
            dur = item["dur"]
            vd = min(V_FADE, dur / 2)
            ad = min(A_FADE, dur / 2)
            parts.append(f"{vl}fade=t=out:st={max(dur - vd, 0):.3f}:d={vd:.3f},"
                         f"format=yuv420p[vfo{k}]")
            parts.append(f"{al}afade=t=out:st={max(dur - ad, 0):.3f}:d={ad:.3f}[afo{k}]")
            vl, al = f"[vfo{k}]", f"[afo{k}]"
        return vl, al

    ALIM = "alimiter=limit=0.891:attack=5:release=80"

    def vcodec_args(preset, intermediate=False):
        """视频编码参数：默认 libx264；--vcodec h264_nvenc 走硬编码。
        nvenc 的 preset 体系不同（p1–p7），不传；质量用 -cq 且 -b:v 0 按质量走。
        intermediate=True 是并行中间块，画质抬高一档。"""
        vc = args.vcodec or "libx264"
        vq = args.vq
        if intermediate:
            vq = 10 if vc == "libx264" else max(vq - 6, 10)
        if vc == "libx264":
            return ["-c:v", "libx264", "-preset", preset, "-crf", str(vq)]
        return ["-c:v", vc, "-cq", str(vq), "-b:v", "0"]

    def build_chunk(idxs, out_path, preset, audio_pcm):
        """把连续的 idxs（时间轴下标）渲染成一个独立 mov，返回完整 ffmpeg 命令和块时长。"""
        cinputs, cparts, cspecial = [], [], []
        vls, als = [], []
        for k, i in enumerate(idxs):
            vl, al = add_segment(k, timeline[i], cinputs, cparts, cspecial)
            vls.append(vl)
            als.append(al)
        m = len(idxs)
        vlist = "".join(vls)
        alist = "".join(als)
        cparts.append(f"{vlist}concat=n={m}:v=1:a=0[vcat]")
        if audio_pcm:
            # 终程还要过全局链：中间块音频走无损 PCM，alimiter/AAC 留给终程
            cparts.append(f"{alist}concat=n={m}:v=0:a=1,"
                          f"aformat=sample_fmts=s16:channel_layouts=stereo[acat]")
            amap = "[acat]"
            acodec = ["-c:a", "pcm_s16le"]
        else:
            # 没有终程：块里直接出定版音频（alimiter 的 attack/release 只有 5/80ms，
            # 块边界就是段边界、段都 ≥3s，分块限幅跟全片限幅听不出差别）
            cparts.append(f"{alist}concat=n={m}:v=0:a=1[acat]")
            cparts.append(f"[acat]{ALIM}[aout]")
            amap = "[aout]"
            acodec = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
        dur = sum(durations[i] for i in idxs)
        cmd = [ff, "-y", "-v", "warning", "-nostats"]
        for j, p in enumerate(cinputs):
            if j in cspecial:
                cmd += ["-loop", "1", "-t", f"{cspecial[j]:.3f}", "-i", p]
            else:
                cmd += ["-i", p]
        cmd += ["-filter_complex", ";".join(cparts), "-map", "[vcat]", "-map", amap,
                "-t", f"{dur:.3f}",
                "-pix_fmt", "yuv420p", "-r", str(FPS),
                "-video_track_timescale", "15360"] + vcodec_args(preset) + acodec + [out_path]
        return cmd, dur

    plan_path = Path(args.plan).resolve()
    drama = plan_path.parent.parent.name
    task = plan_path.parent.name
    progress.start("mix_trailer", drama, task, total=0, unit="次",
                   note=f"{n} 段 / {total:.1f}s")
    # ffmpeg 可能跑几分钟；每 30 秒 touch 一次，页面不会误判成卡死
    stop_beat = progress.heartbeat("mix_trailer", drama, task,
                                   every=30, note="ffmpeg 装配中")

    def _fail(note, cmd=None):
        # ffmpeg 有时把错因吞在 -v warning 之外（比如输入序列/滤镜图报错被管道截掉），
        # 失败时把完整命令落盘，人可以直接复制重跑看原始报错，不用猜。
        progress.fail("mix_trailer", drama, task, note=note)
        if cmd:
            cmd_log = Path(out).with_suffix(".ffmpeg_cmd.txt")
            try:
                cmd_log.parent.mkdir(parents=True, exist_ok=True)
                cmd_log.write_text(" ".join(cmd), encoding="utf-8")
                print(f"\n装配命令已存：{cmd_log}（复制它单独跑，能看到 ffmpeg 原始报错）")
            except Exception:
                pass

    t_start = time.time()
    try:
        if args.jobs and args.jobs > 1 and n > 1:
            # ================= 并行装配（11-17 加） =================
            nchunks = min(n, args.jobs * 2)
            target = total / nchunks
            chunks, cur, acc = [], [], 0.0
            for i, d in enumerate(durations):
                cur.append(i)
                acc += d
                rest_chunks = nchunks - len(chunks) - 1
                rest_segs = n - (i + 1)
                if acc >= target and rest_chunks > 0 and rest_segs >= rest_chunks:
                    chunks.append(cur)
                    cur, acc = [], 0.0
            if cur:
                chunks.append(cur)
            # 挂了画面参数表（全局链/扫光/字幕）就有终程：中间块用近无损画质
            # （crf 10 veryfast），终程统一过全局链再压定版；没挂就直接在块里
            # 压定版参数，合并时纯复制零再编码。
            tail_needed = bool(spec and (spec.get("global") or sweep_pattern or ass_path))
            # 中间块画质固定近无损（vcodec_args 里 intermediate=True），
            # 这里只定 preset：有终程用 veryfast，没终程直接用定版 preset
            cpreset = "veryfast" if tail_needed else (args.preset or "medium")
            tmpdir = os.path.join(os.path.dirname(out) or ".",
                                  f".parallel_{os.getpid()}")
            os.makedirs(tmpdir, exist_ok=True)
            print(f"\n并行装配：{len(chunks)} 块 / {args.jobs} 进程，"
                  f"{'终程过全局链' if tail_needed else '块即定版、合并零再编码'}")
            chunk_files = [os.path.join(tmpdir, f"chunk_{ci:03d}.mov")
                           for ci in range(len(chunks))]

            def run_chunk(ci):
                cmd, dur = build_chunk(chunks[ci], chunk_files[ci],
                                       cpreset, tail_needed)
                log = chunk_files[ci] + ".log"
                with open(log, "wb") as lf:
                    r = subprocess.run(cmd, stdout=lf, stderr=lf)
                return ci, r.returncode, cmd, log

            failed = None
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
                futs = {ex.submit(run_chunk, ci): ci for ci in range(len(chunks))}
                for f in concurrent.futures.as_completed(futs):
                    ci, rc, cmd, log = f.result()
                    if rc != 0:
                        failed = (ci, rc, cmd, log)
                        print(f"  ✗ 块 {ci + 1} 失败（退出码 {rc}），日志：{log}")
                    else:
                        print(f"  ✓ 块 {ci + 1}/{len(chunks)} 完成"
                              f"（第 {chunks[ci][0] + 1}–{chunks[ci][-1] + 1} 段）")
            if failed:
                ci, rc, cmd, log = failed
                try:
                    print(open(log, encoding="utf-8", errors="replace").read()[-2000:])
                except Exception:
                    pass
                _fail(f"并行块 {ci + 1} 失败 退出码 {rc}", cmd)
                sys.exit(f"并行装配失败，中间产物保留在 {tmpdir}")
            t_chunk = time.time() - t_start
            print(f"分块渲染完成，用时 {t_chunk:.0f}s；开始合并")

            if tail_needed:
                # 终程：所有块解码 → concat 滤镜 → 全局参数包/扫光/字幕 → 定版编码。
                # 音频在块里是无损 PCM，这里过 alimiter 后压 AAC，跟老路径同源。
                K = len(chunks)
                fparts = []
                vlist = "".join(f"[{k}:v]" for k in range(K))
                alist = "".join(f"[{k}:a]" for k in range(K))
                fparts.append(f"{vlist}concat=n={K}:v=1:a=0[vcat]")
                fparts.append(f"{alist}concat=n={K}:v=0:a=1[acat]")
                fparts.append(f"[acat]{ALIM}[aout]")
                cmd = [ff, "-y", "-v", "warning", "-stats"]
                for p in chunk_files:
                    cmd += ["-i", p]
                sweep_idx = None
                if sweep_pattern:
                    sweep_idx = K
                    # 帧序列循环输入容易被主线程队列卡住，给足缓冲
                    cmd += ["-stream_loop", "-1", "-thread_queue_size", "1024",
                            "-framerate", str(sweep_fr), "-i", sweep_pattern]
                tailp, vmap = dedup_tail_chains(spec, W, H, sweep_idx,
                                                ass_path, fontsdir)
                fparts.extend(tailp)
                cmd += ["-filter_complex", ";".join(fparts),
                        "-map", vmap, "-map", "[aout]",
                        "-t", f"{total:.3f}"] + vcodec_args(args.preset or "medium") + [
                        "-pix_fmt", "yuv420p", "-r", str(FPS),
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                        "-movflags", "+faststart", out]
                r = subprocess.run(cmd)
                if r.returncode != 0:
                    _fail(f"终程退出码 {r.returncode}", cmd)
                    sys.exit(f"终程失败，中间产物保留在 {tmpdir}")
            else:
                # 没有全局链：concat 复制合并，零再编码
                lst = os.path.join(tmpdir, "list.txt")
                with open(lst, "w", encoding="utf-8") as f:
                    for p in chunk_files:
                        esc = p.replace("\\", "/").replace("'", "'\\''")
                        f.write(f"file '{esc}'\n")
                cmd = [ff, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                       "-i", lst, "-c", "copy", "-movflags", "+faststart", out]
                r = subprocess.run(cmd)
                if r.returncode != 0:
                    _fail(f"合并退出码 {r.returncode}", cmd)
                    sys.exit(f"合并失败，中间产物保留在 {tmpdir}")
            shutil.rmtree(tmpdir, ignore_errors=True)
        else:
            # ============ 老的单进程路径（--jobs 1 时走这条，行为与以前一致） ============
            inputs, parts = [], []
            special = {}  # input idx -> 展示时长（图片类）
            loop_input = {}  # input idx -> 无限循环标记（扫光帧序列）
            vls, als = [], []  # 各时间轴项的最终画/声标签（带淡入淡出后缀）

            def add_input(p):
                inputs.append(p)
                return len(inputs) - 1

            for k, it in enumerate(timeline):
                vl, al = add_segment(k, it, inputs, parts, special)
                vls.append(vl)
                als.append(al)

            # 扫光帧序列作为额外输入挂在最后；-stream_loop -1 让它循环到全片结束
            sweep_idx = None
            if sweep_pattern:
                sweep_idx = add_input(sweep_pattern)
                loop_input[sweep_idx] = sweep_fr

            vlist = "".join(vls)
            alist = "".join(als)
            parts.append(f"{vlist}concat=n={n}:v=1:a=0[vcat]")
            parts.append(f"{alist}concat=n={n}:v=0:a=1[acat]")
            parts.append(f"[acat]{ALIM}[aout]")

            vmap = "[vcat]"
            if spec:
                tail, vmap = dedup_tail_chains(spec, W, H, sweep_idx,
                                               ass_path, fontsdir)
                parts.extend(tail)

            cmd = [ff, "-y", "-v", "warning", "-stats"]
            for j, p in enumerate(inputs):
                if j in special:
                    cmd += ["-loop", "1", "-t", f"{special[j]:.3f}", "-i", p]
                elif j in loop_input:
                    # 帧序列循环输入容易被主线程队列卡住（ffmpeg 会提示 thread message
                    # queue blocking），给足缓冲，不然全片里每 5 秒都要白等一次
                    cmd += ["-stream_loop", "-1", "-thread_queue_size", "1024",
                            "-framerate", str(loop_input[j]), "-i", p]
                else:
                    cmd += ["-i", p]
            cmd += ["-filter_complex", ";".join(parts), "-map", vmap, "-map", "[aout]",
                    "-t", f"{total:.3f}"] + vcodec_args(args.preset or "medium") + [
                    "-pix_fmt", "yuv420p", "-r", str(FPS),
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    "-movflags", "+faststart", out]
            r = subprocess.run(cmd)
            if r.returncode != 0:
                _fail(f"ffmpeg 退出码 {r.returncode}", cmd)
                sys.exit("ffmpeg 失败")
    finally:
        if stop_beat:
            stop_beat()
    took = time.time() - t_start
    progress.finish("mix_trailer", drama, task, note=f"{total:.1f}s / 实耗 {took:.0f}s")
    print(f"完成 {out}  {total:.2f}s（装配实耗 {took:.0f}s）")
    freeze_check(out, ff)


if __name__ == "__main__":
    main()
