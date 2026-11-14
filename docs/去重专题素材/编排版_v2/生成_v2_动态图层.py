# -*- coding: utf-8 -*-
"""
v2 验证脚本（v1 保留不动，用于对照）
相对 v1 的三处改动：
  1) 去 PPT 风：整屏深色底板 → 局部半透明浮层，原画面继续可见
  2) 静态整页 → 动态浮层（淡入/上滑/逐条出现/淡出），做成 PNG 帧序列叠进视频；
     另行导出 GIF 供甲方在浏览器里直接预览动效
  3) 文字全改成给观众看的：删掉幕号、集数、剧集包说明、验证版字样、
     进度条上的幕名结构词；全部文案从剧集包自动取，一句不手写

产物：编排版_v2/ 下 帧序列、GIF、演示片、抽帧总览
"""
import json, os, subprocess, sys, math, glob

ROOT = "/Users/zgl/vibecut-ecom"
RUN = f"{ROOT}/runs/以真心守护至亲/D3_线一认不认这个妈"
SRC = f"{RUN}/build/v3.mp4"
PACK = f"{ROOT}/packs/short-drama/以真心守护至亲/drama-pack.json"
OUT = f"{RUN}/build/去重预览/编排版_v2"
SEQ = f"{OUT}/帧序列"
GIF = f"{OUT}/GIF预览"
os.makedirs(SEQ, exist_ok=True)
os.makedirs(GIF, exist_ok=True)

FF = "/opt/anaconda3/bin/ffmpeg"
FONT = "/System/Library/Fonts/PingFang.ttc"
PW, PH = 540, 960          # 预览分辨率（与 v1 一致）
FPS, DUR = 24, 12          # 每段 12 秒
NFR = FPS * DUR            # 288 帧/段

from PIL import Image, ImageDraw, ImageFont, ImageFilter

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

# ---------- 观众向文案：全部自动取，不手写 ----------
pack = json.load(open(PACK))
facts = {x["field"]: x["value"] for x in pack["drama_facts"]}
TITLE = str(facts.get("title", "以真心守护至亲"))
HOOK = pack["hook_map"][0]["opening_lines"][0]          # 悬念句
PEOPLE = [(c["name"], c["evidence"]["quote"]) for c in pack["characters"][:3]]
QUOTE = pack["elements"]["golden_quotes"][1]["quote"]    # 金句
ENDLINE = pack["elements"]["ctas"][0]["text"]            # 悬念收尾

# ---------- 配色：暖金 + 米白，克制，不铺满屏 ----------
GOLD = (222, 184, 110)
CREAM = (250, 244, 232)
DARK = (18, 15, 12)

def ease(x):
    """ease-out，动效不生硬"""
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3

def fade(t, t0, tin, hold, tout):
    """卡片整体透明度：淡入 → 停留 → 淡出"""
    if t < t0 or t > t0 + tin + hold + tout: return 0.0
    if t < t0 + tin: return ease((t - t0) / tin)
    if t < t0 + tin + hold: return 1.0
    return 1.0 - ease((t - (t0 + tin + hold)) / tout)

def base_frame():
    return Image.new("RGBA", (PW, PH), (0, 0, 0, 0))

def gradient_scrim(im, y0, y1, maxalpha=165):
    """从下往上的渐变遮罩：压暗一点让字看得清，但画面继续可见"""
    ov = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    h = y1 - y0
    for i in range(h):
        a = int(maxalpha * (i / h) ** 1.6)
        d.line((0, y1 - i, PW, y1 - i), fill=DARK + (a,))
    im.alpha_composite(ov)

def badge(im):
    """常驻角标：只留剧名，去掉所有制作说明"""
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((22, 30, 22 + 6, 78), radius=3, fill=GOLD + (210,))
    f = font(27)
    d.text((40, 30), TITLE, font=f, fill=CREAM + (235,))

def progress(im, ratio):
    """常驻进度条：纯图形五段刻度，无幕名、无标签（观众只需知道进度）"""
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
    """悬念句：左下浮层 + 竖金线，不盖满屏；字逐行浮现"""
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
        # 逐行延迟浮现 + 轻微上滑
        k = ease((alpha - i * 0.12) / 0.55)
        if k <= 0: continue
        yy = y + i * 52 + int(16 * (1 - k))
        d.text((x + 22, yy), ln, font=f,
               fill=CREAM + (int(250 * k),))
    im.alpha_composite(layer)

def people_card(im, t, alpha):
    """人物名片：三条横浮层，逐条从右滑入；名字 + 该角色的一句台词"""
    if alpha <= 0: return
    layer = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    y0 = int(PH * 0.30)
    for i, (name, line) in enumerate(PEOPLE):
        k = ease((alpha - i * 0.16) / 0.60)
        if k <= 0: continue
        y = y0 + i * 132
        dx = int(70 * (1 - k))
        # 局部半透明底，不铺满整屏
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
    """金句：描边大字淡入，无集数标注；字号按文字宽度自动缩，不出画"""
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
    # 描边（深色）保证在亮画面上也读得清
    for ox in (-2, 0, 2):
        for oy in (-2, 0, 2):
            d.text((x + ox, y + oy), QUOTE, font=f, fill=DARK + (int(210 * k),))
    d.text((x, y), QUOTE, font=f, fill=CREAM + (int(255 * k),))
    im.alpha_composite(layer)

def end_card(im, t, alpha):
    """收尾悬念：放在画面上半部，避开 62–66% 的原剧字幕带"""
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
    # 描边，保证亮画面上也清楚
    cx = (PW - w) / 2
    for ox in (-2, 0, 2):
        for oy in (-2, 0, 2):
            d.text((cx + ox, y + oy), ENDLINE, font=f, fill=DARK + (int(210 * k),))
    d.text((cx, y), ENDLINE, font=f, fill=CREAM + (int(255 * k),))
    d.rounded_rectangle(((PW - 90) / 2, y + 72, (PW + 90) / 2, y + 75),
                        radius=2, fill=GOLD + (int(230 * k),))
    im.alpha_composite(layer)

# 每段：卡片种类 + 出现时间（相对段内秒）
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

# ============ 1. 生成 4 段帧序列（含常驻层 + 动态卡） ============
for si, spec in enumerate(PLAN):
    d = f"{SEQ}/{spec['name']}"
    os.makedirs(d, exist_ok=True)
    for n in range(NFR):
        t = n / FPS
        im = base_frame()
        badge(im)
        progress(im, (si + t / DUR) / 4)
        for kind, cfg in spec["cards"]:
            a = fade(t, cfg["t0"], cfg["tin"], cfg["hold"], cfg["tout"])
            CARD_FN[kind](im, t, a)
        im.save(f"{d}/{n:04d}.png")
    print(f"帧序列 {spec['name']} {NFR} 帧", flush=True)

# ============ 2. 导出 GIF 预览（叠在真实画面上，甲方浏览器直接能看） ============
# 取各段卡片出现区间的原片帧做底，把动效帧合上去
SEG_SRC_T = [60, 20, 88, 100]     # 各段在原片里的起点
for si, spec in enumerate(PLAN):
    for kind, cfg in spec["cards"]:
        t0, total = cfg["t0"], cfg["tin"] + cfg["hold"] + cfg["tout"]
        n0, cnt = int((t0 - 0.4) * FPS), int((total + 0.8) * FPS)
        # 底图：该时间点的原片帧（缩到预览尺寸）
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

# ============ 3. 渲染 4 段 + 拼接 ============
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

# 段1：特写，只 A3
args = [FF, "-y", "-v", "error", "-ss", "60", "-t", "12", "-i", SRC] + seq_input(1)
fc = (f"[0:v]{A3}[base];[1:v]format=rgba[ly];"
      "[base][ly]overlay=0:0:shortest=1[v]")
args += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a"] + COMMON_V + COMMON_A + TAIL + [f"{OUT}/seg1.mp4"]
run(args, "段1 悬念句浮层")

# 段2：双人，A1+A3
args = [FF, "-y", "-v", "error", "-ss", "20", "-t", "12", "-i", SRC] + seq_input(2)
fc = (f"[0:v]{A1}[base];[1:v]format=rgba[ly];"
      "[base][ly]overlay=0:0:shortest=1[v]")
args += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a"] + COMMON_V + COMMON_A + TAIL + [f"{OUT}/seg2.mp4"]
run(args, "段2 人物名片逐条浮入")

# 段3：对峙 + 别人辅助窗 + 金句浮入
args = [FF, "-y", "-v", "error",
        "-ss", "88", "-t", "12", "-i", SRC,
        "-ss", "100", "-t", "7", "-i", SRC] + seq_input(3)
fc = (f"[0:v]{A3}[base];"
      "[1:v]scale=150:260,drawbox=x=0:y=0:w=150:h=260:color=white@0.85:t=3,setsar=1[pip];"
      "[base][pip]overlay=370:575:enable='between(t,4,9)':eof_action=pass:repeatlast=0[o1];"
      "[2:v]format=rgba[ly];[o1][ly]overlay=0:0:shortest=1[v]")
args += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a"] + COMMON_V + COMMON_A + TAIL + [f"{OUT}/seg3.mp4"]
run(args, "段3 辅助窗 + 金句")

# 段4：新郎 + 细节放大窗 + 收尾悬念
args = [FF, "-y", "-v", "error", "-ss", "100", "-t", "12", "-i", SRC] + seq_input(4)
fc = (f"[0:v]split=2[m][z];"
      f"[m]{A3}[base];"
      "[z]crop=420:520:330:520,scale=150:260,eq=brightness=0.02,"
      "drawbox=x=0:y=0:w=150:h=260:color=white@0.85:t=3,setsar=1[pip];"
      "[base][pip]overlay=370:575:enable='between(t,3,8)'[o1];"
      "[1:v]format=rgba[ly];[o1][ly]overlay=0:0:shortest=1[v]")
args += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a"] + COMMON_V + COMMON_A + TAIL + [f"{OUT}/seg4.mp4"]
run(args, "段4 细节窗 + 悬念收尾")

lst = f"{OUT}/concat.txt"
open(lst, "w").write("".join(f"file '{OUT}/seg{i}.mp4'\n" for i in range(1, 5)))
run([FF, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
     "-c", "copy", "-movflags", "+faststart", f"{OUT}/编排演示_v2_48秒.mp4"], "拼接")

# ============ 4. 抽帧总览（看动效过渡是否自然） ============
for t in [1, 4, 7, 15, 18, 22, 29, 33, 41, 45, 47]:
    run([FF, "-y", "-v", "error", "-ss", str(t), "-i", f"{OUT}/编排演示_v2_48秒.mp4",
         "-frames:v", "1", "-update", "1", f"{OUT}/_f{t}.png"], f"抽帧 {t}s")
from PIL import Image as _I
ts = [1, 4, 7, 15, 18, 22, 29, 33, 41, 45, 47]
W, H = 180, 320
sheet = _I.new("RGB", (W * 4 + 15 * 5, H * 3 + 15 * 4 + 26), (242, 239, 233))
sd = ImageDraw.Draw(sheet)
lf = font(18)
for i, t in enumerate(ts):
    im = _I.open(f"{OUT}/_f{t}.png").convert("RGB").resize((W, H))
    x = 15 + (i % 4) * (W + 15); y = 26 + (i // 4) * (H + 15)
    sheet.paste(im, (x, y)); sd.text((x, y - 20), f"{t}s", font=lf, fill=(60, 50, 40))
sheet.save(f"{OUT}/成片抽帧_v2.png")
for p in glob.glob(f"{OUT}/_f*.png"): os.remove(p)

print("DONE v2")
print("观众向文案：")
print("  角标 =", TITLE)
print("  悬念 =", HOOK)
print("  人物 =", PEOPLE)
print("  金句 =", QUOTE)
print("  收尾 =", ENDLINE)
