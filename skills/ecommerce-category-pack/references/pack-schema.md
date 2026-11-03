# 品类包 schema v1.0

骨架见 `assets/category-pack-template.json`。**顶层固定 8 个键**：`meta` + 7 块（7 块里已含 `conflicts`）。
不写二次汇总字段，不写角度占用 / 任务状态 / run_id。

> **JSON 是给下一步生成脚本用的，只放「值 + 开关」。**
> 放：`value` / `usable_rewrite` / 七库 / 合规条目 / 素材索引（数据），
> `verified` / `blocking` / `status` / `condition` / `placeholder` / `pending`（下游要读的开关）。
> **不放**解释性文字：为什么算核实、为什么这样改、方法论正文、待裁决理由、数据质量描述。
> 这些一律写进 `品类包说明.md`，脚本一行都不会读。

> **包与包之间孤立隔离**：`category-pack.json` 与 `品类包说明.md` 不得出现另一品类的
> 名字、数据或流程对照。通用性验证、流程演进写技能文档，不写进包。
> **说明.md 只渲染本包数据结论**，不放决策过程、理由、验证结论或方法论正文。

## 顶层

| 键 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `meta` | object | ✅ | 版本、品类、来源、审核状态 |
| `product_facts` | array | ✅ | 产品事实，每条带来源与核实状态 |
| `selling_point_map` | array | ✅ | 卖点话术转换表（不是禁用清单） |
| `compliance_ext` | object | ✅ | 品类专属合规扩展 |
| `elements` | object | ✅ | 七库 + 价格话术库 |
| `pending` | array | ✅ | 待核实项，带 blocking |
| `asset_index` | object | ✅ | 素材模式 / 四层 / 硬约束 / 索引 |
| `conflicts` | array | ✅ | 多来源冲突清单（可为空数组，但不能缺） |

## meta

```json
{
  "schema_version": "1.0",
  "category": "羽衣甘蓝",
  "pack_version": "1.0-draft",
  "created_at": "2026-11-03",
  "created_by": "WorkBuddy",
  "source_materials": ["..."],
  "review": { "self_check": "passed", "human_review": "pending", "published_to_tdrive": false },
  "consumers": ["ecommerce-script-gen", "vibecut UI"],
  "notes": "..."
}
```

`category` **必须与 `vibecut project list` 项目名完全一致**。`published_to_tdrive`
默认 `false`；上传后补路径与日期。

## product_facts[] — 每条 8 字段

| 字段 | 说明 |
|---|---|
| `id` | `pf_001` 递增 |
| `field` | 见下方枚举 |
| `label` | 人读字段名 |
| `value` | 值；未知填 `null` |
| `source` | `{ "type": ..., "ref": ... }`，type 见材料分级 |
| `verified` | bool，**厂商卖点图不算核实** |
| `usable_in_copy` | bool，能不能进口播 |
| `blocking` | bool，未解决是否卡配音 |
| `placeholder` | 可选，`value: null` 时写 `【规格】` 一类 |

`field` 建议值（**可扩展**：不同品类会有自己的字段，如 `brand` / `manufacturer` /
`claims_zero_add`；扩展时 `label` 必须写清是什么，且不得与已有字段重复）：
`product_name` / `brand` / `product_type` / `ingredients` / `nutrition` / `net_weight` /
`price` / `origin` / `process` / `shelf_life` / `storage` / `certification` / `allergen` /
`usage` / `manufacturer` / `trust`。

`source.type` 枚举（权重从强到弱）：
`official_label` / `product_page` / `lab_report` / `vendor_claim` / `detail_page` /
`reference_video` / `script_design` / `project_config` / `unknown` / `inferred`。
只有前三类可直接 `verified: true`；`script_design` 与 `project_config` 属**自用素材/内部命名**
（例如「厂商吃法清单」被当作场景设计素材、或内部项目品类名），可标 true，
但 `verified_note` 必须写明「这不是厂商事实断言」，否则校验器会 WARN。

## selling_point_map[] — 每条 7 字段

| 字段 | 说明 |
|---|---|
| `id` | `sp_001` |
| `vendor_claim` | 厂商/竞品原话，逐字 |
| `risk_type` | 身体功效结论 / 比较宣称 / 健康暗示 / 无依据换算 / 绝对化 / 疾病功效 / 无认证宣称 / 虚假价格稀缺 / 人群口径不符 |
| `usable_rewrite` | **可用说法**；默认 = `vendor_claim` 逐字保留。转换须同一语义内核，禁止换成不相干卖点（如「饱腹感强」→「一勺能抹满一片面包」）；仅四类红线可为 `null` |
| `condition` | 使用前提（待核项、价格落地等） |
| `status` | `ready` / `conditional` / `blocked` |

## compliance_ext

```json
{
  "category_specific": [
    { "term": "...", "reason": "...", "action": "forbid|conditional|forbid_until_verified", "scope": "all|<人群>" }
  ],
  "shared_overlay_ref": "技能自带 references/platform-audience.md",
  "base_redline_ref": "技能自带 references/compliance-base.md",
  "note": "..."
}
```

**只放品类专属**。人群红线与通用四类红线写 ref，不复制正文。

## elements

七库固定键：`hooks` / `pains` / `scenes` / `proofs` / `trust` / `ctas` / `golden_quotes`，
外加 `price_talk` 与可选 `note`。每条带 `source` 与 `status`。
`proofs` 用 `level: L1|L2|L3|L4`；`trust` 用 `level: T1|T2|T3`。
`price_talk` 全部标明「竞品口径，仅供结构参考，数字不得编造」。

## pending[]

| 字段 | 说明 |
|---|---|
| `id` / `item` / `why` | 标识与原因 |
| `blocking` | **bool，必填** |
| `owner` | 厂商 / 运营 / 拍摄 |
| `how_to_resolve` | 具体怎么拿到 |

## asset_index

必填 `mode`（`vendor_provided` / `legacy_competitor_clips`）、`layers`（A–D 四层，
B 层标 `hard_constraint: true`）、`hard_constraints`、`consistency_anchors`、`vendor_assets`。
可选 `measured_finding` / `semantic_index`。**只放 id 与索引，不放媒体本体。**

## conflicts[]

**JSON 里固定 `[]`**（键不能缺，值必须空）。冲突与待裁决属于**人审**，不进机读源：

- 有明确优先来源时直接取值，不列冲突（正式标签 > 厂商卖点图 > 竞品台词）；
- 真需要人裁决的（厂商话术与标签打架、价格档位口径、定位口径），
  一行一条写进 `品类包说明.md` 的「人审待裁决」节。

理由：下游脚本读的是「这个值能不能用」（由 `verified` / `blocking` / `placeholder` / `pending` 表达），
不是「哪几个值在打架」。

## 校验器检查项（`scripts/validate_pack.py`）

1. `schema_version` 合法、顶层 8 键齐备
2. `meta.category` 非空
3. `product_facts` 每条有 `source` / `verified` / `usable_in_copy` / `blocking`（`verified_note` 已移除）
4. `value: null` 的事实必须有 `placeholder`，且该占位符在 `pending` 有对应项
5. `selling_point_map` 每条有 `usable_rewrite` 与 `condition`（`basis` 已移除）
6. `compliance_ext.category_specific` 非空
7. `elements` 七库齐全、`proofs`/`trust` 的 level 合法
8. `pending` 每条 `blocking` 是布尔
9. `conflicts` 中 `status: open` 的必须 ≥2 个 values 且 `resolution` 不含"选定/采用"
10. 兜底禁词扫描（只扫 `usable_rewrite` 与 `elements` 的 example/wording/quote）
11. `published_to_tdrive: true` 但 `human_review != passed` → 报错
12. 体积 sanity check（防产物膨胀）
13. 机读源纯净度：`verified_note` / `basis` / `notes` / `note` / `rule` / `mode_note` 出现 → WARN
14. `conflicts` 非空 → WARN（清单应放说明.md）
