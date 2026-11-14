# -*- coding: utf-8 -*-
"""
v3 验证脚本（v1 / v2 全部保留不动，用于对照）
相对 v2 的两处改动：
  1) 去掉「小的画中画」：段3 的别人辅助窗、段4 的细节放大窗全部删除。
     A1 的大画中画（主画面缩小 + 模糊背景）保留，A3 参数包、A4 动态浮层一律不动。
  2) 新增「斜向扫光」：一条倾斜、边缘柔和、带轻微波动的淡光带，按全局时间缓慢扫过全片。
     峰值不透明度 7%，肉眼不明显，但每帧位置都不同。
     来源：张老师 2026-11-14 提供的《影视解说二创内容原创化与去重方案》5.1「低强度动态特效」。
     实现：numpy 预生成一个周期的扫光层，逐帧复用，叠在帧序列最底层。

红线：不碰剪辑清单、不改段序/段长/台词/五幕/原字幕，只动画面。
产物：编排版_v3/ 下 帧序列、GIF、演示片、抽帧总览、扫光对照图
"""
import json, os, subprocess, sys, math, glob

ROOT = "/Users/zgl/vibecut-ecom"
RUN = f"{ROOT}/runs/以真心守护至亲/D3_线一认不认这个妈"
SRC = f"{RUN}/build/v3.mp4"
PACK = f"{ROOT}/packs/short-drama/以真心守护至亲/drama-pack.json"
OUT = f"{RUN}/build/去重预览/编排版_v3"
SEQ = f"{OUT}/帧序列"
GIF = f"{OUT}/GIF预览"
os.makedirs(SEQ, exist_ok=True)
os.makedirs(GIF, exist_ok=True)

FF = "/opt/anaconda3/bin/ffmpeg"
FONT = "/System/Library/Fonts/PingFang.ttc"
PW, PH = 540, 960          # 预览分辨率（与 v1/v2 一致）
FPS, DUR = 24, 12          # 每段 12 秒
NFR = FPS * DUR            # 288 帧/段

# ---- 扫光参数（集中放这儿，方便以后调；峰值强度是护栏，别调高） ----
SWEEP_PEAK   = 0.07        # 成片峰值不透明度，落在资料建议的 3%~8% 区间
SWEEP_PERIOD = 6.5         # 扫过一次全屏的秒数
SWEEP_STEPS  = 42          # 一个周期预生成多少张扫光层
SWEEP_ANGLE  = 32          # 光带倾角（度）：20° 在竖屏上偏「横」，调到 32° 才看得出斜着扫
SWEEP_SHOW   = 0.45        # 仅「示意 GIF / 对照图」用的放大强度，不进成片

import numpy as np
from PIL import Image, ImageDraw, ImageFont

def font(sz):
    return ImageFont.truetype(FONT, sz)

def wrap(text, f, maxw):
    lines, cur = [], ""
    for ch in text:
        if f.getlength(cur + ch) <= maxw:
            cur += ch
        else:
            lines.append(cur); cur = ch
    if cur: lines.append(cur)
    return lines

# ---------- 观众向文案：全部自动取，不手写（v2 定的规矩） ----------
pack = json.load(open(PACK))
facts = {x["field"]: x["value"] for x in pack["drama_facts"]}
TITLE = str(facts.get("title", "以真心守护至亲"))
HOOK = pack["hook_map"][0]["opening_lines"][0]
PEOPLE = [(c["name"], c["evidence"]["quote"]) for c in pack["characters"][:3]]
QUOTE = pack["elements"]["golden_quotes"][1]["quote"]
ENDLINE = pack["elements"]["ctas"][0]["text"]

# ---------- 配色 ----------
GOLD = (222, 184, 110)
CREAM = (250, 244, 232)
DARK = (18, 15, 12)

def ease(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3

def fade(t, t0, tin, hold, tout):
    if t < t0 or t > t0 + tin + hold + tout: return 0.0
    if t < t0 + tin: return ease((t - t0) / tin)
    if t < t0 + tin + hold: return 1.0
    return 1.0 - ease((t - (t0 + tin + hold)) / tout)

def base_frame():
    return Image.new("RGBA", (PW, PH), (0, 0, 0, 0))

# ---------- 扫光：斜向、柔边、带一点波动的淡光带 ----------
def make_sweep_layers(peak):
    """预生成一个完整扫过周期的光带层；每帧按相位取一张复用，比逐帧算快得多。"""
    yy, xx = np.mgrid[0:PH, 0:PW].astype(np.float32)
    th = math.radians(SWEEP_ANGLE)
    nx, ny = -math.sin(th), math.cos(th)       # 法线 = 光带扫过的方向
    tx, ty = math.cos(th), math.sin(th)        # 切线 = 光带自身的方向
    proj = nx * xx + ny * yy                   # 每个像素在扫过方向上的坐标
    pmin, pmax = float(proj.min()), float(proj.max())
    span = pmax - pmin
    band_w = span * 0.16                       # 光带宽度（高斯衰减）
    along = tx * xx + ty * yy                  # 沿光带方向的坐标，用来做轻微弯曲
    layers = []
    for k in range(SWEEP_STEPS):
        phase = k / SWEEP_STEPS
        center = pmin - band_w * 2 + (span + band_w * 4) * phase
        wobble = 16.0 * np.sin(along / 150.0 + phase * 2 * math.pi)  # 轻微波浪，不死板
        dist = proj + wobble - center
        g = np.exp(-(dist / band_w) ** 2)     # 0~1 的柔和亮带
        a = (g * peak * 255).astype(np.uint8)
        rgba = np.zeros((PH, PW, 4), np.uint8)
        rgba[..., 0] = 255
        rgba[..., 1] = 250
        rgba[..., 2] = 235                    # 暖白，提亮而非色块
        rgba[..., 3] = a
        layers.append(Image.fromarray(rgba, "RGBA"))
    return layers

SWEEP = make_sweep_layers(SWEEP_PEAK)

def sweep_phase(si, t):
    """全局时间连续，拼接处不跳变"""
    gt = si * DUR + t
    return int((gt % SWEEP_PERIOD) / SWEEP_PERIOD * SWEEP_STEPS) % SWEEP_STEPS

# ---------- 常驻层 / 动态卡（v2 原样，未改） ----------
def gradient_scrim(im, y0, y1, maxalpha=165):
    ov = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    h = y1 - y0
    for i in range(h):
        a = int(maxalpha * (i / h) ** 1.6)
        d.line((0, y1 - i, PW, y1 - i), fill=DARK + (a,))
    im.alpha_composite(ov)

def badge(im):
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((22, 30, 22 + 6, 78), radius=3, fill=GOLD + (210,))
    f = font(27)
    d.text((40, 30), TITLE, font=f, fill=CREAM + (235,))

def progress(im, ratio):
    d = ImageDraw.Draw(im)
    x0, x1, y = 32, PW - 32, PH - 30
    d.rounded_rectangle((x0, y, x1, y + 4), radius=2, fill=(255, 255, 255, 55))
    seg = (x1 - x0) / 5
    for i in range(5):
        cx = x0 + seg * i + seg / 2
        d.ellipse((cx - 4, y - 2, cx + 4, y + 6), fill=(255, 255, 255, 90))
    d.rounded_rectangle((x0, y, x0 + (x1 - x0) * ratio, y + 4), radius=2,
                        fill=GOLD + (225,))

def hook_card(im, t, alpha):
    if alpha <= 0: return
    gradient_scrim(im, int(PH * 0.55), PH, 170)
    layer = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x = 38
    d.rounded_rectangle((x, int(PH * 0.63), x + 5, int(PH * 0.63) + 190),
                        radius=2, fill=GOLD + (int(220 * alpha),))
    f = font(37)
    lines = wrap(HOOK, f, PW - 100)
    y = int(PH * 0.64)
    for i, ln in enumerate(lines[:4]):
        k = ease((alpha - i * 0.12) / 0.55)
        if k <= 0: continue
        yy = y + i * 52 + int(16 * (1 - k))
        d.text((x + 22, yy), ln, font=f, fill=CREAM + (int(250 * k),))
    im.alpha_composite(layer)

def people_card(im, t, alpha):
    if alpha <= 0: return
    layer = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    y0 = int(PH * 0.30)
    for i, (name, line) in enumerate(PEOPLE):
        k = ease((alpha - i * 0.16) / 0.60)
        if k <= 0: continue
        y = y0 + i * 132
        dx = int(70 * (1 - k))
        d.rounded_rectangle((34 + dx, y, PW - 28 + dx, y + 116),
                            radius=10, fill=DARK + (int(175 * k),))
        d.rounded_rectangle((34 + dx, y, 39 + dx, y + 116),
                            radius=2, fill=GOLD + (int(235 * k),))
        nf = font(31)
        d.text((56 + dx, y + 14), name, font=nf, fill=GOLD + (int(250 * k),))
        qf = font(24)
        qtxt = "「" + line + "」"
        for j, ln in enumerate(wrap(qtxt, qf, PW - 120)[:2]):
            d.text((56 + dx, y + 56 + j * 30), ln, font=qf,
                   fill=CREAM + (int(235 * k),))
    im.alpha_composite(layer)

def quote_card(im, t, alpha):
    if alpha <= 0: return
    layer = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, maxw = 40, PW - 80
    sz = 52
    f = font(sz)
    while d.textlength(QUOTE, font=f) > maxw and sz > 28:
        sz -= 2
        f = font(sz)
    k = ease(alpha)
    y = int(PH * 0.72) + int(18 * (1 - k))
    for ox in (-2, 0, 2):
        for oy in (-2, 0, 2):
            d.text((x + ox, y + oy), QUOTE, font=f, fill=DARK + (int(210 * k),))
    d.text((x, y), QUOTE, font=f, fill=CREAM + (int(255 * k),))
    im.alpha_composite(layer)

def end_card(im, t, alpha):
    if alpha <= 0: return
    gradient_scrim(im, 0, int(PH * 0.42), 150)
    layer = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    k = ease(alpha)
    sz = 46
    f = font(sz)
    while d.textlength(ENDLINE, font=f) > PW - 80 and sz > 28:
        sz -= 2
        f = font(sz)
    y = int(PH * 0.22) + int(20 * (1 - k))
    w = d.textlength(ENDLINE, font=f)
    cx = (PW - w) / 2
    for ox in (-2, 0, 2):
        for oy in (-2, 0, 2):
            d.text((cx + ox, y + oy), ENDLINE, font=f, fill=DARK + (int(210 * k),))
    d.text((cx, y), ENDLINE, font=f, fill=CREAM + (int(255 * k),))
    d.rounded_rectangle(((PW - 90) / 2, y + 72, (PW + 90) / 2, y + 75),
                        radius=2, fill=GOLD + (int(230 * k),))
    im.alpha_composite(layer)

PLAN = [
    dict(name="seg1", cards=[("hook",   dict(t0=2.5, tin=0.7, hold=3.5, tout=0.8))]),
    dict(name="seg2", cards=[("people", dict(t0=2.5, tin=0.8, hold=4.0, tout=0.8))]),
    dict(name="seg3", cards=[("quote",  dict(t0=5.5, tin=0.6, hold=4.5, tout=0.9))]),
    dict(name="seg4", cards=[("end",    dict(t0=8.0, tin=0.7, hold=2.3, tout=1.0))]),
]
CARD_FN = {"hook": hook_card, "people": people_card, "quote": quote_card, "end": end_card}

def run(args, tag):
    print("→", tag, flush=True)
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        print("!! 失败", tag, r.stderr[-2200:], flush=True)
        sys.exit(1)

# ============ 1. 帧序列：扫光在最底 → 常驻层 → 动态卡 ============
for si, spec in enumerate(PLAN):
    d = f"{SEQ}/{spec['name']}"
    os.makedirs(d, exist_ok=True)
    for n in range(NFR):
        t = n / FPS
        im = base_frame()
        im.alpha_composite(SWEEP[sweep_phase(si, t)])   # 新增：斜向淡光带
        badge(im)
        progress(im, (si + t / DUR) / 4)
        for kind, cfg in spec["cards"]:
            a = fade(t, cfg["t0"], cfg["tin"], cfg["hold"], cfg["tout"])
            CARD_FN[kind](im, t, a)
        im.save(f"{d}/{n:04d}.png")
    print(f"帧序列 {spec['name']} {NFR} 帧（含扫光）", flush=True)

# ============ 2. GIF 预览 ============
# 2a. 四张卡片动效 GIF（沿用 v2，帧里已经带着扫光）
SEG_SRC_T = [60, 20, 88, 100]
for si, spec in enumerate(PLAN):
    for kind, cfg in spec["cards"]:
        t0, total = cfg["t0"], cfg["tin"] + cfg["hold"] + cfg["tout"]
        n0, cnt = int((t0 - 0.4) * FPS), int((total + 0.8) * FPS)
        run([FF, "-y", "-v", "error", "-ss", str(SEG_SRC_T[si] + t0 + 1),
             "-i", SRC, "-frames:v", "1", "-vf", f"scale={PW}:{PH}",
             "-update", "1", f"{OUT}/_bg_{si}.png"], f"GIF 底图 {si}")
        bg = Image.open(f"{OUT}/_bg_{si}.png").convert("RGBA")
        frames = []
        for n in range(n0, min(n0 + cnt, NFR)):
            ly = Image.open(f"{SEQ}/{spec['name']}/{n:04d}.png").convert("RGBA")
            fr = bg.copy()
            fr.alpha_composite(ly)
            frames.append(fr.convert("RGB").resize((270, 480)))
        gp = f"{GIF}/{kind}动效.gif"
        frames[0].save(gp, save_all=True, append_images=frames[1:],
                       duration=int(1000 / FPS), loop=0, optimize=True)
        print(f"GIF {gp} {len(frames)} 帧", flush=True)
        os.remove(f"{OUT}/_bg_{si}.png")

# 2b. 扫光单独示意（强度放大到能看清，仅用于看懂效果，不进成片）
run([FF, "-y", "-v", "error", "-ss", "30", "-i", SRC, "-frames:v", "1",
     "-vf", f"scale={PW}:{PH}", "-update", "1", f"{OUT}/_sweep_bg.png"], "扫光底图")
_bg = Image.open(f"{OUT}/_sweep_bg.png").convert("RGBA")
_show = make_sweep_layers(SWEEP_SHOW)
_small_bg = _bg.resize((270, 480))
show_frames = []
for k in range(SWEEP_STEPS):
    fr = _small_bg.copy()
    fr.alpha_composite(_show[k].resize((270, 480)))
    show_frames.append(fr.convert("RGB"))
show_frames[0].save(f"{GIF}/扫光动效_强度放大示意.gif", save_all=True,
                    append_images=show_frames[1:],
                    duration=int(SWEEP_PERIOD * 1000 / SWEEP_STEPS), loop=0, optimize=True)
print("GIF 扫光示意 ok", flush=True)

# 2c. 三栏对照：原片 / 成片真实强度(7%) / 放大示意(45%)
k = SWEEP_STEPS // 3
_w, _h = 270, 480
cmp = Image.new("RGB", (_w * 3 + 20 * 4, _h + 96), (242, 239, 233))
cd = ImageDraw.Draw(cmp)
cf = font(20)
a = _bg.resize((_w, _h)).convert("RGB")
b = _bg.copy(); b.alpha_composite(SWEEP[k]); b = b.resize((_w, _h)).convert("RGB")
c = _bg.copy(); c.alpha_composite(_show[k]); c = c.resize((_w, _h)).convert("RGB")
for i, (im, label, sub) in enumerate([
        (a, "原片", "未加任何图层"),
        (b, "成片真实强度", "峰值 7%，肉眼几乎看不出"),
        (c, "放大示意", "45%，只为看清光带长什么样")]):
    x = 20 + i * (_w + 20)
    cmp.paste(im, (x, 48))
    cd.text((x, 14), label, font=cf, fill=(60, 50, 40))
    cd.text((x, 48 + _h + 8), sub, font=font(14), fill=(110, 100, 88))
cmp.save(f"{OUT}/扫光_三栏对照.png")
os.remove(f"{OUT}/_sweep_bg.png")

# ============ 3. 渲染 4 段 + 拼接（小画中画已全部删除） ============
A3 = ("scale=540:960,eq=brightness=0.03:contrast=1.03,vignette,"
      "crop=532:944:4:4,scale=540:960,setsar=1,format=yuv420p")
A1 = ("scale=540:960,split=2[bg][fg];"
      "[bg]boxblur=10:1,eq=brightness=-0.08[bg2];"
      "[fg]scale=486:864,drawbox=x=0:y=0:w=486:h=864:color=white@0.30:t=2,setsar=1[fg2];"
      "[bg2][fg2]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2," + A3)
COMMON_V = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
            "-r", "24", "-pix_fmt", "yuv420p"]
COMMON_A = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
TAIL = ["-t", "12", "-shortest"]

def seq_input(si):
    return ["-framerate", str(FPS), "-i", f"{SEQ}/seg{si}/%04d.png"]

def one_seg(si, start, base_filter, tag):
    """统一结构：1 路原片 + 1 路帧序列，overlay 一次。不再有任何小窗。"""
    args = [FF, "-y", "-v", "error", "-ss", str(start), "-t", "12", "-i", SRC] + seq_input(si)
    fc = (f"[0:v]{base_filter}[base];[1:v]format=rgba[ly];"
          "[base][ly]overlay=0:0:shortest=1[v]")
    args += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a"] \
            + COMMON_V + COMMON_A + TAIL + [f"{OUT}/seg{si}.mp4"]
    run(args, tag)

one_seg(1, 60, A3, "段1 特写：A3 + 悬念句 + 扫光")
one_seg(2, 20, A1, "段2 双人：A1+A3 + 人物名片 + 扫光")
one_seg(3, 88, A3, "段3 对峙：A3 + 金句 + 扫光（原「别人辅助窗」已删）")
one_seg(4, 100, A3, "段4 新郎：A3 + 收尾 + 扫光（原「细节放大窗」已删）")

lst = f"{OUT}/concat.txt"
open(lst, "w").write("".join(f"file '{OUT}/seg{i}.mp4'\n" for i in range(1, 5)))
run([FF, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
     "-c", "copy", "-movflags", "+faststart", f"{OUT}/编排演示_v3_48秒.mp4"], "拼接")

# ============ 4. 抽帧总览 ============
from PIL import Image as _I
ts = [1, 4, 7, 15, 18, 22, 29, 33, 41, 45, 47]
for t in ts:
    run([FF, "-y", "-v", "error", "-ss", str(t), "-i", f"{OUT}/编排演示_v3_48秒.mp4",
         "-frames:v", "1", "-update", "1", f"{OUT}/_f{t}.png"], f"抽帧 {t}s")
W, H = 180, 320
sheet = _I.new("RGB", (W * 4 + 15 * 5, H * 3 + 15 * 4 + 26), (242, 239, 233))
sd = ImageDraw.Draw(sheet)
lf = font(18)
for i, t in enumerate(ts):
    im = _I.open(f"{OUT}/_f{t}.png").convert("RGB").resize((W, H))
    x = 15 + (i % 4) * (W + 15); y = 26 + (i // 4) * (H + 15)
    sheet.paste(im, (x, y)); sd.text((x, y - 20), f"{t}s", font=lf, fill=(60, 50, 40))
sheet.save(f"{OUT}/成片抽帧_v3.png")
for p in glob.glob(f"{OUT}/_f*.png"): os.remove(p)

print("DONE v3")
print("扫光：峰值", SWEEP_PEAK, "周期", SWEEP_PERIOD, "s 倾角", SWEEP_ANGLE, "度")
print("观众向文案：", TITLE, "|", HOOK, "|", QUOTE, "|", ENDLINE)
