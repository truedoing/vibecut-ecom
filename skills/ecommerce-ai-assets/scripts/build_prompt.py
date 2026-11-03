#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从角色卡/环境卡生成 ImageGen 提示词（定妆照单图双联 / 环境参考图）。

用法（仓库根）：
  python3 skills/ecommerce-ai-assets/scripts/build_prompt.py --card ch_001
  python3 skills/ecommerce-ai-assets/scripts/build_prompt.py --card env_001
  python3 skills/ecommerce-ai-assets/scripts/build_prompt.py --card ch_001 --save

设计要点（都是踩坑换来的，别随便改）：
  - 侧身三锁：身体/脸/视线都要锁，少一处模型就转回来看镜头
  - look 六维分离：action 管生活感、skin 管年龄感，混写会把年龄写爆
  - 年龄算法：取区间下沿再 -2（AI 出图系统性偏老）
  - 反向锁必带：不是电视剧剧照/广告片/摆拍；环境不是样板间/效果图/中介照
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "assets" / "ai_assets"

DEFAULT_LOOK = {
    "action": "正在做一件手边的小事，动作是下意识的，没有摆姿势",
    "wardrobe_wear": "衣服有穿出来的日常褶皱",
    "grooming": "头发没有刻意打理",
    "skin": "气色好，皮肤饱满，眼角只有浅浅的细纹，显年轻",
    "expression": "放松的、没在配合拍照的那种",
    "sight": "视线落在手边的东西上",
}


def find_card(key):
    """按 id 或目录名在 characters / environments 下找 card.json。"""
    for kind in ("characters", "environments"):
        for p in (LIB / kind).iterdir():
            if not p.is_dir():
                continue
            f = p / "card.json"
            if not f.exists():
                continue
            c = json.loads(f.read_text(encoding="utf-8"))
            if c.get("id") == key or p.name == key or p.name.startswith(f"{key}_"):
                return c, f
    sys.exit(f"找不到卡：{key}")


def pick_age(age_feel):
    """AI 出图系统性偏老：取区间下沿再 -2。"""
    nums = [int(x) for x in re.findall(r"\d+", age_feel or "")]
    if len(nums) >= 2:
        return max(20, nums[0] - 2)
    return max(20, nums[0] - 2) if nums else 40


def env_of(card):
    ref = card.get("home_env", "")
    if not ref:
        return {}
    for p in (LIB / "environments").iterdir():
        f = p / "card.json"
        if f.exists():
            c = json.loads(f.read_text(encoding="utf-8"))
            if c.get("id") == ref:
                return c
    return {}


def join_props(props):
    """把生活痕迹列表连成一句自然中文。"""
    items = [re.sub(r"[（(].*?[)）]", "", p) for p in props]
    return "、".join(items)


def clean(text):
    """去掉给人看的括号注释（如"（即定妆照这套，换装等于换人）"），只留画面描述。"""
    return re.sub(r"[（(].*?[)）]", "", text or "").strip()


def gender_word(g):
    return {"女": "女性", "男": "男性"}.get(g, g or "人")


def portrait_prompt(card):
    look = dict(DEFAULT_LOOK)
    look.update({k: v for k, v in (card.get("look") or {}).items() if v})
    gd = card.get("garment_detail") or {}
    env = env_of(card)
    place = card.get("scene") or env.get("place", "自家")   # scene 可按角色覆盖场景
    light = env.get("light", "上午的自然光从画面左侧的窗户照进来，暖白，柔和")
    he_she = "她" if card.get("gender") == "女" else "他"
    gw = gender_word(card.get("gender"))
    garment = gd.get("garment", "身上那件衣服")
    fabric = gd.get("fabric", "能看清面料的纹理和缝线")

    left = (
        f"一位{pick_age(card.get('age_feel'))}岁左右的中国城市{gw}，"
        f"{clean(card.get('role_type'))}，{clean(card.get('figure')) or '普通人的样子，不是模特'}。"
        f"在{place}里，身体是接近正侧面的侧身姿态，肩膀一前一后，只能看到单侧肩线和背部轮廓；"
        f"脸朝向画面左侧，接近正侧面角度，能看到单侧的眉骨、眼睛和鼻梁轮廓，{look['sight']}，"
        f"不看镜头，不是正脸。{look['action']}。"
        f"穿{clean(card.get('wardrobe'))}；{look['wardrobe_wear']}。"
        f"{look['grooming']}，{look['skin']}，表情是{look['expression']}。"
        f"{light}，落在{he_she}的侧脸和肩膀上；背景是这个家的日常细节，略微虚化。"
        f"手机随手拍的质感，不是电视剧剧照，不是广告片，不是摆拍。"
    )
    right = (
        f"同一位{gw}身上{garment}的局部特写，只拍领口、肩线和袖口的位置，"
        f"{fabric}。这一格里没有人脸，只有衣服局部，背景虚化。"
    )
    return (
        "一张竖版双联拼图：左右两格并排，像家人拿手机随手拍下再拼在一起的两张照片，"
        "两格之间一条两指宽的白色分隔条，整幅图无文字、无水印、无装饰边框。\n\n"
        f"左格（约占三分之二宽）：{left}\n\n"
        f"右格（约占三分之一宽）：{right}"
    )


def env_prompt(card):
    neg = card.get("negative") or ["不是样板间", "不是效果图"]
    props = card.get("props") or []
    blank = card.get("blank_area", "")
    body = (
        f"一张竖版室内照片，画面里没有任何人。{card.get('place','')}，"
        f"{card.get('time_feel','上午')}时分。\n\n"
        f"{card.get('light','')}，落在浅色木纹餐桌上。\n\n"
        f"场景里的生活痕迹：{join_props(props)}。\n\n"
        + (f"{blank}。\n\n" if blank else "")
        + f"{'，'.join(neg)}；就是日常的、有点小杂物但干净的家，"
        "真实手机拍摄的室内照片质感，色调暖白，没有人物，无文字水印。"
    )
    return body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", required=True, help="角色/环境卡 id 或目录名，如 ch_001 / env_001")
    ap.add_argument("--save", action="store_true", help="同时存到卡目录下的 prompt.txt")
    a = ap.parse_args()

    card, f = find_card(a.card)
    is_env = "place" in card and "gender" not in card
    text = env_prompt(card) if is_env else portrait_prompt(card)
    print(text)
    if a.save:
        out = f.parent / ("env_prompt.txt" if is_env else "portrait_prompt.txt")
        out.write_text(text + "\n", encoding="utf-8")
        print(f"\n[saved] {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
