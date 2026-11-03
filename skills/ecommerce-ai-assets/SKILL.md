# ecommerce-ai-assets

description: "管理 AI 视频的角色与环境资产：AI 生成单图双联定妆照（侧身+服装细节）、MiMo TTS 音色锚（voicedesign 造音色，voiceclone 固定）、环境参考图。角色卡（人物+语音+服装+人群）是一套，编号全局递增、按需生长、可跨脚本复用。触发词：建角色、定妆图、音色、AI 资产、角色库、环境参考。"

## 核心原则

1. **最小单元是角色卡，不是图也不是音色**。人物长相和声音是同一个身份，必须成套。
2. **按需生长**：脚本需要时先查库，能复用就复用；都不合适才新建。不为凑库先造人。
3. **编号全局递增不复用**（ch_001, ch_002, …），脚本里引用 id，中文名只给人看。
4. **角色有生命周期**：一个角色用 5–8 条就该歇，别同一张脸连卖十个品类。

## 目录与结构

```
assets/ai_assets/
  characters/ch_NNN_中文名/
    portrait.jpg         定妆图：单图双联（左 3/4 侧身半身 + 右服装细节）
    voice.mp3            音色锚样本（voiceclone 的输入，丢了=嗓子丢了）
    card.json            角色卡
  environments/env_NNN_中文名/
    ref.jpg              场景参考
    card.json            环境卡（必须记光线）
```

## 生成定妆图（ImageGen，一张双联就够）

**一张竖版双联图**：左格 3/4 侧身半身像（人物参考），右格同套衣服领口袖口细节（服装参考），
中间白色分隔条，像手机相册里拼好的两张照片。人物消费气质配产品客单价，城市场景。
提示词模板、生成后处理（裁水印 60px）、自检清单见 `references/character-spec.md`。

- 描述路径写「手机相册里拼好的两张照片」，**别写**「三视图」「character sheet」「设定稿」。
- 年龄写具体中值（42岁左右）+「显年轻」，只写区间会生成到上限。
- **不要生成标准三视图**——设定稿式内容容易触发真人内容限制。
- 观感年龄超卡的上限就重生成，不要改卡迁就图。

## 音色（MiMo TTS：voicedesign 造锚 + voiceclone 固定）

voicedesign 每次现造音色、不返回 voice_id，直接配音会漂。**锚 = voice.mp3 样本**：

```bash
# ① 造锚（按 card.json 的 voice.desc 生成 19 秒固定文本，人耳审听）
python3 skills/ecommerce-ai-assets/scripts/gen_voice.py --card assets/ai_assets/characters/ch_001_林岚/card.json
# ② 正式配音（voiceclone 传锚样本，音色一致）
python3 skills/ecommerce-ai-assets/scripts/gen_voice.py --card ... --clone --text "台词" -o out.mp3
```

- `voice.desc`（年龄/音区/语速/劲头）现在就写，换锚、跨平台都靠它重建。
- 换锚后旧台词重配，别新旧混用。clone 的 voice 参数必须 DataURL（脚本已处理）。
- key 在 `.env`：MIMO_API_KEY / MIMO_BASE_URL。

## 环境卡

`card.json` 必须记 **光线方向与色温**（如「左侧窗自然光，暖白」）——
AI 钩子的餐桌和混剪的桌拍素材必须光影一致，接起来才不跳。

## 脚本怎么引用

`ecommerce-script-gen` 的 `ai_shots`：

```json
{"shot_id": "AI-1", "covers": [1], "duration_sec": 15,
 "character_ref": "ch_001", "env_ref": "env_001",
 "prompt": "", "negative_prompt": "..."}
```

存 id 不存路径。写脚本时人物只定功能位（丈夫/妻子），建卡时才定长相；
同一个脚本的两个角色**年龄感气质要搭**（夫妻差不超过 8 岁）。

## 别做

不为凑数先建角色；不用厂商素材里的真人当角色基础（肖像权红线）；
不做视频生成、不写分镜提示词（那是 script-gen 和视频生成的事）。
