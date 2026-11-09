# 剧集包 schema v1.0（短剧专用）

骨架见 `assets/pack-template.json`。**顶层固定 9 个键**，一个不多一个不少。

> 与商品包的差别：没有 `product_facts`、没有 `selling_point_map`、没有价格/配料/功效字段。
> 多了 `characters`（人物关系）与 `hook_map`（钩子素材）。**两套 schema 不互认**，
> 剧集包里出现商品字段一律判 ERROR —— 防止从旧包抄错结构。

## 顶层

| 键 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `meta` | object | ✅ | 版本、剧名、平台、任务、审核状态 |
| `drama_facts` | array | ✅ | 剧集事实，每条带来源与核实状态 |
| `characters` | array | ✅ | 人物关系，至少 3 人，必须含主角与对立面 |
| `hook_map` | array | ✅ | 高能节点 → 可截断点 → 开场白，至少 6 条 |
| `compliance` | object | ✅ | 五类必查项 + 结局策略 + 旁白禁词 |
| `elements` | object | ✅ | 八库：hooks/pains/scenes/proofs/trust/ctas/golden_quotes/audiences |
| `pending` | array | ✅ | 待核实项，带 blocking |
| `asset_index` | object | ✅ | 集数清单、去字幕状态、大结局标记 |
| `conflicts` | array | ✅ | 多来源冲突（可为空数组，但不能缺） |

## meta

```json
{
  "schema_version": "1.0",
  "kind": "short_drama",
  "drama": "八零憨妻的甜蜜日常",
  "pack_version": "1.0-draft",
  "platform": "kuaishou",
  "task_id": "平台任务 id 或 null",
  "created_at": "2026-11-08",
  "created_by": "WorkBuddy",
  "source_materials": ["本地 20 集全集", "星火任务页截图"],
  "review": { "self_check": "pending", "human_review": "pending", "published_to_tdrive": false },
  "consumers": ["short-drama-script"]
}
```

`kind` 固定 `short_drama`；`platform` 枚举 `kuaishou` / `douyin` / `other`。

## drama_facts[] — 每条 8 字段

| 字段 | 说明 |
|---|---|
| `id` | `df_001` 递增 |
| `field` | 见下方枚举 |
| `label` | 人读字段名 |
| `value` | 值；未知填 `null` |
| `source` | `{ "type": ..., "ref": ... }` |
| `verified` | bool |
| `usable_in_copy` | bool，能不能进口播/字幕 |
| `blocking` | bool，未解决是否卡文案 |
| `placeholder` | 可选，`value: null` 时写 `【集数】` 一类 |

`field` 枚举（可扩展，扩展时 `label` 必须写清）：
`title` / `alias` / `series_length` / `episode_duration` / `genre` / `era` / `setting` /
`logline` / `premise` / `style` / `core_conflict` / `ending_type` / `platform` /
`task_window` / `settlement` / `license_scope` / `sub_format` / `audio` / `finale`

- `logline` 一句话；`premise` 是「观众看片段不知道、解说必须先交代」的前因（如「为救男友摔伤变傻」）。
- `ending_type` **只记类型**（暖收/悬念/开放），**不得写结局内容**。
- `style` 用短标签：`苦情` / `轻喜` / `爽剧反杀` / `悬疑` / `年代亲情`。

`source.type` 枚举（权重从强到弱）：

| type | 含义 | 可标 verified |
|---|---|---|
| `local_material` | 原片实核验（抽帧/听音轨） | ✅ |
| `subtitle_ocr` | 字幕 OCR / ASR 转写 | ✅ |
| `platform_task_page` | 任务详情页实盘字段 | ✅ |
| `platform_list` | 列表页字段（投稿数、标签） | ✅ 但只对应当前时刻 |
| `task_template` | 任务介绍模板文案（【漫剧】之类） | ❌ |
| `external_search` | 站外/全网检索 | ❌ |
| `inferred` | 推断 | ❌ |

> **`task_template` 与 `external_search` 永远不许标 `verified: true`。**
> 已实证：任务介绍写【漫剧】，而本地抽帧是实拍；外部搜索返回同名不同剧。

## characters[] — 每条 7 字段

| 字段 | 说明 |
|---|---|
| `id` | `ch_001` |
| `name` | 剧中称呼 |
| `role` | `protagonist` / `antagonist` / `supporting` / `neighbor` / `family` |
| `relation` | 与主角的关系，一句话 |
| `traits` | 特征数组（≤4 个标签） |
| `evidence` | `{ "ep": 1, "t": 44, "quote": "…" }`，**必填**，证明这个位置 |
| `usable_in_copy` | bool，解说里能不能点名 |

## hook_map[] — 每条 9 字段（本技能核心产出）

| 字段 | 说明 |
|---|---|
| `id` | `hk_001` |
| `beat` | 这一段发生了什么，一句话 |
| `ep` | 集数 |
| `t_start` / `t_end` | 秒；`t_end` 是**可用范围的结束**，不是剧集结束 |
| `quote` | 该节点最有力的一句原台词 |
| `truncate_at` | `{ "ep":..., "t":..., "quote":... }` **必填**，断在这里观众会想问「后来呢」 |
| `opening_lines` | 至少 1 条、建议 3 条，三种不同结构（冲突前置 / 悬念提问 / 反差揭示） |
| `hook_type` | `conflict` / `suspense` / `contrast` / `reveal` |
| `risk` | `low` / `medium` / `high` + 一句话说明 |
| `status` | `ready` / `conditional` / `blocked` |

> `truncate_at` 不是「随便一刀」：必须停在一句完整台词说完之后的位置。
> 断在半句话上，观众会以为卡了；断在结论之后，观众就不点进去了。

## compliance — 五类必查项一个都不能空

```json
{
  "platform": "kuaishou",
  "rules_ref": "references/platform-rules.md",
  "ending_policy": "do_not_use",
  "must_check": {
    "ending_spoiler": { "status": "ok", "note": "第 20 集不用，钩子全部取 1–12 集" },
    "disability_disease_wording": { "status": "watch", "note": "「傻姑娘」可在引用原台词时出现，旁白不得用作描述" },
    "rebuild_evidence": { "status": "ok", "note": "去字幕 + 换配音 + 改文案 + AI 片头" },
    "offsite_diversion": { "status": "ok", "note": "不留微信/公众号/私信" },
    "ai_label": { "status": "pending", "note": "发布页需勾选 AI 生成标识" }
  },
  "banned_in_voiceover": ["治", "疗效", "康复", "精神失常", "大结局", "结局是", "最后他们", "加微信", "公众号", "主页有", "私信", "全网最", "第一", "官方推荐"],
  "quote_risk_terms": ["傻", "残", "病", "死"]
}
```

- `status` 枚举 `ok` / `watch` / `pending` / `blocked`；`blocked` 表示这条不解决不许发。
- `quote_risk_terms` 是「引用原台词时可出现、旁白里不可当描述」的词，**不扫禁，只提示**。

## elements — 八库

| 库 | 短剧语义 |
|---|---|
| `hooks` | 钩子结构库，按 `hook_type` 归档，可跨剧复用 |
| `pains` | 观众痛点（被退婚 / 被看轻 / 被婆家欺 / 娘家不理解） |
| `scenes` | 场景词（灶台、院子、集市、门楼、晒被子） |
| `proofs` | **剧情证据**：这条说法出自第几集哪句，可引用 |
| `trust` | 可信度：授权来源、二创声明、账号定位一致性 |
| `ctas` | 引导点击话术（视频号/快手口吻） |
| `golden_quotes` | 高光台词，**每条必带 `ep` + `t`** |
| `audiences` | 人群，**每条必带 `evidence`** |

`proofs[].level` 固定枚举 `E1`–`E4`（E = episode）：
`E1` 原片直接台词 / `E2` 原片画面可证 / `E3` 剧情逻辑可推 / `E4` 外部信息（默认不可用）。

## asset_index

```json
{
  "mode": "official_task_material",
  "episodes": [
    { "ep": 1, "file": "第1集_无bgm版.mp4", "duration": 132.9, "desub": "pending" }
  ],
  "finale_episodes": [20],
  "audio": "no_bgm",
  "subtitle": "burned_in",
  "splittable": { "usable_ep_range": "1-19", "estimated_clips": 6 }
}
```

- `mode` 枚举：`official_task_material`（任务内素材）/ `self_licensed`（自有授权）/ `no_license`。
- `desub` 枚举：`done` / `pending` / `not_needed`。
- **`finale_episodes` 必须非空**，且这些集不得出现在任何 `hook_map` 的 `ep` 里。

## pending / conflicts

与商品包同构：

- `pending[]`：`id` / `item` / `owner` / `how_to_resolve` / `blocking`（bool，必填）
- `conflicts[]`：`id` / `topic` / `values[]`（每项带 `source`）/ `status`（`open`/`resolved`）/
  `resolution`（**open 时不得出现「选定/采用/就按」这类自动选值表述**）

> **冲突不自动选值**：两个来源打架就挂着，交人定。这条跨所有包类型不变。
