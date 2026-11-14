# -*- coding: utf-8 -*-
"""
v4：把「文字层」从 Pillow 逐帧画字，换成 ASS 字幕 + libass 渲染（开源短视频字幕的正经做法）。

相对 v3 的改动：
  1) 文字层改 ASS：逐字弹跳 / 滑入旋转 / 关键词变色 / 打字机，四种动效、四种字体。
     不再有整屏卡片、深色底框、三行长台词 —— 去掉 PPT 感。
     三条人物卡 → 三个名字小标签（约 60 字砍到 7 字）。
  2) 扫光收窄：光带宽度 16% → 5%（张老师反馈「太宽」）。
     【已被取代·原口径】「峰值仍锁 7% 不擅自调高，附 12% / 15% 对照帧供选择」。
     【2026-11-14 新口径·张老师拍板】峰值提到 12%，移动速度稍快（周期 6.5s → 5.0s）。
     7% 旧版成片与抽帧已另存「存档_*_7pct*」留作对照，不删。
  3) 扫光实现改成「156 帧周期循环 PNG + overlay」，不再逐帧合成 1152 张全图层。
     文字、角标、进度条全部交给 libass，一次编码完成。

零新装依赖：ffmpeg(含 libass) + numpy + Pillow(只用来量字宽) + 本机已装字体。

红线依旧：不动清单、段序、段长、台词、五幕、原剧字幕；音频不做。
"""
import json, os, re, math, subprocess, sys, glob, shutil
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = "/Users/zgl/vibecut-ecom"
RUN = f"{ROOT}/runs/以真心守护至亲/D3_线一认不认这个妈"
SRC = f"{RUN}/build/v3.mp4"
PACK = f"{ROOT}/packs/short-drama/以真心守护至亲/drama-pack.json"
OUT = f"{RUN}/build/去重预览/编排版_v4"
SEQ = f"{OUT}/扫光周期_12pct_5s"   # 换了周期就换目录：ffmpeg 按序号连续读，旧帧混进来会错相位
GIF = f"{OUT}/GIF预览"
FDIR = f"{OUT}/字体"
os.makedirs(SEQ, exist_ok=True); os.makedirs(GIF, exist_ok=True); os.makedirs(FDIR, exist_ok=True)

FF = "/opt/anaconda3/bin/ffmpeg"
PW, PH = 540, 960
FPS, DUR, NSEG = 24, 12, 4
NFR = FPS * DUR

# ---- 字体（本机已装、可商用；fontsdir 指向干净目录，避开非字体文件报错） ----
SYSF = "/Users/apple/Library/Fonts"
FONT_FILE = {
    "heavy": "SourceHanSansSC-Heavy.otf",
    "oppo":  "OPPOSans-H.ttf",
    "serif": "SourceHanSerifCN-Regular.ttf",
    "bold":  "SourceHanSansCN-Bold.otf",
}
ASS_FONT = {  # libass 认的家族名（已实跑验证）
    "heavy": "Source Han Sans SC Heavy",
    "oppo":  "OPPO Sans H",
    "serif": "Source Han Serif CN",
    "bold":  "Source Han Sans CN Bold",
}
for k, fn in FONT_FILE.items():
    dst = f"{FDIR}/{fn}"
    if not os.path.exists(dst):
        shutil.copy(f"{SYSF}/{fn}", dst)

def pilfont(key, sz):
    return ImageFont.truetype(f"{FDIR}/{FONT_FILE[key]}", sz)

# ---- 扫光参数 ----
SWEEP_PEAK   = 0.12     # 2026-11-14 张老师定：7% → 12%
SWEEP_PERIOD = 5.0      # 2026-11-14 张老师定「稍微快一点」：6.5s → 5.0s（快约 23%）
SWEEP_STEPS  = int(FPS * SWEEP_PERIOD)   # 120 帧一整周期，循环无缝
SWEEP_ANGLE  = 32
SWEEP_W      = 0.05     # 收窄：v3 是 0.16
SWEEP_SHOW   = 0.45

# ---- 配色（ASS 是 &HAABBGGRR，BGR 顺序） ----
GOLD  = (222, 184, 110)
CREAM = (250, 244, 232)
DARK  = (18, 15, 12)
WHITE = (255, 255, 255)

def ac(rgb, a=0):
    r, g, b = rgb
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}&"

def ts(x):
    x = max(0.0, x); m = int(x // 60); s = x - m * 60
    return f"0:{m:02d}:{s:05.2f}"

def ev(start, end, text, layer=0):
    return f"Dialogue: {layer},{ts(start)},{ts(end)},Base,,0,0,0,,{text}"

# ---------- 观众向文案：全部从剧集包自动取，不手写 ----------
pack = json.load(open(PACK))
facts = {x["field"]: x["value"] for x in pack["drama_facts"]}
TITLE = str(facts.get("title", "以真心守护至亲"))
HOOK_FULL = pack["hook_map"][0]["opening_lines"][0]
HOOK = HOOK_FULL.split("，")[-1].rstrip("。")     # 取悬念落点那半句，字少
NAMES = [c["name"] for c in pack["characters"][:3]]
QUOTE = pack["elements"]["golden_quotes"][1]["quote"]
ENDLINE = pack["elements"]["ctas"][0]["text"]

# ---------- ASS 头 ----------
def ass_header():
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PW}
PlayResY: {PH}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,{ASS_FONT['heavy']},48,{ac(CREAM)},&H000000FF&,{ac(DARK)},&H800C0F12&,0,0,0,0,100,100,0,0,1,3,2,5,20,20,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

def rect(x, y, w, h, color, alpha=0):
    return (f"{{\\p1\\an7\\pos({x:.0f},{y:.0f})\\c{ac(color,alpha)}\\bord0\\shad0}}"
            f"m 0 0 l {w:.0f} 0 l {w:.0f} {h:.0f} l 0 {h:.0f}{{\\p0}}")

def common(si):
    """角标 + 底部纯图形五段刻度（不带任何结构词）"""
    o = []
    o.append(ev(0, DUR, rect(22, 30, 5, 40, GOLD, 0x20), layer=0))
    o.append(ev(0, DUR,
        f"{{\\an7\\pos(38,28)\\fn{ASS_FONT['oppo']}\\fs25\\c{ac(CREAM)}\\bord2\\shad1\\alpha&H20&}}{TITLE}",
        layer=1))
    x0, x1, y = 32, PW - 32, PH - 32
    seg = (x1 - x0) / 5
    for i in range(5):
        o.append(ev(0, DUR, rect(x0 + seg * i + 2, y, seg - 4, 4, WHITE, 0xB0), layer=0))
    # 当前段高亮（纯图形，无文字）
    o.append(ev(0, DUR, rect(x0 + seg * si + 2, y - 1, seg - 4, 6, GOLD, 0), layer=1))
    return o

# ---------- 动效 1：逐字弹跳（悬念句） ----------
def fx_pop(lines, text, t0, y, key="heavy", size=56, delay=0.075, tail_gold=3):
    f = pilfont(key, size)
    ws = [f.getlength(ch) for ch in text]
    total = sum(ws)
    x = (PW - total) / 2
    for i, ch in enumerate(text):
        cx = x + sum(ws[:i]) + ws[i] / 2
        st = t0 + i * delay
        gold = i >= len(text) - tail_gold
        sz = size * 1.18 if gold else size
        col = GOLD if gold else CREAM
        tilt = (-7 + (i % 5) * 3)
        tags = (f"\\an5\\pos({cx:.0f},{y:.0f})\\fn{ASS_FONT[key]}\\fs{sz:.0f}"
                f"\\c{ac(col)}\\3c{ac(DARK)}\\bord4\\shad3"
                f"\\alpha&HFF&\\fscx55\\fscy55\\frz{tilt}"
                f"\\t(0,170,\\alpha&H00&\\fscx120\\fscy120\\frz0)"
                f"\\t(170,310,\\fscx94\\fscy94)"
                f"\\t(310,440,\\fscx100\\fscy100)"
                f"\\t(2200,2650,\\alpha&HFF&)")
        lines.append(ev(st, st + 2.7, "{" + tags + "}" + ch, layer=2))

# ---------- 动效 2：名字标签横向依次滑入 + 甩正（替代三条人物卡） ----------
def fx_names(lines, names, t0=2.6, y=350):
    f = pilfont("oppo", 44)
    ws = [f.getlength(n) for n in names]
    gap = 34
    total = sum(ws) + gap * (len(names) - 1)
    x = (PW - total) / 2
    end_all = t0 + 4.2          # 三个标签统一收尾，一起淡出，不会先先后后消失
    for i, n in enumerate(names):
        cx = x + sum(ws[:i]) + gap * i + ws[i] / 2
        st = t0 + i * 0.24
        col = GOLD if i == 0 else CREAM
        fade1 = int((end_all - st) * 1000) - 500
        fade2 = int((end_all - st) * 1000)
        tags = (f"\\an5\\fn{ASS_FONT['oppo']}\\fs44\\c{ac(col)}\\3c{ac(DARK)}\\bord4\\shad3"
                f"\\alpha&HFF&\\frz{-14}\\fscx65\\fscy65"
                f"\\move({cx - 70:.0f},{y + 26:.0f},{cx:.0f},{y:.0f},0,340)"
                f"\\t(0,240,\\alpha&H00&\\fscx115\\fscy115\\frz0)"
                f"\\t(240,400,\\fscx100\\fscy100)"
                f"\\t({fade1},{fade2},\\alpha&HFF&)")
        lines.append(ev(st, end_all, "{" + tags + "}" + n, layer=2))
        # 装饰短线去掉：libass 对绘图块做缩放/变换时会变形，
        # 而且张老师要「文字少一些、不要 PPT 样式」，纯文字动效更干净。

# ---------- 动效 3：整句滑入 + 关键词 inline 变色放大（金句，宋体） ----------
def fx_quote(lines, text, t0=5.4, y=720):
    """整句交给 libass 自己排版（\an5 居中），只用 inline 标签把数字那截放大变金色。
    不手动算每个字的 x —— 上一版手动算宽度导致首字被切、字间出现大空隙。"""
    m = re.search(r"\d+[^，。？！]*[年前天岁]?", text)
    body = text
    if m:
        a, b = m.span()
        body = (text[:a] + "{\\fs62\\c" + ac(GOLD) + "}" + text[a:b]
                + "{\\fs50\\c" + ac(CREAM) + "}" + text[b:])
    tags = (f"\\an5\\fn{ASS_FONT['serif']}\\fs50\\c{ac(CREAM)}\\3c{ac(DARK)}\\bord4\\shad3"
            f"\\alpha&HFF&\\fscx100\\fscy100"
            f"\\move({PW / 2 - 90:.0f},{y + 18:.0f},{PW / 2:.0f},{y:.0f},0,420)"
            f"\\t(0,380,\\alpha&H00&)"
            f"\\t(380,650,\\fscx106\\fscy106)\\t(650,860,\\fscx100\\fscy100)"
            f"\\t(3300,3900,\\alpha&HFF&)")
    lines.append(ev(t0, t0 + 4.0, "{" + tags + "}" + body, layer=2))

# ---------- 动效 4：打字机逐字（收尾句，放上半部避开原剧字幕） ----------
def fx_type(lines, text, t0=8.0, y=235):
    f = pilfont("bold", 44)
    ws = [f.getlength(ch) for ch in text]
    total = sum(ws)
    x = (PW - total) / 2
    for i, ch in enumerate(text):
        cx = x + sum(ws[:i]) + ws[i] / 2
        st = t0 + i * 0.11
        tags = (f"\\an5\\pos({cx:.0f},{y:.0f})\\fn{ASS_FONT['bold']}\\fs44"
                f"\\c{ac(WHITE)}\\3c{ac(DARK)}\\bord4\\shad3"
                f"\\alpha&HFF&\\t(0,90,\\alpha&H00&)"
                f"\\t(2500,3100,\\alpha&HFF&)")
        lines.append(ev(st, st + 3.2, "{" + tags + "}" + ch, layer=2))
    # 收尾金线去掉：缩放作用在绘图块上会把矩形压成竖线（实测 46s 出现白竖条）。
    # 打字机逐字 + 整体淡出已经够，不额外加图形。

# ---------- 生成 4 段 ASS ----------
PLAN = [
    ("seg1", 60, "A3", lambda L: fx_pop(L, HOOK, 2.5, 700)),
    ("seg2", 20, "A1", lambda L: fx_names(L, NAMES)),
    ("seg3", 88, "A3", lambda L: fx_quote(L, QUOTE)),
    ("seg4", 100, "A3", lambda L: fx_type(L, ENDLINE)),
]
for si, (name, src_t, filt, fx) in enumerate(PLAN):
    lines = common(si)
    fx(lines)
    open(f"{OUT}/{name}.ass", "w", encoding="utf-8").write(ass_header() + "\n".join(lines) + "\n")
    print(f"ASS {name} {len(lines)} 条事件", flush=True)

# ---------- 扫光：156 帧周期循环（纯 numpy 生成，不再逐帧合成全图层） ----------
yy, xx = np.mgrid[0:PH, 0:PW].astype(np.float32)
th = math.radians(SWEEP_ANGLE)
nx, ny = -math.sin(th), math.cos(th)
tx, ty = math.cos(th), math.sin(th)
proj = nx * xx + ny * yy
pmin, pmax = float(proj.min()), float(proj.max())
span = pmax - pmin
along = tx * xx + ty * yy
band_w = span * SWEEP_W

def sweep_layer(peak, phase):
    center = pmin - band_w * 2 + (span + band_w * 4) * phase
    wobble = 16.0 * np.sin(along / 150.0 + phase * 2 * math.pi)
    g = np.exp(-((proj + wobble - center) / band_w) ** 2)
    a = (g * peak * 255).astype(np.uint8)
    rgba = np.zeros((PH, PW, 4), np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = 255, 250, 235
    rgba[..., 3] = a
    return Image.fromarray(rgba, "RGBA")

def run(args, tag):
    print("→", tag, flush=True)
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        print("!! 失败", tag, r.stderr[-2500:], flush=True)
        sys.exit(1)

for k in range(SWEEP_STEPS):
    sweep_layer(SWEEP_PEAK, k / SWEEP_STEPS).save(f"{SEQ}/{k:04d}.png")
print(f"扫光周期 {SWEEP_STEPS} 帧（宽 {int(SWEEP_W * 100)}%，峰值 {int(SWEEP_PEAK * 100)}%）", flush=True)

# ---------- 渲染：原片 + 循环扫光 + ASS 文字，一次编码 ----------
A3 = ("scale=540:960,eq=brightness=0.03:contrast=1.03,vignette,"
      "crop=532:944:4:4,scale=540:960,setsar=1,format=yuv420p")
A1 = ("scale=540:960,split=2[bg][fg];"
      "[bg]boxblur=10:1,eq=brightness=-0.08[bg2];"
      "[fg]scale=486:864,drawbox=x=0:y=0:w=486:h=864:color=white@0.30:t=2,setsar=1[fg2];"
      "[bg2][fg2]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2," + A3)
BASE = {"A1": A1, "A3": A3}
COMMON_V = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "24", "-r", "24", "-pix_fmt", "yuv420p"]
COMMON_A = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]

for si, (name, src_t, filt, fx) in enumerate(PLAN):
    args = [FF, "-y", "-v", "error",
            "-ss", str(src_t), "-t", str(DUR), "-i", SRC,
            "-stream_loop", "-1", "-framerate", str(FPS), "-i", f"{SEQ}/%04d.png"]
    fc = (f"[0:v]{BASE[filt]}[base];[1:v]format=rgba[sw];"
          f"[base][sw]overlay=0:0:shortest=1:eval=frame[tmp];"
          f"[tmp]ass={OUT}/{name}.ass:fontsdir={FDIR}[v]")
    args += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a"] \
            + COMMON_V + COMMON_A + ["-t", str(DUR), "-shortest", f"{OUT}/{name}.mp4"]
    run(args, f"段{si+1} {name}：A3/A1 + 收窄扫光 + ASS 文字")

lst = f"{OUT}/concat.txt"
open(lst, "w").write("".join(f"file '{OUT}/{PLAN[i][0]}.mp4'\n" for i in range(NSEG)))
run([FF, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
     "-c", "copy", "-movflags", "+faststart", f"{OUT}/编排演示_v4_48秒.mp4"], "拼接")

# ---------- 抽帧总览 ----------
ts_list = [1, 4, 7, 15, 18, 22, 29, 33, 41, 45, 47]
for t in ts_list:
    run([FF, "-y", "-v", "error", "-ss", str(t), "-i", f"{OUT}/编排演示_v4_48秒.mp4",
         "-frames:v", "1", "-update", "1", f"{OUT}/_f{t}.png"], f"抽帧 {t}s")
W, H = 180, 320
sheet = Image.new("RGB", (W * 4 + 15 * 5, H * 3 + 15 * 4 + 26), (242, 239, 233))
sd = ImageDraw.Draw(sheet)
lf = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 18)
for i, t in enumerate(ts_list):
    im = Image.open(f"{OUT}/_f{t}.png").convert("RGB").resize((W, H))
    x = 15 + (i % 4) * (W + 15); y = 26 + (i // 4) * (H + 15)
    sheet.paste(im, (x, y)); sd.text((x, y - 20), f"{t}s", font=lf, fill=(60, 50, 40))
sheet.save(f"{OUT}/成片抽帧_v4.png")
for p in glob.glob(f"{OUT}/_f*.png"): os.remove(p)

# ---------- 扫光强度对照（7% 旧版 / 12% 本轮成片 / 15% 备选） ----------
run([FF, "-y", "-v", "error", "-ss", "90", "-i", SRC, "-frames:v", "1",
     "-vf", f"scale={PW}:{PH}", "-update", "1", f"{OUT}/_bg.png"], "扫光对照底图")
bg = Image.open(f"{OUT}/_bg.png").convert("RGBA")
W2, H2 = 216, 384
cmp = Image.new("RGB", (W2 * 4 + 16 * 5, H2 + 74), (242, 239, 233))
cd = ImageDraw.Draw(cmp)
cf = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 17)
for i, (label, peak) in enumerate([("原片", 0), ("收窄5% / 7%（旧版）", 0.07),
                                   ("收窄5% / 12%（本轮成片）", 0.12), ("收窄5% / 15%（备选）", 0.15)]):
    im = bg.copy()
    if peak: im.alpha_composite(sweep_layer(peak, 0.38))
    x = 16 + i * (W2 + 16)
    cmp.paste(im.resize((W2, H2)).convert("RGB"), (x, 40))
    cd.text((x, 12), label, font=cf, fill=(60, 50, 40))
cmp.save(f"{OUT}/扫光强度对照.png")
os.remove(f"{OUT}/_bg.png")

# ---------- 动效 GIF：ffmpeg 一次直出，不落任何临时图片 ----------
# （上一版逐帧抽图再一张张删，累计删到 50 个被安全机制拦下，这里改成零中间文件）
GIF_START = [2.4, 2.4, 5.2, 7.9]
for si, (name, src_t, filt, fx) in enumerate(PLAN):
    gst = si * DUR + GIF_START[si]
    fc = ("[0:v]fps=24,scale=216:384:flags=lanczos,split[a][b];"
          "[a]palettegen[p];[b][p]paletteuse[v]")
    run([FF, "-y", "-v", "error", "-ss", f"{gst:.3f}", "-t", "3.6",
         "-i", f"{OUT}/编排演示_v4_48秒.mp4",
         "-filter_complex", fc, "-map", "[v]", "-loop", "0",
         f"{GIF}/{name}_动效.gif"], f"GIF {name}")
    print(f"GIF {name} ok", flush=True)

print("DONE v4")
print("文案：", TITLE, "|", HOOK, "|", NAMES, "|", QUOTE, "|", ENDLINE)
