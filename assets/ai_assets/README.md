# AI 资产库

电商短视频的**人物 / 音色 / 场景**资产，供 AI 镜头生成使用。
每个角色是一张卡：**一张定妆图 + 一份音色锚 + 一张卡**，三者是一套，不可拆。

## 目录

```
characters/ch_NNN_中文名/
  portrait.jpg    定妆图：单图双联。左格=接近正侧面的人物（喂 Seedance 以此格为准），
                  右格=同套衣服的领口袖口细节（服装款式参考，含面料/褶皱/起球）
  voice.mp3       音色锚样本（不是试听！见下）
  card.json       角色卡：人物设定 + look 六维 + 服装 + 音色描述 + 用过哪些脚本
environments/env_NNN_中文名/
  ref.jpg         场景参考图
  card.json       环境卡：place(场景) / light(光线) / props(生活痕迹) / blank_area(留白区)
```

## 三条使用规则

**1. 编号全局递增，永不复用**
`ch_001`、`env_001` 这样的 id 一旦分配就不再给别的角色用。脚本里只引用 id，中文名只给人看。

**2. voice.mp3 是音色的锚，不是试听样本**
TTS 平台（MiMo）的 `voicedesign` 每次按文字描述现造音色、不返回固定 voice_id，直接用它配音，
每条视频的声音都会漂。所以流程是：

```
card.json 的 voice.desc（文字描述）→ 生成 voice.mp3（19秒锚样本）→ 正式配音时传 voice.mp3 做 voiceclone
```

**弄丢 voice.mp3 = 这个角色的嗓子找不回来了**（只能按 desc 重造，音色会变）。
所以它必须和 card.json 一起走。

**3. 场景与光线必须同源**
角色卡的 `home_env` 指向环境卡，定妆照的背景与光线自动取自环境卡。
人物图和环境图共用一套光线语言，混剪接起来才不跳。个别角色可用 `scene` 覆盖场景。

## card.json 里最要紧的是 look 六维

```json
"look": {
  "action":        "刚忙完一点家务，一只手端着白瓷杯喝了一口…",
  "wardrobe_wear": "衣服有穿出来的日常褶皱、有点松垮",
  "grooming":      "齐肩短发，有一缕没别好落在耳边，几乎看不出化妆",
  "skin":          "气色好，皮肤饱满紧实有光泽，眼角只有浅浅的细纹，显年轻",
  "expression":    "放松的、没在配合拍照的那种",
  "sight":         "视线落在手里端着的白瓷杯上"
}
```

为什么要拆成六项：`action` 管**生活感**，`skin` 管**年龄感**。
混在一起写「皮肤有真实纹理」会把年龄写爆（实测 42 岁出图 50+），
写「皮肤紧致」又变回广告模特。分开写才能各自调、互不干扰。

改卡时**不要**把六项合成一句描述。

## 怎么用

脚本里引用 id，不引用路径：

```json
{"shot_id": "AI-1", "character_ref": "ch_001", "env_ref": "env_001"}
```

要自己出图/配音时，在代码仓库里跑（需要 SKILL 与脚本）：

```bash
python3 skills/ecommerce-ai-assets/scripts/build_prompt.py --card ch_001   # 出提示词
python3 skills/ecommerce-ai-assets/scripts/gen_voice.py --card <卡路径> --clone --text "台词" -o out.mp3
python3 skills/ecommerce-ai-assets/scripts/registry.py validate            # 校验
```

## 维护约定

- **别改别人建的卡**。要调整人设就在卡里加 `revisions` 记录并与作者确认。
- **改人设要归档**：旧卡整目录挪到 `_v1_old/`，别直接覆盖（图上已经换了人，追溯全靠它）。
- **提交前跑 validate**：它会拦下播音腔禁词、look 缺项、图不是竖版、环境写成「现代简约/样板间」、
  缺生活痕迹清单这几类问题。
- **角色有生命周期**：一张脸用 5–8 条就该歇，别同一张脸连卖十个品类。

## 当前库存

| id | 名称 | 性别/年龄感 | 定位 | 音色锚 |
|---|---|---|---|---|
| ch_001 | 林岚 | 女 42-46 | 中学教师（都市白领） | voice.mp3（B 版，13.3s） |
| ch_002 | 郑海涛 | 男 46-50 | 设计院工程师（城市中产） | voice.mp3（A 版，15.3s） |
| env_001 | 城市餐厅晨光 | — | 普通家庭厨房连餐厅，左窗暖白光 | — |
