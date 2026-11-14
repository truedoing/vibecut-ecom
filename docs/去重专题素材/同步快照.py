# -*- coding: utf-8 -*-
"""去重专题：把几轮迭代的报告、引用图、脚本、参数表复制成一份入库快照。

背景：.gitignore 里 build/ 和 runs/**/*.mp4 都被排除，报告和成片原件完全不受
版本控制保护，将来清理视频时很容易一起删掉。这个脚本把「值得留的证据」复制
到 docs/去重专题素材/ 里入库；**原件原地不动，一个都不删**。

只增不改不删：目标目录里已有的其他文件不会被清理。重跑会覆盖同名文件，
用于下一轮迭代后刷新快照。

用法：python3 同步快照.py
"""
import os
import re
import json
import shutil
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(ROOT, "runs", "以真心守护至亲", "D3_线一认不认这个妈", "build", "去重预览")

ROUNDS = ["编排版", "编排版_v2", "编排版_v3", "编排版_v4", "全片版"]

# 第一轮没有报告，成果是根目录这几张对照图
FIRST_ROUND_IMAGES = [
    "00_原片.png",
    "01_A1画面包装+A3参数包.png",
    "02_场景挑选_联系图.png",
    "03_A2分屏.png",
    "04_原片_90秒.png",
    "05_A1A3_90秒.png",
    "10_场景扫描_联系图.png",
]

# 未被报告引用、但属于迭代证据的图
EXTRA_IMAGES = {
    "全片版": ["A1包装段复核.png", "小样抽帧对照.png", "文字位置检查.png"],
    "编排版_v4": ["交付前复核.png", "修复复核_放大.png"],
}

SMALL_EXTS = (".py", ".json", ".ass", ".txt", ".md")
SKIP_NAMES = ("concat.txt",)

copied = []


def put(rel_src, rel_dst=None):
    """复制一个文件，rel_src 相对 SRC，rel_dst 相对 HERE。"""
    if rel_dst is None:
        rel_dst = rel_src
    s = os.path.join(SRC, rel_src)
    d = os.path.join(HERE, rel_dst)
    if not os.path.isfile(s):
        return False
    os.makedirs(os.path.dirname(d), exist_ok=True)
    shutil.copy2(s, d)
    copied.append({"path": rel_dst, "bytes": os.path.getsize(d)})
    return True


def report_images(rel_html):
    """读报告里 <img src>，返回它引用的相对路径。"""
    s = os.path.join(SRC, rel_html)
    txt = open(s, encoding="utf-8").read()
    base = os.path.dirname(rel_html)
    out = []
    for r in re.findall(r'<img[^>]+src="([^"]+)"', txt):
        if r.startswith(("http://", "https://", "data:")):
            continue
        out.append(os.path.normpath(os.path.join(base, r)))
    return out


# ---- 1. 五轮：报告 + 报告引用的图 + 脚本 + 小文件 ----
for rd in ROUNDS:
    rd_src = os.path.join(SRC, rd)
    if not os.path.isdir(rd_src):
        continue
    # 报告（含它引用的所有图，保持相对路径，报告才打得开）
    for dp, dn, fn in os.walk(rd_src):
        for f in sorted(fn):
            if not f.endswith(".html"):
                continue
            rel = os.path.relpath(os.path.join(dp, f), SRC)
            put(rel)
            for img in report_images(rel):
                put(img)
    # 生成脚本、参数表、字幕文件、日志（顶层，不钻进帧序列目录）
    for f in sorted(os.listdir(rd_src)):
        p = os.path.join(rd_src, f)
        if not os.path.isfile(p):
            continue
        if f.endswith(SMALL_EXTS) and f not in SKIP_NAMES:
            put(os.path.join(rd, f))
        if f in EXTRA_IMAGES.get(rd, []):
            put(os.path.join(rd, f))
    for f in EXTRA_IMAGES.get(rd, []):
        put(os.path.join(rd, f))
    # 子目录里的小文件（如 GIF预览 已被报告引用，这里补 ass / json）
    for sub in ("图层",):
        sp = os.path.join(rd_src, sub)
        if not os.path.isdir(sp):
            continue
        for f in sorted(os.listdir(sp)):
            if f.endswith((".py", ".json", ".ass", ".png")):
                put(os.path.join(rd, sub, f))

# ---- 2. 第一轮根目录成果图 ----
os.makedirs(os.path.join(HERE, "00_第一轮_A1A3"), exist_ok=True)
for f in FIRST_ROUND_IMAGES:
    put(f, os.path.join("00_第一轮_A1A3", f))

# ---- 3. 本地必留成片清单（不入库，只登记路径与体积）----
local_mp4 = []
for dp, dn, fn in os.walk(SRC):
    for f in sorted(fn):
        if f.lower().endswith(".mp4"):
            p = os.path.join(dp, f)
            local_mp4.append({
                "path": os.path.relpath(p, ROOT),
                "MB": round(os.path.getsize(p) / 1024 / 1024, 2),
                "in_git": False,
            })
local_mp4.sort(key=lambda x: x["path"])

# ---- 4. 防误删标记（写在本地原件目录里，给将来清理视频的人看）----
MARK = """# 请勿删除：去重专题迭代资产

这个目录（含子目录）是「纯剪预告去重 + 画面修饰」这个主题六轮迭代的原件：
各轮报告、演示成片、抽帧对照、生成脚本、参数表全在这里。

它们**不是可由清单再生的临时素材**，是记录「怎么一步步改对」的证据链，
教学与自媒体素材要用，删了就补不回来。

- 入库快照（报告/图/脚本）：`docs/去重专题素材/`
- 完整清单与边界：`docs/去重专题_素材总账.md`
- 2026-11-14 登记。要清理视频前，请先打开总账核对路径。

其它剧、其它故事线生成的视频不在此保护范围，可以另行处理。
"""
mark_targets = [SRC] + [os.path.join(SRC, r) for r in ROUNDS]
for d in mark_targets:
    if os.path.isdir(d):
        with open(os.path.join(d, "请勿删除_去重专题资产.md"), "w", encoding="utf-8") as fh:
            fh.write(MARK)

# ---- 5. 清单数据 ----
total_bytes = sum(x["bytes"] for x in copied)
manifest = {
    "快照时间": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "原件目录": os.path.relpath(SRC, ROOT),
    "入库目录": os.path.relpath(HERE, ROOT),
    "说明": "入库的是报告、报告引用的图、生成脚本、参数表与对照图；成片 mp4 不入库，本地原件原地保留。",
    "入库文件数": len(copied),
    "入库体积MB": round(total_bytes / 1024 / 1024, 2),
    "入库文件": sorted(copied, key=lambda x: x["path"]),
    "本地必留成片": local_mp4,
    "本地必留成片数": len(local_mp4),
    "本地必留成片体积MB": round(sum(x["MB"] for x in local_mp4), 2),
    "防误删标记": [os.path.relpath(os.path.join(d, "请勿删除_去重专题资产.md"), ROOT) for d in mark_targets if os.path.isdir(d)],
}
with open(os.path.join(HERE, "资产清单.json"), "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, ensure_ascii=False, indent=2)

print("入库文件数", len(copied), "体积", round(total_bytes / 1024 / 1024, 2), "MB")
print("本地必留成片", len(local_mp4), "个 /", round(sum(x["MB"] for x in local_mp4), 2), "MB（不入库，原地保留）")
print("防误删标记", len(manifest["防误删标记"]), "处")
print("原件未删除，仅复制")
