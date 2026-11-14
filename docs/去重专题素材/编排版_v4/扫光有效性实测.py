# -*- coding: utf-8 -*-
"""
用本地感知哈希近似平台的「抽帧 → 缩灰度 → DCT 低频 → 64 位指纹」这一层，
实测：扫光宽度、峰值强度到底能翻转多少位。

边界（必须如实说明）：
  - 这是本地 pHash / dHash 复现，不是平台真实模型，也不是平台公开规则；
  - 只回答「画面指纹有没有被动到」，不回答「平台会不会判重」；
  - 汉明距离的参考档位来自公开技术文章 renderio.dev（0-5 同源 / 6-10 改动过 / 11+ 判不同），
    那也不是平台官方阈值，只作量级参照。
"""
import math, os, subprocess
import numpy as np
import cv2
from PIL import Image

ROOT = "/Users/zgl/vibecut-ecom"
RUN = f"{ROOT}/runs/以真心守护至亲/D3_线一认不认这个妈"
SRC = f"{RUN}/build/v3.mp4"
OUT = f"{RUN}/build/去重预览/编排版_v4"
os.makedirs(OUT, exist_ok=True)
FF = "/opt/anaconda3/bin/ffmpeg"
PW, PH = 540, 960
ANGLE = 32

# ---------- 指纹 ----------
def phash(rgb, hs=8, hf=4):
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    s = cv2.resize(g, (hs * hf, hs * hf), interpolation=cv2.INTER_AREA)
    d = cv2.dct(np.float32(s))[:hs, :hs]
    m = np.median(d[1:])
    return (d > m).flatten()

def dhash(rgb, n=8):
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    r = cv2.resize(g, (n + 1, n), interpolation=cv2.INTER_AREA)
    return (r[:, 1:] > r[:, :-1]).flatten()

def ham(a, b):
    return int(np.count_nonzero(a != b))

# ---------- 扫光层 ----------
yy, xx = np.mgrid[0:PH, 0:PW].astype(np.float32)
th = math.radians(ANGLE)
nx, ny = -math.sin(th), math.cos(th)
tx, ty = math.cos(th), math.sin(th)
proj = nx * xx + ny * yy
pmin, pmax = float(proj.min()), float(proj.max())
span = pmax - pmin
along = tx * xx + ty * yy

def sweep_alpha(width_ratio, peak, phase, twin=False):
    bw = span * width_ratio
    center = pmin - bw * 2 + (span + bw * 4) * phase
    wobble = 16.0 * np.sin(along / 150.0 + phase * 2 * math.pi)
    dist = proj + wobble - center
    g = np.exp(-(dist / bw) ** 2)
    if twin:  # 双细带：主带 + 一条更弱、隔开的副带
        g2 = np.exp(-((dist - bw * 2.6) / (bw * 0.7)) ** 2) * 0.55
        g = np.maximum(g, g2)
    return (g * peak).astype(np.float32)

def apply(rgb, a):
    light = np.array([255, 250, 235], np.float32)
    o = rgb.astype(np.float32) * (1 - a[..., None]) + light * a[..., None]
    return np.clip(o, 0, 255).astype(np.uint8)

# ---------- 取 3 个不同场景的原片帧 ----------
frames = []
for t in [30, 90, 150]:
    p = f"{OUT}/_probe_{t}.png"
    subprocess.run([FF, "-y", "-v", "error", "-ss", str(t), "-i", SRC,
                    "-frames:v", "1", "-vf", f"scale={PW}:{PH}",
                    "-update", "1", p], check=True)
    frames.append((t, np.array(Image.open(p).convert("RGB"))))
    os.remove(p)

# ---------- 变体清单：宽度 × 强度 ----------
VARIANTS = [
    ("v3现状 宽16% 峰值7%",      0.16, 0.07, False),
    ("收窄   宽8%  峰值7%",      0.08, 0.07, False),
    ("收窄   宽5%  峰值7%",      0.05, 0.07, False),
    ("收窄   宽5%  峰值4%",      0.05, 0.04, False),
    ("收窄   宽5%  峰值10%",     0.05, 0.10, False),
    ("收窄   宽5%  峰值12%(本轮采用)", 0.05, 0.12, False),
    ("收窄   宽5%  峰值15%",     0.05, 0.15, False),
    ("双细带 宽5%  峰值7%",      0.05, 0.07, True),
    ("双细带 宽5%  峰值10%",     0.05, 0.10, True),
]

print("=" * 78)
print("扫光有效性实测：每格数字 = 加了扫光的帧 相对 原帧 翻转的指纹位数（64 位制）")
print("pHash = 低频指纹（平台那一层的近似）； dHash = 相邻像素梯度指纹（辅助参照）")
print("=" * 78)
print(f"{'变体':<26} {'pHash@相位.3':>13} {'pHash@相位.6':>13} {'dHash@相位.3':>13} {'9相位平均pHash':>16}")
print("-" * 78)

summary = []
for name, w, peak, twin in VARIANTS:
    ps3, ps6, ds3, allps = [], [], [], []
    for t, rgb in frames:
        h0p, h0d = phash(rgb), dhash(rgb)
        a3 = sweep_alpha(w, peak, 0.3, twin)
        a6 = sweep_alpha(w, peak, 0.6, twin)
        ps3.append(ham(h0p, phash(apply(rgb, a3))))
        ps6.append(ham(h0p, phash(apply(rgb, a6))))
        ds3.append(ham(h0d, dhash(apply(rgb, a3))))
        # 取 9 个均匀分布的不同相位（周期长短不影响相位集合，只影响扫过快慢）
        for k in range(9):
            a = sweep_alpha(w, peak, k / 9.0, twin)
            allps.append(ham(h0p, phash(apply(rgb, a))))
    r = (round(np.mean(ps3), 1), round(np.mean(ps6), 1),
         round(np.mean(ds3), 1), round(np.mean(allps), 1))
    summary.append((name, *r))
    print(f"{name:<26} {r[0]:>13} {r[1]:>13} {r[2]:>13} {r[3]:>16}")

print("-" * 78)
print("参照：公开技术文章给的 pHash 汉明距离量级 —— 0~5 极可能同源 / 6~10 可能改过 / 11+ 判不同")
print("      （来源 renderio.dev，不是平台官方阈值；这里是同一帧改前改后的差，不是两条片子的差）")
print()
print("结论要看的两件事：")
print("  1) 位数是不是 > 0 —— 说明这一层指纹确实被动到了；= 0 就是白加")
print("  2) 收窄后位数掉多少 —— 掉太多说明光带细到被 DCT 当高频滤掉了")

# ---------- 出一张「不同宽度看得出多宽」的直观对照 ----------
t, rgb = frames[1]
show = [(label, sweep_alpha(w, peak, 0.38, twin))
        for label, w, peak, twin in [
            ("宽16% (v3)", 0.16, 0.45, False), ("宽8%", 0.08, 0.45, False),
            ("宽5%", 0.05, 0.45, False), ("双细带5%", 0.05, 0.45, True)]]
W, H = 216, 384
sheet = Image.new("RGB", (W * 4 + 16 * 5, H + 44), (242, 239, 233))
from PIL import ImageDraw, ImageFont
f = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 18)
d = ImageDraw.Draw(sheet)
for i, (label, a) in enumerate(show):
    im = Image.fromarray(apply(rgb, a)).resize((W, H))
    x = 16 + i * (W + 16)
    sheet.paste(im, (x, 34))
    d.text((x, 10), label, font=f, fill=(60, 50, 40))
sheet.save(f"{OUT}/宽度对照_放大示意.png")
print("\n已出宽度对照图（强度统一放大到 45% 只为看清形状，不进成片）")

# ---------- 补充一：两条片子不同扫光相位，彼此像不像 ----------
print()
print("=" * 78)
print("补充实测：同一帧原片 + 不同扫光相位，两两之间的指纹距离")
print("（这才贴近真实问题：两条片子用同一场戏，会不会被判成同一帧）")
print("=" * 78)
for label, w, peak, twin in [("v3现状 宽16%/7%", 0.16, 0.07, False),
                             ("收窄 宽5%/7%(旧版)", 0.05, 0.07, False),
                             ("收窄 宽5%/12%(本轮)", 0.05, 0.12, False),
                             ("收窄 宽5%/15%", 0.05, 0.15, False)]:
    pair = []
    for t, rgb in frames:
        hs = [phash(apply(rgb, sweep_alpha(w, peak, k / 8.0, twin))) for k in range(8)]
        for i in range(8):
            for j in range(i + 1, 8):
                pair.append(ham(hs[i], hs[j]))
    print(f"  {label:<20} 两两距离 平均 {np.mean(pair):.2f} 位 / 最大 {max(pair)} 位 / 距离=0 的组合 {pair.count(0)}/{len(pair)}")
print("  读法：距离=0 越多，说明换相位也没把两帧分开，扫光等于白扫。")

# ---------- 补充二：A3 参数包里到底哪一层在动指纹 ----------
print()
print("=" * 78)
print("补充实测：A3 全片参数包各单项，相对原帧翻转多少位")
print("（用来回答「真正干活的是不是扫光」）")
print("=" * 78)

def v_crop(rgb, px=4):
    return cv2.resize(rgb[px:px + 944, px:px + 532], (PW, PH), interpolation=cv2.INTER_LINEAR)

def v_bright(rgb, b=0.03):
    return np.clip(rgb.astype(np.float32) + b * 255, 0, 255).astype(np.uint8)

def v_contrast(rgb, c=1.03):
    return np.clip((rgb.astype(np.float32) - 128) * c + 128, 0, 255).astype(np.uint8)

def v_vignette(rgb):
    cx, cy = PW / 2, PH / 2
    r = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2)
    f = np.clip(1 - 0.22 * r ** 2, 0, 1).astype(np.float32)
    return np.clip(rgb.astype(np.float32) * f[..., None], 0, 255).astype(np.uint8)

def v_a3(rgb):
    return v_vignette(v_contrast(v_bright(v_crop(rgb))))

for label, fn in [("裁切8px再放大", v_crop), ("亮度 +3%", v_bright),
                  ("对比度 1.03", v_contrast), ("暗角", v_vignette),
                  ("A3 全套", v_a3)]:
    ds = [ham(phash(rgb), phash(fn(rgb))) for t, rgb in frames]
    print(f"  {label:<20} pHash 平均翻转 {np.mean(ds):.1f} 位  (三帧分别 {ds})")
print("  读法：A3 那套几何/亮度处理，动指纹的力度比 7% 扫光大一个量级。")

print()
print("DONE")
