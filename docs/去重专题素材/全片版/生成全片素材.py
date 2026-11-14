# -*- coding: utf-8 -*-
"""
全片一次编码的「素材准备」——只产出参数与素材，不碰清单、不碰成片。

对应两份技术方案：
  · 去重技能：选哪几段套 A1 包装、全局参数包、12% / 5 秒扫光
  · 修饰技能：全片 ASS 文字（四种动效、四种字体，v4 定版样式）

这次相对 48 秒演示片改了两件事：
  1) 文字不再固定取金句库第 2 条，改由「点睛句表」驱动：
     金句按「集号 + 原剧时间点」自动落到清单选中的段里，上屏时刻跟着剧情走。
  2) ASS 时间轴铺满全片 231.73 秒，坐标按 1080×1920 显式展开（v4 是 540×960）。

红线：不动段序、段长、台词、五幕、原剧字幕；音频不做；不为字幕多选一段戏。
"""
import json, os, re, math, shutil
import numpy as np
from PIL import Image, ImageFont

ROOT = "/Users/zgl/vibecut-ecom"
RUN = f"{ROOT}/runs/以真心守护至亲/D3_线一认不认这个妈"
OUT = f"{RUN}/build/去重预览/全片版"
V4 = f"{RUN}/build/去重预览/编排版_v4"
PACK = f"{ROOT}/packs/short-drama/以真心守护至亲/drama-pack.json"
PLAN = f"{RUN}/plan.silence.json"

SEQ = f"{OUT}/扫光帧"
FDIR = f"{OUT}/字体"
os.makedirs(SEQ, exist_ok=True); os.makedirs(FDIR, exist_ok=True)

# ---------- 规格：与装配器 plan 完全一致 ----------
W, H, FPS = 1080, 1920, 30
S = 2                      # v4 定版是 540×960，全片坐标/字号等比 ×2
PW, PH = 540 * S, 960 * S
SWEEP_PEAK, SWEEP_PERIOD, SWEEP_ANGLE, SWEEP_W = 0.12, 5.0, 32, 0.05

FONT_FILE = {
    "heavy": "SourceHanSansSC-Heavy.otf",
    "oppo":  "OPPOSans-H.ttf",
    "serif": "SourceHanSerifCN-Regular.ttf",
    "bold":  "SourceHanSansCN-Bold.otf",
}
ASS_FONT = {
    "heavy": "Source Han Sans SC Heavy",
    "oppo":  "OPPO Sans H",
    "serif": "Source Han Serif CN",
    "bold":  "Source Han Sans CN Bold",
}
for fn in FONT_FILE.values():
    dst = f"{FDIR}/{fn}"
    if not os.path.exists(dst):
        shutil.copy(f"{V4}/字体/{fn}", dst)

def pilfont(key, sz):
    return ImageFont.truetype(f"{FDIR}/{FONT_FILE[key]}", int(sz))

# ---------- 读清单，算成片时间轴 ----------
plan = json.load(open(PLAN, encoding="utf-8"))
pack = json.load(open(PACK, encoding="utf-8"))
t = 0.0
segs = []
for s in plan["segs"]:
    d = s["b"] - s["a"] if s["type"] == "ep" else s["d"]
    segs.append({**s, "t0": round(t, 3), "t1": round(t + d, 3), "dur": round(d, 3)})
    t += d
TOTAL = round(t, 3)

facts = {x["field"]: x["value"] for x in pack["drama_facts"]}
TITLE = str(facts.get("title", "以真心守护至亲"))
HOOK = pack["hook_map"][0]["opening_lines"][0].split("，")[-1].rstrip("。")
NAMES = [c["name"] for c in pack["characters"][:3]]
ENDLINE = pack["elements"]["ctas"][0]["text"]

# ============================================================
# 一、点睛句表：文案阶段该产出的东西（独立文件，不塞进 plan.json）
# ============================================================
quotes_all = pack["elements"]["golden_quotes"]
quote_table = []
for q in quotes_all:
    hit = next((s for s in segs if s["type"] == "ep" and s["ep"] == q["ep"]
                and s["a"] <= q["t"] <= s["b"]), None)
    if hit:
        at = round(hit["t0"] + (q["t"] - hit["a"]), 3)
        quote_table.append({
            "id": q["id"], "ep": q["ep"], "t_in_ep": q["t"],
            "seg": hit["label"], "pos": hit["pos"],
            "seg_start": hit["t0"], "seg_end": hit["t1"],
            "at_in_trailer": at, "quote": q["quote"],
            "matched": True,
        })

# 上屏筛选：同一幕内间隔太近的只留一句；Button 那句留到收尾
# 理由写进表里，方便文案阶段复核，也守住「不为字幕多选段」
DROP = {"gq_003": "与上一条只隔 2.16 秒，同属开场第一拍，叠字会糊成一片"}
picked = [q for q in quote_table if q["id"] not in DROP]
for q in picked:
    q["on_screen"] = True
    # 六种动效都在片子里出现，但分工固定：开场逐字弹跳、人物滑入、金句整句滑入、收尾打字机。
    # 金句统一用一种样式，避免全片每条金句动效乱跳，也符合「v4 样式定版本轮不再改」。
    q["fx"] = "quote"
    # 尽量贴着台词那一刻，但保证 3.2 秒动效完整落在这一段里，不跨段
    q["show_at"] = round(max(q["seg_start"] + 0.4,
                             min(q["at_in_trailer"] - 0.3, q["seg_end"] - 3.4)), 3)
    q["show_dur"] = 3.2
    q["why"] = "按集号+时间点自动落到已选段；该段本来就在清单里，没有为字幕加戏"
for q in quote_table:
    q.setdefault("on_screen", False)
    q.setdefault("why", DROP.get(q["id"], "已选段内"))

table = {
    "_说明": "文案编排阶段应产出的独立产物。只描述哪句该上屏、在第几秒；不进 plan.json，不改剪辑清单。",
    "_来源": {"金句库": "packs/short-drama/以真心守护至亲/drama-pack.json → elements.golden_quotes",
              "清单": "runs/以真心守护至亲/D3_线一认不认这个妈/plan.silence.json"},
    "_对齐规矩": "金句 ep 与段 ep 相同，且金句时间点落在该段 a–b 内，才算命中；命中后再按成片时间轴换算。",
    "_边界": "点睛句只能由选段结果推出，不能反过来影响选段；不为凑一句漂亮字幕去多选一段戏。",
    "统计": {
        "金句库总数": len(quotes_all),
        "自动对齐命中": len(quote_table),
        "命中但未上屏": sum(1 for q in quote_table if not q["on_screen"]),
        "实际选段数": len([s for s in segs if s["type"] == "ep"]),
        "成片总时长": TOTAL,
    },
    "开场悬念句": {"text": HOOK, "at": 0.6, "fx": "pop", "来源": "hook_map[0].opening_lines[0] 落点半句"},
    "人物名标签": {"text": NAMES, "at": 32.0, "fx": "names", "来源": "characters[].name 前三位"},
    "收尾句": {"text": ENDLINE, "at": 228.0, "fx": "type", "来源": "elements.ctas[0].text"},
    "点睛句": quote_table,
    "未命中": [
        {"id": q["id"], "ep": q["ep"], "t": q["t"], "quote": q["quote"],
         "原因": "这条故事线没选到该时间点所在的段，不是对齐算法漏了"}
        for q in quotes_all if q["id"] not in {x["id"] for x in quote_table}
    ],
}
json.dump(table, open(f"{OUT}/点睛句表.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

# ============================================================
# 二、全片 ASS：v4 四种动效定版，坐标/字号按 1080×1920 展开
# ============================================================
GOLD, CREAM, DARK, WHITE = (222, 184, 110), (250, 244, 232), (18, 15, 12), (255, 255, 255)

def ac(rgb, a=0):
    r, g, b = rgb
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}&"

def ts(x):
    x = max(0.0, x); m = int(x // 60); s = x - m * 60
    return f"0:{m:02d}:{s:05.2f}"

def ev(start, end, text, layer=0):
    return f"Dialogue: {layer},{ts(start)},{ts(end)},Base,,0,0,0,,{text}"

def rect(x, y, w, h, color, alpha=0):
    return (f"{{\\p1\\an7\\pos({x:.0f},{y:.0f})\\c{ac(color,alpha)}\\bord0\\shad0}}"
            f"m 0 0 l {w:.0f} 0 l {w:.0f} {h:.0f} l 0 {h:.0f}{{\\p0}}")

def ass_header():
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PW}
PlayResY: {PH}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,{ASS_FONT['heavy']},{48*S},{ac(CREAM)},&H000000FF&,{ac(DARK)},&H800C0F12&,0,0,0,0,100,100,0,0,1,{3*S},{2*S},5,20,20,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

def fx_pop(L, text, t0, y=560*S, key="heavy", size=56*S, delay=0.075, tail_gold=3):
    f = pilfont(key, size)
    ws = [f.getlength(ch) for ch in text]
    total = sum(ws); x = (PW - total) / 2
    for i, ch in enumerate(text):
        cx = x + sum(ws[:i]) + ws[i] / 2
        st = t0 + i * delay
        gold = i >= len(text) - tail_gold
        sz = size * 1.18 if gold else size
        col = GOLD if gold else CREAM
        tilt = -7 + (i % 5) * 3
        tags = (f"\\an5\\pos({cx:.0f},{y:.0f})\\fn{ASS_FONT[key]}\\fs{sz:.0f}"
                f"\\c{ac(col)}\\3c{ac(DARK)}\\bord{4*S}\\shad{3*S}"
                f"\\alpha&HFF&\\fscx55\\fscy55\\frz{tilt}"
                f"\\t(0,170,\\alpha&H00&\\fscx120\\fscy120\\frz0)"
                f"\\t(170,310,\\fscx94\\fscy94)\\t(310,440,\\fscx100\\fscy100)"
                f"\\t(2200,2650,\\alpha&HFF&)")
        L.append(ev(st, st + 2.7, "{" + tags + "}" + ch, layer=2))

def fx_names(L, names, t0, y=350*S):
    size = 44*S
    f = pilfont("oppo", size)
    ws = [f.getlength(n) for n in names]
    gap = 34*S
    total = sum(ws) + gap * (len(names) - 1)
    x = (PW - total) / 2
    end_all = t0 + 4.2
    for i, n in enumerate(names):
        cx = x + sum(ws[:i]) + gap * i + ws[i] / 2
        st = t0 + i * 0.24
        col = GOLD if i == 0 else CREAM
        fade1 = int((end_all - st) * 1000) - 500
        fade2 = int((end_all - st) * 1000)
        tags = (f"\\an5\\fn{ASS_FONT['oppo']}\\fs{size}\\c{ac(col)}\\3c{ac(DARK)}"
                f"\\bord{4*S}\\shad{3*S}\\alpha&HFF&\\frz-14\\fscx65\\fscy65"
                f"\\move({cx-70*S:.0f},{y+26*S:.0f},{cx:.0f},{y:.0f},0,340)"
                f"\\t(0,240,\\alpha&H00&\\fscx115\\fscy115\\frz0)"
                f"\\t(240,400,\\fscx100\\fscy100)"
                f"\\t({fade1},{fade2},\\alpha&HFF&)")
        L.append(ev(st, end_all, "{" + tags + "}" + n, layer=2))

def fx_quote(L, text, t0, y=430*S, dur=3.2):
    # 长句自动缩字号，避免右边界切字（v4 报告里修过一次，这里做成规则）
    base = 50*S
    f = pilfont("serif", base)
    avail = PW - 70*S
    width = f.getlength(text)
    size = base if width <= avail else max(34*S, base * avail / width)
    big = size * 1.24
    m = re.search(r"\d+[^，。？！]*[年前天岁]?", text)
    body = text
    if m:
        a, b = m.span()
        body = text[:a] + "{\\fs" + f"{big:.0f}" + "\\c" + ac(GOLD) + "}" + text[a:b] \
               + "{\\fs" + f"{size:.0f}" + "\\c" + ac(CREAM) + "}" + text[b:]
    fade = int(dur * 1000)
    tags = (f"\\an5\\fn{ASS_FONT['serif']}\\fs{size:.0f}\\c{ac(CREAM)}\\3c{ac(DARK)}"
            f"\\bord{4*S}\\shad{3*S}\\alpha&HFF&\\fscx100\\fscy100"
            f"\\move({PW/2-90*S:.0f},{y+18*S:.0f},{PW/2:.0f},{y:.0f},0,420)"
            f"\\t(0,380,\\alpha&H00&)"
            f"\\t(380,650,\\fscx106\\fscy106)\\t(650,860,\\fscx100\\fscy100)"
            f"\\t({fade-700},{fade-100},\\alpha&HFF&)")
    L.append(ev(t0, t0 + dur, "{" + tags + "}" + body, layer=2))

def fx_type(L, text, t0, y=235*S):
    size = 44*S
    f = pilfont("bold", size)
    ws = [f.getlength(ch) for ch in text]
    total = sum(ws); x = (PW - total) / 2
    for i, ch in enumerate(text):
        cx = x + sum(ws[:i]) + ws[i] / 2
        st = t0 + i * 0.11
        tags = (f"\\an5\\pos({cx:.0f},{y:.0f})\\fn{ASS_FONT['bold']}\\fs{size}"
                f"\\c{ac(WHITE)}\\3c{ac(DARK)}\\bord{4*S}\\shad{3*S}"
                f"\\alpha&HFF&\\t(0,90,\\alpha&H00&)"
                f"\\t(2500,3100,\\alpha&HFF&)")
        L.append(ev(st, st + 3.2, "{" + tags + "}" + ch, layer=2))

# ---- 常驻：角标 + 五段纯图形刻度（不带任何结构词）----
L = []
L.append(ev(0, TOTAL, rect(22*S, 30*S, 5*S, 40*S, GOLD, 0x20), layer=0))
L.append(ev(0, TOTAL,
    f"{{\\an7\\pos({38*S},{28*S})\\fn{ASS_FONT['oppo']}\\fs{25*S}\\c{ac(CREAM)}"
    f"\\bord{2*S}\\shad{1*S}\\alpha&H20&}}{TITLE}", layer=1))
x0, x1, ybar = 32*S, PW - 32*S, PH - 32*S
segw = (x1 - x0) / 5
for i in range(5):
    L.append(ev(0, TOTAL, rect(x0 + segw*i + 2*S, ybar, segw - 4*S, 4*S, WHITE, 0xB0), layer=0))
# 高亮按五幕推进，幕边界由清单 pos 自动算，不写死
ACT = [(1, 4), (5, 8), (9, 12), (13, 18), (19, 23)]
for i, (p0, p1) in enumerate(ACT):
    ss = [s for s in segs if p0 <= s.get("pos", 0) <= p1]
    if not ss:
        continue
    a, b = min(s["t0"] for s in ss), max(s["t1"] for s in ss)
    L.append(ev(a, b, rect(x0 + segw*i + 2*S, ybar - 1*S, segw - 4*S, 6*S, GOLD, 0), layer=1))

# ---- 上屏文字，全部按点睛句表 / 文案数据 ----
fx_pop(L, HOOK, table["开场悬念句"]["at"])
fx_names(L, NAMES, table["人物名标签"]["at"])
for q in [x for x in quote_table if x["on_screen"]]:
    fx_quote(L, q["quote"], q["show_at"], dur=q["show_dur"])
fx_type(L, ENDLINE, table["收尾句"]["at"])

open(f"{OUT}/全片文字.ass", "w", encoding="utf-8").write(ass_header() + "\n".join(L) + "\n")
print(f"ASS：{len(L)} 条事件，覆盖 0–{TOTAL}s")

# ============================================================
# 三、扫光循环帧：540×960 生成，装配器里放大到 1080×1920
# ============================================================
for old in os.listdir(SEQ):
    if old.endswith(".png"):
        os.remove(f"{SEQ}/{old}")
LW, LH = 540, 960
yy, xx = np.mgrid[0:LH, 0:LW].astype(np.float32)
th = math.radians(SWEEP_ANGLE)
nx, ny = -math.sin(th), math.cos(th)
tx, ty = math.cos(th), math.sin(th)
proj = nx * xx + ny * yy
pmin, pmax = float(proj.min()), float(proj.max())
span = pmax - pmin
along = tx * xx + ty * yy
band_w = span * SWEEP_W
STEPS = int(FPS * SWEEP_PERIOD)      # 30fps × 5s = 150 帧，无缝循环
for k in range(STEPS):
    phase = k / STEPS
    center = pmin - band_w * 2 + (span + band_w * 4) * phase
    wobble = 16.0 * np.sin(along / 150.0 + phase * 2 * math.pi)
    g = np.exp(-((proj + wobble - center) / band_w) ** 2)
    a = (g * SWEEP_PEAK * 255).astype(np.uint8)
    rgba = np.zeros((LH, LW, 4), np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = 255, 250, 235
    rgba[..., 3] = a
    Image.fromarray(rgba, "RGBA").save(f"{SEQ}/{k:04d}.png")
print(f"扫光：{STEPS} 帧 / {SWEEP_PERIOD}s 周期 / 峰值 {int(SWEEP_PEAK*100)}% / 宽 {int(SWEEP_W*100)}%")

# ============================================================
# 四、画面参数表 dedup_spec.json
# ============================================================
# A1 只给「静场 / 内心 / 特写」段：收拢视线、突出人物；
# 冲突密集的争吵段保持满屏，不遮信息、不切原剧字幕。
A1_LABELS = ["S04_那道疤", "S07_拿穷样子试他们的心", "S14_我先带妈妈回家",
             "S20_是我今天才看清", "S21_他根本没把这些当回事"]
A1_LABELS = [x for x in A1_LABELS if any(s["label"] == x for s in segs)]

spec = {
    "_说明": "去重技能 + 修饰技能共同产出的画面参数表。装配器只负责挂上去，不做任何剪辑决策。",
    "_版本": "全片一次编码 v0.1 / 2026-11-14",
    "_红线": "不改段序、段长、台词、五幕、原剧字幕；音频不动；不因去重降低剪辑质量。",
    "a1_segments": A1_LABELS,
    "_a1_应用策略": "只挑静场、独白、特写段套画中画包装；冲突密集段保持满屏，避免遮信息、避免全片套框太腻。",
    "a1": {
        "fg_scale": 0.90, "bg_blur": "10:1", "bg_brightness": -0.08,
        "border": "white@0.30", "border_t": 6, "bg_drop_subtitle": True,
        "_注": "底层先裁掉底部字幕带再模糊，模糊背景不留字影；主画面完整保留原剧字幕。",
    },
    "global": {
        "brightness": 0.03, "contrast": 1.03, "vignette": True, "crop": 8,
        "_注": "本地实测：亮度/对比单独对画面指纹 0 位，真正起作用的是裁切(1.3)与暗角(1.7)，合起来 3.7 位。",
    },
    "sweep": {
        "pattern": "扫光帧/%04d.png", "framerate": FPS,
        "peak": SWEEP_PEAK, "period": SWEEP_PERIOD, "width": SWEEP_W, "angle": SWEEP_ANGLE,
        "_注": "张老师 11-14 拍板：峰值 12%、周期 5.0 秒；宽度 5%、倾角 32° 不变。",
    },
    "decorate": {
        "ass": "全片文字.ass", "fontsdir": "字体",
        "table": "点睛句表.json",
        "_注": "文字样式为 v4 定版（四种动效、四种字体、无卡片底框）；文案改由点睛句表驱动，不再按固定序号取金句。",
    },
}
json.dump(spec, open(f"{OUT}/dedup_spec.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

# ============================================================
# 五、小样清单：前两段 + 一段 A1，用来先验滤镜链语法
# ============================================================
small = json.loads(json.dumps(plan, ensure_ascii=False))
keep = ["S01_我是妈妈", "S02_我们没妈", "S04_那道疤"]
small["segs"] = [s for s in plan["segs"] if s["label"] in keep]
json.dump(small, open(f"{OUT}/小样_plan.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

print(f"A1 段 {len(A1_LABELS)} 个：{'、'.join(A1_LABELS)}")
print(f"点睛句：命中 {len(quote_table)} 条，上屏 {sum(1 for q in quote_table if q['on_screen'])} 条")
for q in quote_table:
    print(f"  {'✓' if q['on_screen'] else '—'} {q['at_in_trailer']:7.2f}s  {q['quote']:<14} "
          f"→ {q['seg']}")
print("DONE 素材准备")
