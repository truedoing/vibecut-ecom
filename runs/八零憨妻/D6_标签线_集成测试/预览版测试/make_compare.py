#!/usr/bin/env python3
"""把母版和两档预览版同一帧拼成对比图，用于人工判断画质差距。"""
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FF = "/opt/anaconda3/bin/ffmpeg"
FONT = "/Library/Fonts/Arial Unicode.ttf"

SOURCES = [
    ("母版 720p  64MB", HERE.parent / "sd_006_标签线_纯剪_v2.mp4"),
    ("预览档 12MB  480p", HERE / "预览版_crf28.mp4"),
    ("预览档 8.2MB  480p", HERE / "预览版_crf32.mp4"),
]

TS = "85"
FRAMES = []
for i, (label, path) in enumerate(SOURCES):
    out = HERE / f"frame_{i}.png"
    subprocess.run(
        [FF, "-v", "error", "-ss", TS, "-i", str(path), "-frames:v", "1", "-y", str(out)],
        check=True,
    )
    FRAMES.append((label, Image.open(out).convert("RGB")))


def build(tiles, out_name, tile_w, crop=None, zoom=1.0, label_h=46, gap=10):
    """tiles: [(label, image)]，crop=(x,y,w,h) 按原图坐标，zoom 为放大倍数。"""
    prepared = []
    for label, im in tiles:
        if crop:
            x, y, w, h = crop
            if im.width != tile_w * 2 and im.height != tile_w * 2:
                pass
            im = im.crop((x, y, x + w, y + h))
        target_h = int(im.height * tile_w / im.width)
        im = im.resize((tile_w, target_h), Image.LANCZOS)
        if zoom != 1.0:
            im = im.resize((int(im.width * zoom), int(im.height * zoom)), Image.LANCZOS)
        prepared.append((label, im))

    max_h = max(im.height for _, im in prepared)
    total_w = sum(im.width for _, im in prepared) + gap * (len(prepared) - 1)
    canvas = Image.new("RGB", (total_w, max_h + label_h), (24, 24, 26))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(FONT, 26)
    x = 0
    for label, im in prepared:
        canvas.paste(im, (x, 0))
        draw.text((x + 10, max_h + 10), label, font=font, fill=(235, 235, 235))
        x += im.width + gap
    canvas.save(HERE / out_name, quality=95)
    print(f"{out_name}  {canvas.size[0]}x{canvas.size[1]}")


# 全景：母版缩到 480 宽（模拟同样大小的屏幕上看）
wide = []
for label, im in FRAMES:
    wide.append((label, im))
build(wide, "对比图_全景.jpg", tile_w=400)

# 局部放大：裁同一块脸（预览 480 宽下的坐标 140,120,200,200，母版按 1.5 倍换算）
crops = [
    ("母版 720p", FRAMES[0][1].crop((210, 180, 510, 480))),
    ("预览档 12MB", FRAMES[1][1].crop((140, 120, 340, 320))),
    ("预览档 8.2MB", FRAMES[2][1].crop((140, 120, 340, 320))),
]
zoomed = [(label, im.resize((400, 400), Image.LANCZOS)) for label, im in crops]

canvas = Image.new("RGB", (400 * 3 + 20, 400 + 46), (24, 24, 26))
draw = ImageDraw.Draw(canvas)
font = ImageFont.truetype(FONT, 26)
x = 0
for label, im in zoomed:
    canvas.paste(im, (x, 0))
    draw.text((x + 10, 410), label, font=font, fill=(235, 235, 235))
    x += 410
canvas.save(HERE / "对比图_局部放大.jpg", quality=95)
print(f"对比图_局部放大.jpg  {canvas.size[0]}x{canvas.size[1]}")
