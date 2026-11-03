#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新建角色卡脚手架：分配 id、建目录、写带生活感默认值的卡骨架，并打印下一步命令。

用法（仓库根）：
  python3 skills/ecommerce-ai-assets/scripts/new_character.py \
      --name 张秀梅 --gender 女 --age 44-48 --role "小学教师" \
      --wardrobe "浅蓝色棉质衬衫，米色直筒裤" --audience 视频号中老年,给妈妈买

默认值全部按"生活感"预置（look 六维、voice.desc 生活化模板），建完只需改 wardrobe 等具体项。
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "assets" / "ai_assets"
CHARS = LIB / "characters"

VOICE_DESC = {
    "女": ("{age}岁上下的中国城市女性，中音，说话像跟熟人唠嗑：语速偏快但不匀，句子之间有自然换气和小停顿，"
           "尾音往下掉、不端着，咬字放松允许连读吞音，离话筒近带轻微气声，情绪是边想边说自己拿主意、"
           "偶尔带一点笑。不要播音腔，不要字正腔圆，不要广告配音的热情上扬"),
    "男": ("{age}岁上下的中国城市男性，中低音，说话松弛：语速中等、常停一下再说，尾音收得随意不拖腔，"
           "咬字带一点松弛的含糊感，离话筒近有轻微气声，语气是心里有数的判断而不是推销，带一点笑意。"
           "不要播音腔，不要字正腔圆，不要广告配音的抑扬顿挫"),
}


def next_id():
    used = [0]
    for p in CHARS.rglob("*"):
        m = re.match(r"ch_(\d+)", p.name)
        if m:
            used.append(int(m.group(1)))
    return f"ch_{max(used) + 1:03d}"


def mid_age(age_feel):
    nums = [int(x) for x in re.findall(r"\d+", age_feel or "")]
    return sum(nums[:2]) // 2 if len(nums) >= 2 else (nums[0] if nums else 45)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--gender", required=True, choices=["女", "男"])
    ap.add_argument("--age", required=True, help="如 44-48")
    ap.add_argument("--role", required=True, help="角色类型，如 小学教师")
    ap.add_argument("--wardrobe", required=True, help="片中要穿的整套衣服")
    ap.add_argument("--audience", default="视频号中老年", help="逗号分隔")
    ap.add_argument("--scene", default="", help="覆盖场景（默认用 home_env 的 place）")
    ap.add_argument("--home-env", default="env_001")
    ap.add_argument("--temperament", default="自然、不端着，有点自己的主意")
    ap.add_argument("--figure", default="普通人的样子，不胖不瘦，不是模特")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    cid = next_id()
    d = CHARS / f"{cid}_{a.name}"
    if d.exists() and not a.force:
        sys.exit(f"目录已存在：{d}（加 --force 覆盖）")
    d.mkdir(parents=True, exist_ok=True)

    card = {
        "id": cid,
        "name": a.name,
        "role_type": a.role,
        "gender": a.gender,
        "age_feel": a.age,
        "region": "城市（华北地级市）",
        "temperament": a.temperament,
        "figure": a.figure,
        "wardrobe": a.wardrobe,
        "look": {
            "action": "正在做一件手边的小事（收拾台面、擦手、整理东西），动作是下意识的，没有摆姿势",
            "wardrobe_wear": "衣服有穿出来的日常褶皱",
            "grooming": "头发没有刻意打理，有一缕没别好",
            "skin": "气色好，皮肤饱满，眼角只有浅浅的细纹，显年轻",
            "expression": "放松的、没在配合拍照的那种",
            "sight": "视线落在手边的东西上",
        },
        "garment_detail": {"garment": "身上那件外套",
                           "fabric": "能看清面料的纹理、缝线和穿出来的褶皱"},
        "home_env": a.home_env,
        "consumption_level": "中高（城市有房，愿为品质买单）",
        "target_audience": [x.strip() for x in a.audience.split(",") if x.strip()],
        "portraits": {"portrait": "portrait.jpg"},
        "portrait_note": "单图双联：左格接近正侧面侧身、不看镜头（喂 Seedance 以此格为准），右格同套衣服领口袖口细节",
        "voice": {
            "platform": "mimo",
            "model": "mimo-v2.5-tts-voicedesign",
            "clone_model": "mimo-v2.5-tts-voiceclone",
            "voice_id": "",
            "desc": VOICE_DESC[a.gender].format(age=mid_age(a.age)),
            "sample": "",
            "sample_note": "sample 即音色锚：正式配音用 voiceclone 传这份样本",
        },
        "used_in": [],
        "status": "draft",
        "created_at": "",
    }
    if a.scene:
        card["scene"] = a.scene

    (d / "card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rel = d.relative_to(ROOT)
    print(f"已建 {cid} {a.name} → {rel}/card.json")
    print("\n下一步（按顺序）：")
    print(f"  1) 出提示词：python3 skills/ecommerce-ai-assets/scripts/build_prompt.py --card {cid}")
    print("     → 复制给 ImageGen（1024x1536，high），图存到本角色目录")
    print(f'  2) 裁水印：ffmpeg -i in.png -vf "crop=iw:ih-60:0:0" -q:v 2 {rel}/portrait.jpg')
    print(f"  3) 造锚样本：python3 skills/ecommerce-ai-assets/scripts/gen_voice.py --card {rel}/card.json -o {rel}/voice_cand_a.mp3 --no-write")
    print("     → 听两版候选，选中的那版 mv 成 voice.mp3")
    print(f"  4) 校验：python3 skills/ecommerce-ai-assets/scripts/registry.py validate")


if __name__ == "__main__":
    main()
