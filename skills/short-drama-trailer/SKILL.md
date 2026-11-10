---
name: short-drama-trailer
description: 纯剪预告片装配技能：消费 short-drama-script 产出的 plan.json，用 mix_trailer.py 校验并装配成片，做段界与切点验证，支持王婶孪生 AI 织入位整段插入。当用户要「装配 / 剪一条预告 / 重出成片」时使用。只管装配与验证，不做选段与五幕重排、不建包、不做任务编排。
agent_created: true
---

> 本技能不是入口。调度、门禁、并发走 `short-drama-supervisor`（2026-11-10 已独立）。
> 本技能只负责「把编排好的 plan.json 装配成一条能看的纯剪预告」。
> **职责划清**：定轴 / 三拍选段 / 五幕重排归 `short-drama-script`，本技能不再宣称做这三件；
> plan.json 不齐就退回上游，不自己补。

# 纯剪预告装配

**零解说、零口播、原片原声混剪，唯一目标是勾人去看全剧。**
它不是缩短版原剧，是从全剧里抽出一条受力结构重新讲一遍——
每一帧都是原片，讲的顺序不是原片。

> 方法论完整版（含迭代史与全部判据出处）：仓库 `docs/纯剪预告方法论_v0.1.md`（正文已升 v0.2）。
> 王婶织入位配方：`references/wangshen-weave-in.md`。

## 何时使用 / 不要使用

- 使用：剧集包已建好，要出一条纯剪预告或第二条片子（换受力结构）。
- 不使用：建包/补台词（`short-drama-pack`）；定轴 / 选段 / 五幕重排（`short-drama-script`）；
  AI 织入片生成（`short-drama-video`）；带货混剪（`ecommerce-mix`，与本线无关）。

## 工序（四步，每步有判据）

> **编排在上游**：定轴 / 三拍选段 / 五幕重排由 `short-drama-script` 完成并产出 plan.json。
> 本技能从「拿到 plan.json」开始，不重新选段。

1. **复核 plan.json**：每段 `beats` 三拍注释齐不齐、`选段说明.md` 有没有五幕归属与
   But/Therefore 连接、砍场前因复述在不在；缺任一项退回 `short-drama-script`，不自己补。
   **存量批次例外（11-10 口径）**：`选段说明.md` 未落盘时，明确报「存量批次未落盘」并继续装配，
   不静默放行、也不倒推退回上游——定版用例 D5 属此情形，已在台账标注。
2. **装配**：跑 `scripts/mix_trailer.py`（自动校验出点上限 = 视频流时长，超限报错不装配）。
3. **验证三件**：抽单帧全尺寸看字幕切点（小拼图会看串行）；段界帧查黑帧；
   AI 织入段加 ASR 逐字比对台词。
4. **（可选）王婶织入**：见 `references/wangshen-weave-in.md`。织入位首选
   第一场后的黑场位（消化位+导航位），插完整 AI 段后原黑场取消。

## 硬纪律（违反必翻车，全部实测换来的）

| 纪律 | 原因 |
|---|---|
| 出点上限 = **视频流时长**，不是 format 时长 | 每集视频流比音频短 0.6–0.7s，超切带黑帧 |
| 切台词边界用原片硬字幕出现/消失（±0.3s） | silencedetect 在田间底噪下测不出人声 |
| 抽帧验证用**单帧全尺寸** | <300px 拼图看串行，误报时间戳错位 |
| 分段 loudnorm 后必须全局 alimiter | 叠化交叉区叠加超峰（实测 TP+1.24dB 削波） |
| SAR setsar=1 放 scale 之后；48k/fltp 统一 | concat 段间 SAR 不一致输出花屏 |
| 黑场/字幕卡先落地成图片文件 | lavfi 源不能直接进 concat |
| 砍场必须复述砍掉的前因 | 完整性 vs 时长是硬兑换，取舍要显性 |
| ffmpeg 不在 PATH 时用 --ff 传绝对路径 | 本机装在 /opt/anaconda3/bin |

## 输入 / 输出

| 项 | 说明 |
|---|---|
| 剧集包 | `packs/short-drama/<剧名>/drama-pack.json`（story_arc / finale 门禁） |
| 台词库 | `assets/short-drama/<剧名>/lines/lines_epNN.json` |
| plan.json | **由 `short-drama-script` 产出**；字段见 scripts/mix_trailer.py 头部注释。允许版本化命名 `plan_<tag>.json`（存量 `plan_v3ws.json` 即此） |
| 输出 | 成片 mp4 + 段落表打印 + （织入时）ASR 比对记录；成片文件名自定义，台账写实际路径 |

**时长预期（11-11 按主/副线分档）**：主线每场自洽的诚实代价是 4–6 分钟、五幕齐；
**副线 2–3 分钟、只取 Tease ＋ 三拍拒绝 ＋ Button 三幕**。
「占原片 40–50%」没写分母，实测同一条片子换分母能从 19% 跳到 41%，**不再当合格线**。
压时长的刀序：先砍功能重复场 → 再砍标签来源场（须用户拍板，前因会缺）。
砍场是编排决策，归 `short-drama-script`；本技能只报装配后的实际时长账。
