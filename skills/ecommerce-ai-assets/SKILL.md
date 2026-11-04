# ecommerce-ai-assets

description: "管理 AI 视频的角色与环境资产：单图双联定妆照（接近正侧面 + 服装细节）、MiMo TTS 音色锚（voicedesign 造音色，voiceclone 固定）、带烟火气的环境参考图。角色卡（人物+语音+服装+人群+look 六维）是一套，编号全局递增、按需生长、可跨脚本复用。触发词：建角色、定妆图、音色、AI 资产、角色库、环境参考。"

## 核心原则

1. **最小单元是角色卡，不是图也不是音色**。长相和声音是同一个身份，必须成套。
2. **按需生长**：脚本需要时先查库，能复用就复用；都不合适才新建。不为凑库先造人。
3. **编号全局递增不复用**（ch_001, ch_002, …），脚本里引用 id，中文名只给人看。
4. **角色有生命周期**：一个角色用 5–8 条就该歇，别同一张脸连卖十个品类。
5. **提示词不手写，由 `build_prompt.py` 从卡里生成**——三轮纠偏得来的规则在脚本里，手写会漏。

## 目录与结构

```
assets/ai_assets/
  characters/ch_NNN_中文名/
    portrait.jpg         定妆图：单图双联（左 3/4 侧身半身 + 右服装细节）
    voice.mp3            音色锚样本（voiceclone 的输入，丢了=嗓子丢了）
    card.json            角色卡（含 look 六维）
    _v1_old/             旧设定归档（改人设时整目录挪进去）
  environments/env_NNN_中文名/
    ref.jpg              场景参考
    card.json            环境卡（place / light / props 必填）
```

## 标准流程（四步，照命令走）

```bash
# ① 建卡（自动分配 id、预置生活感默认值）
python3 skills/ecommerce-ai-assets/scripts/new_character.py \
  --name 张秀梅 --gender 女 --age 44-48 --role "小学教师" \
  --wardrobe "浅蓝棉衬衫，米色直筒裤" --audience "视频号中老年,中年女性"

# ② 出提示词（复制给 ImageGen，1024x1536 / high）
python3 skills/ecommerce-ai-assets/scripts/build_prompt.py --card ch_003

# ③ 裁水印落正式名
ffmpeg -i in.png -vf "crop=iw:ih-60:0:0" -q:v 2 <角色目录>/portrait.jpg

# ④ 造音色锚（抽 5 条自动择优；改 desc 试验用 --desc，都不回写卡）
python3 skills/ecommerce-ai-assets/scripts/gen_voice.py --card <角色目录>/card.json \
  --tries 5 --keep-cands --no-write -o <角色目录>/voice_v2.mp3
python3 skills/ecommerce-ai-assets/scripts/voice_audit.py <角色目录>/voice_v2*.mp3
# 再跑 ASR 咬字验收（含易混词），过了才改名 voice.mp3

# ⑤ 校验（禁词、六维、竖版、锚样本、环境清单）
python3 skills/ecommerce-ai-assets/scripts/registry.py validate
```

## 旁白与句边界已拆出去

| 要做什么 | 去哪个技能 |
|---|---|
| 正式旁白（每片一次、TTS、要达标） | `ecommerce-narration` |
| 句边界、段清单、混剪 | `ecommerce-mix` |

本技能只管**音色锚**（`voice.mp3`）：锚在，嗓子才在。锚丢了无法复现（voicedesign 每次现造音色、不返回 voice_id）。
**锚决定音色与咬字下限，不决定配音起伏**（实测锚 LRA 7.3 的 clone 只有 2.9）。
所以这里要根治的是咬字：desc 去掉「连读吞音 / 含糊感 / 尾音往下掉 / 句间自然换气」，
改写「辅音交代到位、不吞字不含糊 + 音量有明确的强弱对比」；起伏与方差归
`narration.py --energy high --tries N` 自动择优。详见 `character-spec.md`「松弛 ≠ 含糊」节。

**成片级旁白不要在这里手搓。** `gen_voice.py --clone` 只用于短句试听和造锚，配音进 `narration.py`。

短句试听 / 造锚才用 `gen_voice.py --clone --text "一句" --style "..."`。
查库：`registry.py list` / `query --audience 中老年 --gender 女 --kw 教师` / `show ch_001`。

## 生活感三条铁律

图通过与否全在这三条上，细节见 `references/character-spec.md`。

**① 侧身三锁**：身体（接近正侧面、单侧肩线）+ 脸（接近正侧面、单侧五官轮廓）+ 视线（落在手里的东西/望向窗外），
少锁一处，模型就转回来看镜头变成正脸。

**② look 六维分离**：`action`（下意识的小动作）管生活感，`skin`（气色/皮肤状态）管年龄感。
两个维度必须分开写——混在一起写「皮肤有真实纹理」会把年龄拉爆（实测 42 岁写成 50+）。
衣服要有「穿出来的褶皱、有点松垮」，头发「有一缕没别好」，表情「没在配合拍照的那种」。

**③ 反向锁**：「手机随手拍的质感，不是电视剧剧照，不是广告片，不是摆拍」。
环境图另加：「不是样板间，不是效果图，不是房产中介照片」。

## 音色（MiMo TTS：voicedesign 造锚 + voiceclone 固定）

voicedesign 每次现造音色、不返回固定 voice_id，直接配音会漂。**锚 = voice.mp3 样本**。

- **禁用词**：吐字清晰、字正腔圆、抑扬顿挫、亲切热情、从容笃定、播音强调（registry 拦），
  **外加发音缺陷词**：连读吞音 / 含糊感 / 尾音往下掉 / 自然换气和小停顿（造锚时自检告警）。
- ✅ **松弛 ≠ 含糊**：该写「句子连着走、句间不加换气和小停顿」「辅音交代到位、不吞字不含糊」
  「音量有明确的强弱对比」「收尾往前推不往下掉」「真觉得好、急着告诉人」「近场轻微气声」。
- ❌ 别再写「允许连读吞音」「判断而不是推销」「不要热情上扬」——11-03 版这么写，
  造成锚 LRA 1.5、停顿 17.4%、ASR 把「虎坚果」听成「福坚果」。
- `sample` 不是试听，是 voiceclone 的输入；换锚后旧台词要重配。
- **造锚后必须过 `voice_audit.py` + ASR 咬字验收**，声学指标判不了发音变形。
- ⚠️ voicedesign 输出天然平稳（同一新 desc 造 5 条 LRA 全在 1.5–1.9）。要高 LRA 锚，
  从已出片的高起伏配音里裁一段，用 `--ref-audio` 试锚，别在 desc 上死磕。
- 踩坑：clone 的 `audio.voice` 必须 DataURL（`data:audio/mpeg;base64,...`），裸 base64 报 400。
- key 在 `.env`：MIMO_API_KEY / MIMO_BASE_URL。

## 环境卡（样板间是反面）

必填 `place`（「一个住了几年的普通城市家庭厨房连着餐厅」）、`light`、`props`（生活痕迹清单）、
`blank_area`（桌面留白供产品上桌）。`props` 决定有没有烟火气：砧板半头蒜、调料瓶、抹布、冰箱便签。

角色卡用 `home_env` 关联环境卡，定妆照的场景与光线自动取自环境卡——**人物图和环境图必须同一套光线语言**，
混剪才不跳。个别角色可用 `scene` 覆盖场景（如男性在客厅而非厨房）。

## 脚本清单

| 脚本 | 作用 |
|---|---|
| `new_character.py` | 建角色目录 + 卡骨架（预置生活感默认值）+ 打印后续命令 |
| `build_prompt.py` | 从卡生成定妆照/环境图提示词（`--save` 存 card 目录） |
| `gen_voice.py` | 造锚样本（`--tries` 自动择优 / `--desc` 试验 / `--ref-audio` 试锚 / `--no-write`）/ `--clone` 短句试听 |
| `voice_audit.py` | 锚的客观验收：LRA / 停顿 / 响度 / 峰值，出表并判合格（**仍需 ASR 补咬字**） |
| `registry.py` | `list` / `query` / `show` / `validate` / `next-id` |

正式旁白与句边界不在本技能：旁白见 `ecommerce-narration`，混剪见 `ecommerce-mix`。

## 脚本怎么引用

`ecommerce-script-gen` 的 `ai_shots`：

```json
{"shot_id": "AI-1", "covers": [1], "duration_sec": 15,
 "character_ref": "ch_001", "env_ref": "env_001",
 "prompt": "", "negative_prompt": "..."}
```

存 id 不存路径。同一脚本的两个角色**年龄感气质要搭**（夫妻差不超过 8 岁）。

## 别做

不为凑数先建角色；不用厂商素材里的真人当角色基础（肖像权红线）；
不做视频生成、不写分镜提示词（那是 script-gen 和 video-gen 的事）；不出旁白、不算句边界（已拆到 narration / mix）；
不改卡迁就图（观感不对重生成图）。
