#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切口数据：每集「哪儿没人说话」的停顿表。

为什么单独一个脚本、单独一份数据
--------------------------------
台词库记的是「这句话第几秒被采到」，镜头库记的是「画面第几秒换」，
两者都答不出「这句说完了没有」——切半句就藏在这个缺口里。
停顿表直接量原片音轨，跟认字、认人、打标全无关：纯本地算，
一集一秒，不花钱、不调模型。按剧存一份，以后所有片子共用。

刻意不塞进 tag_shots / extract_lines
------------------------------------
那两个一跑几小时几块钱。停顿表如果跟着它们走，以后想调一个阈值，
就得陪着模型重跑一遍——这种亏吃过。

产出
----
assets/short-drama/<剧>/cuts/epNN.json
  {"ep", "source", "audio_duration", "video_duration",
   "threshold_db", "floor_db", "peak_db", "pauses": [[起, 止], ...]}

pauses 是「没人说话」的区间，单位秒，分辨率 50 毫秒。

用法
----
  # 1) 算表（源文件没变就跳过，按 mtime + 大小判）
  python3 cut_index.py --build --drama 以真心守护至亲
  python3 cut_index.py --build --src-dir <素材根>/以真心守护至亲 --ep 1 2 3

  # 2) 体检一份剪辑清单（装配器里也有同一套判据）
  python3 cut_index.py --check runs/以真心守护至亲/D3_xxx/plan.json

  # 3) 按停顿重定刀口，另存一份清单，不动原件
  python3 cut_index.py --snap runs/以真心守护至亲/D3_xxx/plan.json \
      --out runs/以真心守护至亲/D3_xxx/plan.silence.json

判据（跟人耳对齐的一次改动，别随手放松）
----------------------------------------
· 刀口必须落在停顿里，容差 ±0.03 秒；
· 差 0.30 秒以内算「贴边」，警告但放行；
· 差 0.30 秒以上算「切在说话中间」，装配器拒装；
· 集开头 0.05 秒内、集末视频流最后 0.15 秒内，一律放行。
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import wave
from pathlib import Path

# 配套库没装就当场补（装完专家包不用任何人敲命令）。
# 必须放在 import numpy 之前：顶层 import 缺库会直接抛错，补都没机会补。
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
try:
    from _env import HOST as _HOST, data_root as _data_root, read_env as _read_env
except Exception:
    _HOST = Path(__file__).resolve().parents[3]

    def _data_root() -> Path:  # 兜底也不能指到当前目录去，见 repo_root 的说明
        _docs = Path.home() / "Documents"
        return (_docs if _docs.is_dir() else Path.home()) / "短剧工作区"

    def _read_env(root=None) -> dict:
        return dict(os.environ)


_SUP = _HOST / "skills" / "short-drama-supervisor" / "scripts"
if _SUP.is_dir() and str(_SUP) not in sys.path:
    sys.path.insert(0, str(_SUP))
try:
    import deps
    deps.need("numpy")
except SystemExit:
    raise
except Exception:
    pass

import numpy as np

HOP_SEC = 0.05          # 音量包络分辨率
MIN_PAUSE = 0.12        # 短于这个的静音不算「缝」
MIN_PAUSE_RATE = 20.0   # 每分钟停顿少于这么多处，说明阈值没切开（整集压着配乐/环境音），走自适应兜底
                        # 参照：安静棚录音的剧实测 30~45 处/分钟；压着配乐的剧兜底后 37~56 处/分钟。
MIN_GAP = 0.08          # 两处停顿之间夹的音头短于这个就并成一处
EDGE_TOL = 0.03         # 刀口落在停顿边界外的容差
WARN_DIST = 0.30        # 距最近停顿多远以内算贴边（超过就是切在话中间）
TAIL_GUARD = 0.15       # 集末视频流最后这么多秒，刀口一律放行
IN_MIN_GAP = 0.35       # 入点只往前退到「像样的句间停顿」：0.3 秒左右常是同一句里的换气


def repo_root() -> Path:
    """数据目录。唯一出口是 _env.data_root()（11-16 修）。

    以前这里找不到工作台就 `return Path.cwd()`，不报错、不提示——装成插件后
    必然走到这一步，于是切口表被静默写到「当前所在目录/assets/...」去。
    自动跑片时没炸，纯粹是因为上层起子进程时把工作目录设成了数据目录；
    人手敲一次命令就错。
    """
    return _data_root()


def find_ff(override=None):
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
    sys.exit("找不到 ffmpeg：跑一次开工自检会自动下；"
             "下不动就手动放进 <数据目录>/bin；或者用 --ff 指定绝对路径")


def find_ffprobe(ff):
    """ffprobe 和 ffmpeg 是一套的：先取同目录那份，再问 _env，最后看 PATH。"""
    if ff:
        exe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        cand = os.path.join(os.path.dirname(ff), exe)
        if os.path.exists(cand):
            return cand
    try:
        from _env import ffprobe as _fp
        cand = _fp()
        if os.path.isabs(cand) and os.path.exists(cand):
            return cand
    except Exception:
        pass
    return shutil.which("ffprobe")


# ---------- 素材定位 ----------

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".m4v", ".ts", ".avi")


def _has_videos(d) -> bool:
    """这个目录里到底有没有视频文件。

    只判「目录在不在」不够：数据目录下按约定会有 assets/short-drama/<剧名>/
    （放台词库、镜头库、停顿表），它一直存在但从来没有 mp4。要是拿它当素材目录
    返回，报错就会变成「这里没扫到剧集文件」，而人看到的目录是存在的——
    于是所有力气都花在查「素材怎么没拷进来」上（测试机实测）。
    """
    try:
        return any(f.lower().endswith(VIDEO_EXTS) for f in os.listdir(d))
    except OSError:
        return False


def drama_src_dir(drama, src_dir):
    """原片在哪。认四种登记方式，顺序固定，命中即返回。

    11-16 加第 3 种（.env 的 SHORT_DRAMA_ASSET_ROOT）：台词抽取走的是 .env，
    这里原来只认 config/drama-sources.json（默认是空的），两条路口径不一致，
    后果是「台词抽得出来、切口表抽不出来」，而且失败信息指向一个空目录。
    """
    if src_dir:
        return os.path.abspath(os.path.expandvars(src_dir))
    root = repo_root()
    tried = []

    # ① 本机的剧名 → 目录对照表（素材搬家只改这里）
    reg = root / "config" / "drama-sources.json"
    if reg.exists():
        tried.append(str(reg))
        try:
            d = json.load(open(reg, encoding="utf-8")).get("dramas", {})
            if drama in d:
                return os.path.expandvars(d[drama])
        except Exception:
            pass

    # ② .env 的素材根（和 extract_lines.py 同一个口径：素材根/剧名）
    try:
        base = (os.path.expandvars(os.path.expanduser(
            (_read_env(root).get("SHORT_DRAMA_ASSET_ROOT") or "").strip())))
    except Exception:
        base = ""
    if base:
        cand = os.path.join(base, drama)
        tried.append(cand)
        if os.path.isdir(cand) and _has_videos(cand):
            return cand

    # ③ 老习惯：用户主目录下的「短剧」文件夹
    cand = os.path.join(os.path.expanduser("~"), "短剧", drama)
    tried.append(cand)
    if os.path.isdir(cand) and _has_videos(cand):
        return cand

    # ④ 数据目录里的素材位（必须有视频才算，见 _has_videos）
    cand = str(root / "assets" / "short-drama" / drama)
    tried.append(cand)
    if _has_videos(cand):
        return cand

    sys.exit(
        f"找不到《{drama}》的原片目录。找过这些地方：\n  "
        + "\n  ".join(tried)
        + f"\n两个办法：① 在 {root / '.env'} 里把 SHORT_DRAMA_ASSET_ROOT 填成"
        "放各剧文件夹的上层目录；② 直接加 --src-dir 指到这部剧的文件夹。"
    )


def scan_episodes(src_dir):
    """扫一遍素材目录，返回 {集号: 文件路径}。命名兼容第N集 / epN / 纯数字。"""
    exts = VIDEO_EXTS
    pat = re.compile(
        r"(?:第\s*0*(\d+)集|(?<![A-Za-z0-9])ep(?:isode)?[\s_.-]*0*(\d+))(?![0-9])", re.I)
    out = {}
    for f in sorted(os.listdir(src_dir)):
        if not f.lower().endswith(exts):
            continue
        m = pat.search(f)
        n = int(m.group(1) or m.group(2)) if m else None
        if n is None:
            stem = os.path.splitext(f)[0].strip()
            if stem.isdigit():
                n = int(stem)
        if n is not None and n not in out:
            out[n] = os.path.join(src_dir, f)
    return dict(sorted(out.items()))


def cuts_dir_for(drama):
    return repo_root() / "assets" / "short-drama" / drama / "cuts"


# ---------- 量一集 ----------

def probe_dur(fp, path, sel):
    if not fp:
        return None
    r = subprocess.run(
        [fp, "-v", "error", "-select_streams", sel,
         "-show_entries", "stream=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return round(float(r.stdout.strip().splitlines()[0]), 3)
    except Exception:
        return None


def decode_pcm(ff, path):
    r = subprocess.run(
        [ff, "-v", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "wav", "-"],
        capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"解音轨失败：{r.stderr.decode('utf-8', 'ignore')[:200]}")
    w = wave.open(io.BytesIO(r.stdout))
    raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def envelope_db(pcm, hop=800):
    n = len(pcm) // hop
    if n == 0:
        return np.array([])
    blocks = pcm[:n * hop].reshape(n, hop)
    rms = np.sqrt((blocks ** 2).mean(axis=1))
    return 20 * np.log10(rms + 1e-9)


def pick_threshold(db):
    """自适应阈值：底噪和峰值之间取一个，且夹在 -55 ~ -32 dB。"""
    floor = float(np.percentile(db, 20))
    peak = float(np.percentile(db, 85))
    thr = floor + 0.35 * (peak - floor)
    return max(min(thr, -32.0), -55.0), floor, peak


def find_pauses(db, thr, min_pause=MIN_PAUSE, min_gap=MIN_GAP):
    quiet = db < thr
    n = len(db)
    out = []
    i = 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if (j - i) * HOP_SEC >= min_pause:
                out.append([i * HOP_SEC, j * HOP_SEC])
            i = j
        else:
            i += 1
    merged = []
    for a, b in out:
        if merged and a - merged[-1][1] < min_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [[round(a, 2), round(b, 2)] for a, b in merged]


def build_one(ff, fp, path, ep):
    pcm = decode_pcm(ff, path)
    db = envelope_db(pcm)
    if len(db) == 0:
        raise RuntimeError("音轨为空")
    thr, floor, peak = pick_threshold(db)
    pauses = find_pauses(db, thr)
    thr_mode = "absolute"
    # 绝对阈值封顶在 -32dB，前提是「没人说话时真的安静」。
    # 有的剧整集压着配乐或环境音，最低也只到 -16dB：轻则整集测出 0 处停顿，
    # 重则只剩片尾一处——两种都等于没体检，装配器只能全片「无法判断」。
    # 判据改成「停顿密度」：每分钟不足 6 处就认为这个阈值没切开，退回自适应值重测一次。
    # 自适应值仍在底噪与峰值之间（人声之下），只是不再硬卡 -32。
    dur_min = max(len(db) * HOP_SEC / 60.0, 1e-6)
    if len(pauses) / dur_min < MIN_PAUSE_RATE:
        thr_raw = round(min(max(floor + 0.25 * (peak - floor), -55.0), peak - 3.0), 1)
        if thr_raw > thr:
            alt = find_pauses(db, thr_raw)
            if len(alt) > len(pauses):
                thr, pauses, thr_mode = thr_raw, alt, "adaptive_fallback"
    st = os.stat(path)
    return {
        "ep": ep,
        "source": {"path": path, "mtime": int(st.st_mtime), "size": st.st_size},
        "audio_duration": round(len(db) * HOP_SEC, 2),
        "video_duration": probe_dur(fp, path, "v:0"),
        "threshold_db": round(thr, 1),
        "threshold_mode": thr_mode,
        "floor_db": round(floor, 1),
        "peak_db": round(peak, 1),
        "pauses": pauses,
        "built_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }


def cache_ok(dst, src_path):
    if not os.path.exists(dst):
        return False
    try:
        d = json.load(open(dst, encoding="utf-8"))
    except Exception:
        return False
    s = d.get("source") or {}
    st = os.stat(src_path)
    return s.get("path") == src_path and s.get("mtime") == int(st.st_mtime) \
        and s.get("size") == st.st_size


def cmd_build(args):
    src = drama_src_dir(args.drama, args.src_dir)
    drama = args.drama or os.path.basename(os.path.normpath(src))
    eps = scan_episodes(src)
    if args.ep:
        eps = {int(k): v for k, v in eps.items() if int(k) in args.ep}
    if not eps:
        sys.exit(f"{src} 下没扫到剧集文件")
    out_dir = cuts_dir_for(drama)
    out_dir.mkdir(parents=True, exist_ok=True)
    ff = find_ff(args.ff)
    fp = find_ffprobe(ff)
    made = skipped = 0
    warn = []
    for ep, path in eps.items():
        dst = out_dir / f"ep{ep:02d}.json"
        if not args.force and cache_ok(str(dst), path):
            skipped += 1
            continue
        try:
            d = build_one(ff, fp, path, ep)
        except Exception as e:
            warn.append(f"ep{ep}: {e}")
            continue
        json.dump(d, open(dst, "w", encoding="utf-8"), ensure_ascii=False)
        made += 1
        rate = len(d["pauses"]) / max(d["audio_duration"], 1) * 60
        print(f"  ep{ep:>2}  {d['audio_duration']:>6.1f}s  阈值{d['threshold_db']:>6.1f}dB"
              f"  停顿{len(d['pauses']):>3}处（每分钟 {rate:.0f} 处）")
    print(f"\n{drama}：新算 {made} 集，沿用缓存 {skipped} 集 → {out_dir}")
    for w in warn:
        print("  !! " + w)


# ---------- 判据（装配器里是同一套，改这里要同步改 mix_trailer.py） ----------

def nearest_pause(pauses, t):
    """返回 (距离, 最近的停顿区间)；t 在停顿内侧则距离为 0。"""
    best = (1e9, None)
    for a, b in pauses:
        if a - EDGE_TOL - 1e-9 <= t <= b + EDGE_TOL + 1e-9:
            return 0.0, [a, b]
        d = a - t if t < a else t - b
        if d < best[0]:
            best = (d, [a, b])
    return best


def judge_cut(pauses, t, video_dur, is_in):
    """判一个刀口。返回 (级别, 说明)。级别 error / warn / ok。"""
    if t <= 0.05:
        return "ok", "集开头"
    if video_dur and t >= video_dur - TAIL_GUARD:
        return "ok", "贴集末"
    d, seg = nearest_pause(pauses, t)
    if d <= 0:
        return "ok", ""
    if seg is None:
        return "warn", "整集没测到停顿，无法判断"
    hint = f"最近停顿 {seg[0]:.2f}–{seg[1]:.2f}"
    where = "之后" if is_in else "之前"
    if d <= WARN_DIST:
        return "warn", f"刀口差 {d:.2f}s，{hint}（刀口{where}）"
    return "error", f"切在说话中间，距最近停顿 {d:.2f}s，{hint}"


def load_cuts(drama):
    d = cuts_dir_for(drama)
    if not d.is_dir():
        return {}
    out = {}
    for f in sorted(d.glob("ep*.json")):
        m = re.match(r"ep(\d+)\.json$", f.name)
        if not m:
            continue
        try:
            out[int(m.group(1))] = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
    return out


def drama_of_plan(plan, plan_path):
    sd = os.environ.get("SHORT_DRAMA_SRC_DIR") or plan.get("src_dir")
    if sd:
        sd = os.path.expandvars(sd)
        if not os.path.isabs(sd):
            sd = os.path.join(os.path.dirname(os.path.abspath(plan_path)), sd)
        name = os.path.basename(os.path.normpath(sd))
        if name:
            return name
    return os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(plan_path))))


def check_plan(plan, plan_path, cuts=None):
    """体检一份 plan，返回 (errors, warns, 检查过几个刀口, 有没有表)。"""
    drama = drama_of_plan(plan, plan_path)
    if cuts is None:
        cuts = load_cuts(drama)
    errors, warns, n = [], [], 0
    if not cuts:
        return errors, warns, 0, False
    for s in plan.get("segs", []):
        if s.get("type") != "ep":
            continue
        ep = s["ep"]
        c = cuts.get(ep)
        if not c:
            warns.append((ep, s["label"], "无停顿表", f"ep{ep} 没算过切口数据"))
            continue
        vd = c.get("video_duration")
        for is_in, t, tag in ((True, s["a"], "入点"), (False, s["b"], "出点")):
            n += 1
            lvl, why = judge_cut(c.get("pauses") or [], t, vd, is_in)
            if lvl == "error":
                errors.append((ep, s["label"], tag, t, why))
            elif lvl == "warn" and why:
                warns.append((ep, s["label"], tag, f"{t:.2f}s {why}"))
    return errors, warns, n, True


def cmd_check(args):
    plan_path = os.path.abspath(args.check_plan)
    plan = json.load(open(plan_path, encoding="utf-8"))
    drama = drama_of_plan(plan, plan_path)
    cuts = load_cuts(drama)
    if not cuts:
        print(f"{drama}：没有切口数据，先跑 --build --drama {drama}")
        return 0
    errors, warns, n, _ = check_plan(plan, plan_path, cuts)
    print(f"{drama} / {os.path.basename(os.path.dirname(plan_path))}："
          f"体检 {n} 个刀口，切在说话中间 {len(errors)} 处，贴边 {len(warns)} 处")
    for ep, label, tag, t, why in errors:
        print(f"  ✗ ep{ep:>2} {tag} {t:>6.2f}  {label}  {why}")
    for ep, label, tag, why in warns:
        print(f"  ⚠ ep{ep:>2} {tag}  {label}  {why}")
    return 1 if errors else 0


# ---------- 按停顿重定刀口 ----------

def snap(ep_cut, t, is_in, max_out=1.5, max_in=1.2, keep=()):
    """把一个刀口挪到句间停顿里。返回 (新时刻, 说明)。"""
    if any(abs(t - k) < 0.02 for k in keep):
        return t, "指定保留"
    pauses = ep_cut.get("pauses") or []
    vd = ep_cut.get("video_duration")
    if t <= 0.05:
        return t, "集开头"
    for a, b in pauses:
        if a - EDGE_TOL - 1e-9 <= t <= b + EDGE_TOL + 1e-9:
            return t, "本来就在停顿里"
    if not is_in:
        # 出点：往后顺延到下一处停顿，但不能整句吞掉太多
        cand = [(a, b) for a, b in pauses if a >= t - 0.06 and a <= t + max_out]
        if cand:
            a, b = cand[0]
            lo, hi = a + 0.03, b - 0.03
            mid = (a + b) / 2
            if hi >= lo:
                v = min(max(mid, lo), hi)
                if vd:
                    v = min(v, vd - 0.06)
                return round(v, 2), "顺延到下一处停顿"
        # 后面没缝（或超上限）：退到前一处停顿，宁可整句舍掉
        back = [(a, b) for a, b in pauses if b <= t + 0.10]
        if back:
            a, b = back[-1]
            if t - (a + b) / 2 <= max_out + 0.4:
                return round((a + b) / 2, 2), "这句念不完，退到前一处停顿（整句舍掉）"
        return t, "1.5 秒内没缝，未动"
    # 入点：往前退到上一处停顿。只认够长的（≥0.35s）：0.3 秒左右的多半是同一句里的换气，
    # 退过去等于切进句子中间——11-14 ep5 30.45–30.75 实测踩过，字幕一直没换。
    cand = [(a, b) for a, b in pauses
            if b <= t + 0.12 and (a + b) / 2 >= t - max_in and (b - a) >= IN_MIN_GAP]
    if cand:
        a, b = cand[-1]
        return round(max((a + b) / 2, 0.0), 2), "退到上一处停顿"
    return t, f"往前 {max_in} 秒内没有够长的停顿（≥{IN_MIN_GAP}s），未动"


def cmd_snap(args):
    plan_path = os.path.abspath(args.snap_plan)
    plan = json.load(open(plan_path, encoding="utf-8"))
    drama = drama_of_plan(plan, plan_path)
    cuts = load_cuts(drama)
    if not cuts:
        sys.exit(f"{drama}：没有切口数据，先跑 --build --drama {drama}")
    keepmap = {}
    for spec in (args.keep or []):
        ep, t = spec.split(":")
        keepmap.setdefault(int(ep), []).append(float(t))
    roles = {}
    for s in plan.get("segs", []):
        if s.get("type") != "ep":
            continue
        for key, val in (((s["ep"], round(s["a"], 2)), "in"),
                         ((s["ep"], round(s["b"], 2)), "out")):
            roles[key] = val if key not in roles else "both"
    newt, log = {}, []
    for (ep, t), role in sorted(roles.items()):
        c = cuts.get(ep)
        if not c:
            newt[(ep, t)] = t
            log.append((ep, t, t, 0.0, role, "无停顿表，未动"))
            continue
        if role == "both":
            v_out, w_out = snap(c, t, False, args.max_out, args.max_in, keepmap.get(ep, ()))
            v_in, w_in = snap(c, t, True, args.max_out, args.max_in, keepmap.get(ep, ()))
            v, w = (v_out, w_out) if abs(v_out - t) <= abs(v_in - t) else (v_in, w_in)
        elif role == "out":
            v, w = snap(c, t, False, args.max_out, args.max_in, keepmap.get(ep, ()))
        else:
            v, w = snap(c, t, True, args.max_out, args.max_in, keepmap.get(ep, ()))
        newt[(ep, t)] = v
        if abs(v - t) > 0.02:
            log.append((ep, t, v, round(v - t, 2), role, w))
    for s in plan.get("segs", []):
        if s.get("type") != "ep":
            continue
        s.setdefault("_orig", [s["a"], s["b"]])
        s["a"] = newt[(s["ep"], round(s["a"], 2))]
        s["b"] = newt[(s["ep"], round(s["b"], 2))]
    plan["_cut_policy"] = (
        f"刀口只落在原片停顿里：出点往后最多顺延 {args.max_out}s，入点往前最多 {args.max_in}s；"
        "念不完又没缝就退到前一处停顿，宁可整句舍掉也不切半句。")
    out = args.out
    json.dump(plan, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    tot = sum(s["b"] - s["a"] for s in plan["segs"] if s["type"] == "ep")
    card = sum(s.get("d", 0) for s in plan["segs"] if s["type"] != "ep")
    short = [(s["label"], s["a"], s["b"]) for s in plan["segs"]
             if s["type"] == "ep" and s["b"] - s["a"] < 1.5]
    print(f"{drama} / {os.path.basename(os.path.dirname(plan_path))}："
          f"调 {len(log)} 个刀口，实拍 {tot:.1f}s + 卡 {card:.1f}s = {tot + card:.1f}s")
    for ep, t, v, dd, role, why in log:
        print(f"  ep{ep:>2} {t:>6.2f} → {v:>6.2f} ({dd:+.2f}) {role:>4}  {why}")
    for lab, a, b in short:
        print(f"  !! 过短 {lab} {a}-{b}（{b - a:.2f}s）")
    print(f"→ {out}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="切口数据：停顿表 / 体检 / 重定刀口")
    ap.add_argument("--build", action="store_true", help="算停顿表")
    ap.add_argument("--check", dest="check_plan", metavar="PLAN", help="体检一份剪辑清单")
    ap.add_argument("--snap", dest="snap_plan", metavar="PLAN", help="按停顿重定刀口")
    ap.add_argument("--out", help="--snap 的输出文件")
    ap.add_argument("--drama", help="剧名（--build 用）")
    ap.add_argument("--src-dir", help="素材目录（--build 用，优先于剧名）")
    ap.add_argument("--ep", nargs="*", type=int, help="只算这几集")
    ap.add_argument("--force", action="store_true", help="忽略缓存重算")
    ap.add_argument("--max-out", type=float, default=1.5, help="出点最多往后顺延秒数")
    ap.add_argument("--max-in", type=float, default=1.2, help="入点最多往前退秒数")
    ap.add_argument("--keep", nargs="*", help="指定不动的刀口，形如 1:4.30")
    ap.add_argument("--ff", help="ffmpeg 绝对路径")
    args = ap.parse_args()

    if args.build:
        return cmd_build(args) or 0
    if args.check_plan:
        return cmd_check(args)
    if args.snap_plan:
        if not args.out:
            sys.exit("--snap 要配 --out")
        return cmd_snap(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
