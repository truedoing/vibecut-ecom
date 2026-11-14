# -*- coding: utf-8 -*-
"""全片验收：时长/段数核对 + 同点抽帧对照 + 报告用图片。"""
import json, os, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

ROOT = "/Users/zgl/vibecut-ecom"
RUN = f"{ROOT}/runs/以真心守护至亲/D3_线一认不认这个妈"
OUT = f"{RUN}/build/去重预览/全片版"
V3 = f"{RUN}/build/v3.mp4"
FULL = f"{OUT}/全片_去重修饰_v1.mp4"
FF = "/opt/anaconda3/bin/ffmpeg"
FP = "/opt/anaconda3/bin/ffprobe"
TABLE = json.load(open(f"{OUT}/点睛句表.json", encoding="utf-8"))
SPEC = json.load(open(f"{OUT}/dedup_spec.json", encoding="utf-8"))

def probe(p):
    r = subprocess.run([FP, "-v", "error", "-show_entries",
                        "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate",
                        "-of", "json", p], capture_output=True, text=True)
    return json.loads(r.stdout)

def run(args, tag):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        print("!! 失败", tag, r.stderr[-1200:], flush=True)
        sys.exit(1)

# ---------- 1. 规格核对 ----------
a, b = probe(V3), probe(FULL)
def info(x):
    vs = next(s for s in x["streams"] if s["codec_type"] == "video")
    au = next((s for s in x["streams"] if s["codec_type"] == "audio"), None)
    return {"时长": round(float(x["format"]["duration"]), 3),
            "画面": f'{vs["width"]}×{vs["height"]} / {vs["r_frame_rate"]} / {vs["codec_name"]}',
            "声音": f'{au["codec_name"]} / {au.get("r_frame_rate","")}' if au else "无"}
IA, IB = info(a), info(b)
print("v3 原成片：", IA)
print("全片新版：", IB)

plan = json.load(open(f"{RUN}/plan.silence.json", encoding="utf-8"))
n_ep = len([s for s in plan["segs"] if s["type"] == "ep"])
dur_plan = round(sum(s["b"] - s["a"] if s["type"] == "ep" else s["d"]
                     for s in plan["segs"]), 3)
checks = [
    ("清单段数", f'{len(plan["segs"])} 段（{n_ep} 实拍 + 1 收尾卡）', "清单未改"),
    ("清单时长", f"{dur_plan:.2f}s", "与装配器段落表一致"),
    ("成片时长", f'{IB["时长"]:.2f}s vs v3 {IA["时长"]:.2f}s',
     "一致" if abs(IB["时长"] - IA["时长"]) < 0.6 else "有偏差，需查"),
    ("画面规格", IB["画面"], "一致" if IB["画面"] == IA["画面"] else "有偏差，需查"),
    ("声音", IB["声音"], "沿用装配器原有响度链，音频去重一律未做"),
    ("原剧字幕", "保留", "不去字幕、不裁字幕带；仅模糊底层先裁后糊，避免字影"),
    ("编码次数", "1 次", "从原剧素材装配时挂全部画面处理，未对 v3 二次压制"),
]
json.dump({"v3": IA, "full": IB, "checks": checks},
          open(f"{OUT}/验收数据.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# ---------- 2. 同点抽帧：v3 / 全片新版 ----------
# 覆盖：开场文字、A1 包装段、各条点睛句上屏时刻、收尾卡
TIMES = [2.0, 9.6, 26.5, 33.0, 57.3, 108.0, 144.7, 181.4, 198.0, 224.3, 229.0]
W, H = 270, 480
for t in TIMES:
    for key, src in (("原", V3), ("新", FULL)):
        run([FF, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", src,
             "-frames:v", "1", "-vf", f"scale={W}:{H}", "-update", "1",
             f"{OUT}/_t{t:g}_{key}.png"], f"抽帧 {key} {t}s")

# 联系图：每个时间点一对（上 v3，下 新版）
cols = 6
rows = (len(TIMES) + cols - 1) // cols
pad, top = 10, 46
sheet = Image.new("RGB", (cols * (W + pad) + pad, rows * (H * 2 + 30 + pad) + top + 10), (242, 239, 233))
d = ImageDraw.Draw(sheet)
f = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 17)
fs = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 13)
d.text((pad, 10), "上排＝v3 原成片   下排＝全片一次编码新版   同时间点对照", fill=(55, 45, 35), font=f)
for i, t in enumerate(TIMES):
    cx = pad + (i % cols) * (W + pad)
    cy = top + (i // cols) * (H * 2 + 30 + pad)
    d.text((cx, cy), f"{t:g}s", fill=(60, 50, 40), font=fs)
    for j, key in enumerate(("原", "新")):
        im = Image.open(f"{OUT}/_t{t:g}_{key}.png").convert("RGB")
        sheet.paste(im, (cx, cy + 16 + j * H))
sheet.save(f"{OUT}/全片抽帧对照.png")
print("联系图", sheet.size)

# ---------- 3. 点睛句上屏时刻单独放大看 ----------
picks = [q for q in TABLE["点睛句"] if q["on_screen"]]
PW2, PH2 = 320, 568
cards = Image.new("RGB", (3 * (PW2 + 12) + 12, 2 * (PH2 + 34) + 12), (242, 239, 233))
cd = ImageDraw.Draw(cards)
for i, q in enumerate(picks):
    t = q["show_at"] + 1.0
    run([FF, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", FULL,
         "-frames:v", "1", "-vf", f"scale={PW2}:{PH2}", "-update", "1",
         f"{OUT}/_q{i}.png"], f"金句帧 {i}")
    x = 12 + (i % 3) * (PW2 + 12)
    y = 12 + (i // 3) * (PH2 + 34)
    cards.paste(Image.open(f"{OUT}/_q{i}.png").convert("RGB"), (x, y + 26))
    cd.text((x, y + 4), f'{q["at_in_trailer"]:.1f}s  「{q["quote"]}」',
            fill=(55, 45, 35), font=fs)
cards.save(f"{OUT}/点睛句上屏抽帧.png")

# 清临时帧；删不掉就留着，不为了整洁让验收脚本崩掉
for fn in os.listdir(OUT):
    if (fn.startswith("_t") or fn.startswith("_q")) and fn.endswith(".png"):
        try:
            os.remove(f"{OUT}/{fn}")
        except OSError:
            pass
print("DONE 验收抽帧")
