# 产物规格

两份文件，命名用 `{script_id}` 前缀，默认落当前工作目录。

## 1. `{id}.json` —— 策略层

只写下面这些字段，够用就行，别自造字段：

```json
{
  "script_id": "hjg_T1_001_真香",
  "product": "虎坚果酱",
  "angle_type": "场景型·反常识（真香反转）",
  "speech_rate": 3.8,
  "aspect_ratio": "9:16",
  "hook_type": "冲突型",
  "hook_line": "又买？柜子里那些瓶瓶罐罐还没动呢",
  "selling_point": "咸甜不腻，一勺能抹开",
  "creative_brief": null,
  "segments": [
    {"seq": 1, "section_role": "hook", "speaker": "丈夫", "content": "又买？这回不一样。",
     "visual_hint": "饭桌，丈夫看见购物袋皱眉", "shot_source": "AI", "ai_shot_id": "AI-1"},
    {"seq": 2, "section_role": "proof", "evidence_level": "L1", "speaker": "旁白",
     "content": "每100克有9.2克膳食纤维。", "visual_hint": "标签特写", "shot_source": "SHOOT"}
  ],
  "ai_settings": {"prompts_pending": true, "sound_plan": "双人对白，无旁白"},
  "ai_shots": [
    {"shot_id": "AI-1", "covers": [1], "duration_sec": 15,
     "prompt": "", "negative_prompt": "文字，logo，多余手指"},
    {"shot_id": "AI-2", "covers": [1], "duration_sec": 8,
     "prompt": "", "negative_prompt": "文字，logo"}
  ],
  "pending": ["厂商素材未入库"]
}
```

- `creative_brief` 只在创意钩子时填：人物 / 冲突 / 反转 / 产品道具 / 声音；不填就是 `null`。
- `speaker` 默认「旁白」，混剪段一律「旁白」。多角色对白可放 `dialogue` 数组（仅钩子段），
  `content` 仍填对白合写文本供字数统计。
- `ai_shots` 固定 5 字段，别加。`prompt` 为空＝提示词下一步生成。
- 素材定位：`ref_start` 取素材字幕真实起点，`ref_end = ref_start + 字数 / speech_rate`；
  素材 id 与分层从品类包 `asset_index` 取。素材不足时借用可重叠，在 `note` 写明需慢放几倍。

## 2. `{id}_拍摄执行单.md`

一份给人看的清单，别写成长文：

```
# {id} 拍摄执行单
## 片子的两段式   0–15s 钩子（AI·人物）/ 15–60s 混剪（素材·旁白）
## 卖点三自测     需要感 / 可信度 / 唯一性，各一行
## 口播文案       逐段：### 段N（role） · 结构 · 口播 · 画面方向
## 实拍素材清单   逐段列：素材 id 或 SHOOT，缺什么标「待确认」
## AI 结构        AI-1 / AI-2 覆盖段、秒数、声音方案
## 待确认         blocking 项 + 价格占位符
```

不复述准则正文，不写预算 / 成本 / 方法论，`negative_prompt` 固定值不必抄进执行单。
