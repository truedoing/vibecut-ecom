# -*- coding: utf-8 -*-
"""
验证用脚本（不是产线技能，不进流水线）
作用：
  1) 从 drama-pack.json 自动取数，用 Pillow 渲染 A4 信息图层（中文文字不手写）
  2) 按「挑场景挂」的策略，用 ffmpeg 分段渲染 + 拼接，出一条编排演示片
  3) 顺带出几张前后对照图，供 HTML 报告使用

输入：D3 的 v3 成片 + 剧集包
输出：build/去重预览/编排版/ 下的 PNG、演示 mp4、对照图
"""
import json, os, subprocess, sys

ROOT = "/Users/zgl/vibecut-ecom"
RUN = f"{ROOT}/runs/以真心守护至亲/D3_线一认不认这个妈"
SRC = f"{RUN}/build/v3.mp4"
PACK = f"{ROOT}/packs/short-drama/以真心守护至亲/drama-pack.json"
OUT = f"{RUN}/build/去重预览/编排版"
IMG = f"{OUT}/图层"
os.makedirs(IMG, exist_ok=True)

FF = "/opt/anaconda3/bin/ffmpeg"
FP = "/opt/anaconda3/bin/ffprobe"
FONT = "/System/Library/Fonts/PingFang.ttc"
W, H = 1080, 1920

# ---------- 配色（配合本剧暖棕金调） ----------
INK      = (26, 20, 16)
GOLD     = (217, 178, 106)
CREAM    = (245, 239, 228)
GREY     = (176, 168, 154)
RED      = (180, 72, 60)
PANEL    = (26, 20, 16, 205)

from PIL import Image, ImageDraw, ImageFont, ImageFilter

def font(sz):
    return ImageFont.truetype(FONT, sz)

def wrap(text, f, maxw):
    """按像素宽度折行"""
    lines, cur = [], ""
    for ch in text:
        if f.getlength(cur + ch) <= maxw:
            cur += ch
        else:
            lines.append(cur); cur = ch
    if cur: lines.append(cur)
    return lines

def canvas():
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))

def pane(img, box, alpha=205, radius=28):
    """半透明圆角底板"""
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    d.rounded_rectangle(box, radius=radius, fill=(26, 20, 16, alpha))
    img.alpha_composite(ov)

def text_center(d, y, s, f, fill, cx=W // 2, spacing=0):
    w = d.textlength(s, font=f)
    d.text((cx - w / 2, y), s, font=f, fill=fill)
    return y + f.size + spacing

pack = json.load(open(PACK))
facts = {x["field"]: x["value"] for x in pack["drama_facts"]}
TITLE = str(facts.get("title", "以真心守护至亲"))
chars = pack["characters"]
acts = pack["story_arc"]["acts"]
quotes = pack["elements"]["golden_quotes"]

# ============ A4-1 固定角标（左上，常驻） ============
im = canvas(); d = ImageDraw.Draw(im)
pane(im, (36, 62, 560, 186), alpha=190, radius=22)
d = ImageDraw.Draw(im)
d.rounded_rectangle((36, 62, 46, 186), radius=5, fill=GOLD + (255,))
d.text((72, 78), TITLE, font=font(42), fill=CREAM + (255,))
d.text((72, 136), "剧情解读 · 画面编排验证", font=font(24), fill=GOLD + (255,))
im.save(f"{IMG}/A4-1_角标.png")

# ============ A4-2 五幕进度条（底部，常驻） ============
im = canvas(); d = ImageDraw.Draw(im)
x0, x1, y = 60, 1020, 1856
pane(im, (30, 1770, 1050, 1896), alpha=175, radius=20)
d = ImageDraw.Draw(im)
d.text((60, 1786), "剧情进度", font=font(24), fill=GREY + (255,))
for i, a in enumerate(acts):
    bx0 = x0 + (x1 - x0) * i / len(acts)
    bx1 = x0 + (x1 - x0) * (i + 1) / len(acts) - 8
    d.rounded_rectangle((bx0, 1836, bx1, 1846), radius=5, fill=(120, 110, 96, 255))
    lab = f"{a['no']} {a['name']}"
    fw = font(26)
    d.text((bx0 + (bx1 - bx0) / 2 - d.textlength(lab, font=fw) / 2, 1856),
           lab, font=fw, fill=GOLD + (255,))
im.save(f"{IMG}/A4-2_五幕进度条.png")

# ============ A4-3 人物关系图谱（独立全屏卡，数据来自 characters） ============
im = Image.new("RGBA", (W, H), INK + (242,))
d = ImageDraw.Draw(im)
# 顶部装饰
d.rectangle((0, 0, W, 10), fill=GOLD + (255,))
d.text((72, 96), "人物关系", font=font(64), fill=CREAM + (255,))
d.text((74, 182), TITLE + " · 谁站哪一边", font=font(30), fill=GOLD + (255,))

ROLE_CN = {"protagonist": "主角", "antagonist": "对立面", "supporting": "配角"}
# 中心 = 主角母亲；四周 = 其余四人
center = next(c for c in chars if c["id"] == "ch_001")
others = [c for c in chars if c["id"] != "ch_001"]
POS = [(215, 470), (865, 470), (215, 1230), (865, 1230)]
CX, CY = 540, 850

# 连线先画
for c, (px, py) in zip(others, POS):
    col = RED + (170,) if c["role"] == "antagonist" and c["id"] in ("ch_004", "ch_005") else GOLD + (150,)
    d.line((CX, CY + 60, px, py + 60), fill=col, width=4)

def node(c, cx, cy, main=False):
    r = 82 if main else 62
    col = GOLD if main else (CREAM if c["role"] == "protagonist" else (198, 140, 128))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(52, 42, 33, 255), outline=col + (255,), width=5)
    # 圆内放完整姓名，按字数缩字号，避免「林秀」「周德」这种截断
    nm_in = c["name"]
    sz = {2: 38, 3: 30, 4: 25}.get(len(nm_in), 34)
    if main: sz += 4
    nf = font(sz)
    d.text((cx - d.textlength(nm_in, font=nf) / 2, cy - nf.size / 2 - 4),
           nm_in, font=nf, fill=col + (255,))
    tf = font(34 if main else 30)
    nm = c["name"] + "  " + ROLE_CN.get(c["role"], "")
    d.text((cx - d.textlength(nm, font=tf) / 2, cy + r + 14), nm, font=tf, fill=CREAM + (255,))
    # 一行来自 traits 的短语（自动取，不手写）
    short = " · ".join(c["traits"][:2])
    sf = font(25)
    for k, ln in enumerate(wrap(short, sf, 300)[:2]):
        d.text((cx - d.textlength(ln, font=sf) / 2, cy + r + 58 + k * 32),
               ln, font=sf, fill=GREY + (255,))

for c, (px, py) in zip(others, POS):
    node(c, px, py)
node(center, CX, CY, main=True)
d.text((72, 1660), "母女（金线） / 周家（红线）", font=font(28), fill=GREY + (255,))
d.text((72, 1710), "本图文字取自剧集包 characters 字段，脚本自动生成", font=font(24), fill=(130, 122, 110, 255))
im.convert("RGB").save(f"{IMG}/A4-3_人物关系图谱.png")

# ============ A4-4 章节卡（取第一幕，数据来自 story_arc） ============
a = acts[0]
im = Image.new("RGBA", (W, H), INK + (238,))
d = ImageDraw.Draw(im)
d.rectangle((0, 0, 16, H), fill=GOLD + (255,))
d.text((90, 300), f"第 {a['no']} 幕", font=font(40), fill=GOLD + (255,))
d.text((84, 370), a["name"], font=font(240), fill=CREAM + (255,))
d.text((90, 690), f"第 {a['ep_from']}–{a['ep_to']} 集", font=font(38), fill=GOLD + (255,))
sf = font(34)
y = 830
for ln in wrap(a["summary"], sf, 880)[:5]:
    d.text((90, y), ln, font=sf, fill=(216, 206, 190, 255)); y += 56
d.text((90, 1680), "章节名与集数取自剧集包 story_arc.acts", font=font(24), fill=(130, 122, 110, 255))
im.convert("RGB").save(f"{IMG}/A4-4_章节卡.png")

# ============ A4-5 金句花字条（叠在画面 73–79% 空带，避开字幕） ============
q = quotes[1]  # 可我妈妈17年前就没了
im = canvas()
pane(im, (90, 1390, 990, 1540), alpha=200, radius=26)
d = ImageDraw.Draw(im)
d.rounded_rectangle((90, 1390, 104, 1540), radius=6, fill=GOLD + (255,))
qf = font(58)
d.text((140, 1410), q["quote"], font=qf, fill=CREAM + (255,))
d.text((142, 1488), f"第 {q['ep']} 集 · 原剧台词", font=font(26), fill=GOLD + (255,))
im.save(f"{IMG}/A4-5_金句花字.png")

# ============ A4-6 片尾落版 ============
im = Image.new("RGBA", (W, H), INK + (248,))
d = ImageDraw.Draw(im)
d.rectangle((0, 0, W, 8), fill=GOLD + (255,))
d.text((W // 2 - d.textlength(TITLE, font=font(70)) / 2, 760),
       TITLE, font=font(70), fill=CREAM + (255,))
d.text((W // 2 - d.textlength("画面编排验证版", font=font(40)) / 2, 880),
       "画面编排验证版", font=font(40), fill=GOLD + (255,))
d.text((W // 2 - d.textlength("编排 · 图层 · 统一视觉参数", font=font(28)) / 2, 970),
       "编排 · 图层 · 统一视觉参数", font=font(28), fill=GREY + (255,))
d.text((W // 2 - d.textlength("本片仅用于效果验证，未改动原片叙事", font=font(24)) / 2, 1700),
       "本片仅用于效果验证，未改动原片叙事", font=font(24), fill=(130, 122, 110, 255))
im.convert("RGB").save(f"{IMG}/A4-6_片尾落版.png")

print("A4 图层 6 张已生成")

# ================= 画面滤镜链 =================
# 预览视频用轻量版：分辨率减半、去掉最慢的逐像素噪点、快速编码
# （噪点属 A3 参数包的一员，正式接产线时保留；这里只为快速看编排效果）
A3 = ("scale=540:960,eq=brightness=0.03:contrast=1.03,vignette,"
      "crop=532:944:4:4,scale=540:960,setsar=1,format=yuv420p")
A1 = ("scale=540:960,split=2[bg][fg];"
      "[bg]boxblur=10:1,eq=brightness=-0.08[bg2];"
      "[fg]scale=486:864,drawbox=x=0:y=0:w=486:h=864:color=white@0.30:t=2,setsar=1[fg2];"
      "[bg2][fg2]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2," + A3)
# 静态对照图用全分辨率版（只出单帧，不慢）
A1_FULL = ("split=2[bg][fg];"
      "[bg]scale=1080:1920,boxblur=20:2,eq=brightness=-0.08[bg2];"
      "[fg]scale=972:1728,drawbox=x=0:y=0:w=972:h=1728:color=white@0.30:t=3,setsar=1[fg2];"
      "[bg2][fg2]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2,"
      "eq=brightness=0.03:contrast=1.03,vignette,noise=alls=3:allf=t,"
      "crop=1064:1904:8:8,scale=1080:1920,setsar=1,format=yuv420p")
PY = "scale=540:960,format=rgba"   # 图层统一缩到预览尺寸再叠

BADGE = f"{IMG}/A4-1_角标.png"
PROG  = f"{IMG}/A4-2_五幕进度条.png"
CARD_R= f"{IMG}/A4-3_人物关系图谱.png"
CARD_A= f"{IMG}/A4-4_章节卡.png"
QUOTE = f"{IMG}/A4-5_金句花字.png"
END   = f"{IMG}/A4-6_片尾落版.png"

def run(args, tag):
    print("→", tag, flush=True)
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        print("!! 失败", tag, r.stderr[-2500:], flush=True)
        sys.exit(1)

def loop_png(p, dur=12):
    # 输入侧限时，避免 -loop 1 的图片流无限拖住渲染
    return ["-framerate", "24", "-loop", "1", "-t", str(dur), "-i", p]

COMMON_V = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
            "-r", "24", "-pix_fmt", "yuv420p"]
COMMON_A = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
TAIL = ["-t", "12", "-shortest"]

# 图层缩放链：[i:v] → [lN]
def LY(i):
    return f"[{i}:v]{PY}[l{i}];"

# ---- 段1：单人特写 60–72s → 只挂 A3 + 常驻图层 + 章节卡 ----
args = [FF, "-y", "-v", "error", "-ss", "60", "-t", "12", "-i", SRC]
for p in (BADGE, PROG, CARD_A): args += loop_png(p)
fc = (LY(1) + LY(2) + LY(3) +
      f"[0:v]{A3}[base];"
      "[base][l1]overlay=0:0:shortest=1[o1];"
      "[o1][l2]overlay=0:0:shortest=1[o2];"
      "[o2][l3]overlay=0:0:enable='between(t,3,6)'[v]")
args += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a"] + COMMON_V + COMMON_A + TAIL + [f"{OUT}/seg1.mp4"]
run(args, "段1 特写：A3 + 章节卡")

# ---- 段2：前后景双人 20–32s → A1+A3 + 常驻图层 + 人物关系卡 ----
args = [FF, "-y", "-v", "error", "-ss", "20", "-t", "12", "-i", SRC]
for p in (BADGE, PROG, CARD_R): args += loop_png(p)
fc = (LY(1) + LY(2) + LY(3) +
      f"[0:v]{A1}[base];"
      "[base][l1]overlay=0:0:shortest=1[o1];"
      "[o1][l2]overlay=0:0:shortest=1[o2];"
      "[o2][l3]overlay=0:0:enable='between(t,3,6.5)'[v]")
args += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a"] + COMMON_V + COMMON_A + TAIL + [f"{OUT}/seg2.mp4"]
run(args, "段2 双人：A1+A3 + 人物关系图")

# ---- 段3：新娘+轮椅 88–100s → A3 + 辅助窗「另一个人」(4–9s) + 金句花字 ----
# 辅助窗取 100–105s 的新郎画面，缩成小窗，不压扁主画面
args = [FF, "-y", "-v", "error",
        "-ss", "88", "-t", "12", "-i", SRC,
        "-ss", "100", "-t", "5", "-i", SRC]
for p in (BADGE, PROG, QUOTE): args += loop_png(p)
fc = (LY(2) + LY(3) + LY(4) +
      f"[0:v]{A3}[base];"
      "[1:v]scale=150:260,drawbox=x=0:y=0:w=150:h=260:color=white@0.85:t=3,setsar=1[pip];"
      "[base][pip]overlay=370:575:enable='between(t,4,9)'[o0];"
      "[o0][l2]overlay=0:0:shortest=1[o1];"
      "[o1][l3]overlay=0:0:shortest=1[o2];"
      "[o2][l4]overlay=0:0:enable='between(t,6,12)'[v]")
args += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a"] + COMMON_V + COMMON_A + TAIL + [f"{OUT}/seg3.mp4"]
run(args, "段3 对峙：A3 + 别人辅助窗 + 花字")

# ---- 段4：新郎 100–112s → A3 + 细节放大辅助窗 + 片尾落版 ----
args = [FF, "-y", "-v", "error", "-ss", "100", "-t", "12", "-i", SRC]
for p in (BADGE, PROG, END): args += loop_png(p)
fc = (LY(1) + LY(2) + LY(3) +
      f"[0:v]split=2[m][z];"
      f"[m]{A3}[base];"
      "[z]crop=420:520:330:520,scale=150:260,eq=brightness=0.02,"
      "drawbox=x=0:y=0:w=150:h=260:color=white@0.85:t=3,setsar=1[pip];"
      "[base][pip]overlay=370:575:enable='between(t,3,8)'[o1];"
      "[o1][l1]overlay=0:0:shortest=1[o2];"
      "[o2][l2]overlay=0:0:shortest=1[o3];"
      "[o3][l3]overlay=0:0:enable='between(t,9,12)'[v]")
args += ["-filter_complex", fc, "-map", "[v]", "-map", "0:a"] + COMMON_V + COMMON_A + TAIL + [f"{OUT}/seg4.mp4"]
run(args, "段4 新郎：A3 + 细节放大窗 + 片尾卡")

# ---- 拼接 ----
lst = f"{OUT}/concat.txt"
open(lst, "w").write("".join(f"file '{OUT}/seg{i}.mp4'\n" for i in range(1, 5)))
run([FF, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
     "-c", "copy", "-movflags", "+faststart", f"{OUT}/编排演示_48秒.mp4"], "拼接 4 段")

# ---- 静态对照图：A2 两种改良版（上下分屏：别人 / 细节放大） ----
run([FF, "-y", "-v", "error", "-ss", "90", "-i", SRC, "-ss", "100", "-i", SRC,
     "-filter_complex",
     "[0:v]scale=1080:960,setsar=1[top];"
     "[1:v]scale=1080:960,eq=brightness=-0.04:saturation=0.9,setsar=1[bot];"
     "[top][bot]vstack=inputs=2,scale=1080:1920,format=yuv420p",
     "-frames:v", "1", "-update", "1", f"{OUT}/A2a_上下分屏_辅助是别人.png"], "A2a 静态图")

run([FF, "-y", "-v", "error", "-ss", "90", "-i", SRC,
     "-vf",
     "split=2[top][z];[top]scale=1080:960,setsar=1[t2];"
     "[z]crop=540:960:270:900,scale=1080:960,eq=brightness=0.02:saturation=0.9,setsar=1[b2];"
     "[t2][b2]vstack=inputs=2,scale=1080:1920,format=yuv420p",
     "-frames:v", "1", "-update", "1", f"{OUT}/A2b_上下分屏_辅助是细节放大.png"], "A2b 静态图")

# ---- 前后对照（A3 only / A1+A3） ----
run([FF, "-y", "-v", "error", "-ss", "90", "-i", SRC, "-frames:v", "1", "-update", "1",
     f"{OUT}/对照_00_原片.png"], "原片对照帧")
run([FF, "-y", "-v", "error", "-ss", "90", "-i", SRC, "-vf", A1_FULL,
     "-frames:v", "1", "-update", "1", f"{OUT}/对照_01_A1加A3.png"], "A1+A3 对照帧")

# ---- A4 六张图层总览（中文字体标题，避免方块） ----
import glob
fs = sorted(glob.glob(f"{IMG}/*.png"))
tw, th = 270, 480
sheet = Image.new("RGB", (tw * 3 + 20 * 4, th * 2 + 90), (242, 239, 233))
sd = ImageDraw.Draw(sheet)
lf = font(20)
for i, p in enumerate(fs):
    im2 = Image.open(p).convert("RGB").resize((tw, th))
    x = 20 + (i % 3) * (tw + 20)
    y = 40 + (i // 3) * (th + 40)
    sheet.paste(im2, (x, y))
    sd.text((x, y - 26), os.path.basename(p).replace(".png", ""), font=lf, fill=(60, 50, 40))
sheet.save(f"{OUT}/A4_六张图层_总览.png")

print("DONE")
for f in sorted(os.listdir(OUT)):
    print("  ", f)
