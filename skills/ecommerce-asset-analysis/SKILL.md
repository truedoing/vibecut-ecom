---
name: ecommerce-asset-analysis
description: "分析商家官方素材库（原片视频目录）：ffprobe 摸底 → ffmpeg 场景检测切镜头 → 每镜头抽 1 帧 → MiMo/智谱 VLM 批量打标（画面描述/主体/景别/场景/画面文字/可用用途），产出 asset_index.json（机读，供文案技能选画面做音画同步）+ 素材台账.md（人看）。触发词：分析素材、素材打标、建素材库、素材台账、抽帧分析。"
---

> **本技能不是入口。** 调度、进度、多线并发、门禁与状态台账一律走 `ecommerce-supervisor`；
> 本技能只负责「把原始素材变成可检索的镜头库」，不判断「现在该不该做、做完该接谁」。

# ecommerce-asset-analysis · 官方素材分析

把商家给的原始素材（相机原片，文件名无意义、无台词）变成**可机读、可检索的镜头级素材库**，
供 `ecommerce-script-gen` 写混剪段时按标签取画面（音画同步的"画"从这出）。

## 定位与边界

- **一次分析、多方消费**：文案选画面、剪辑找素材、复盘核对都读 `asset_index.json`。
- **只读原片，绝不动原片**：抽帧、缩图全部写到产出目录，原片零修改零移动。
- **媒体永不上传云端**：VLM 只收缩图（640px JPEG base64），原文件不出本机。
- **可增量续跑**：按文件级断点，中断后重跑自动跳过已完成视频。
- 与 `ecommerce-category-pack` 的关系：包里 `asset_index` 块只登记"素材已分析"状态与索引路径，
  镜头明细不进包（会变、体积大），真源在本技能的产出目录。

## 输入输出

| 项 | 说明 |
|---|---|
| 输入 | `--source` 素材根目录 + `--categories` 逗号分隔子目录（支持 `桌拍素材/4.10` 二级路径） |
| 输出 | `--out` 产出目录：`asset_index.json` + `素材台账.md` + `frames/`（抽帧缓存，可随时删） |
| 密钥 | 读仓库根 `.env` 的 `MIMO_API_KEY / MIMO_BASE_URL / MIMO_MODEL`；智谱备用走 `ZHIPU_API_KEY` |

## 用法

```bash
cd <仓库根>          # 即 skills/ 的上一级；.env 与 packs/ 都在这里
# 验证批次：桌拍+磨酱+罐装（示例，--source 用 .env 里登记的素材根目录）
python3 skills/ecommerce-asset-analysis/scripts/analyze.py \
  --source "$ASSET_ROOT" \
  --categories "桌拍素材/4.10,磨酱,流水线罐装" \
  --out assets/虎坚果酱 \
  --category-label 虎坚果酱
```

参数：`--only` 只处理匹配子串的文件（调试用）；`--no-vlm` 只摸底+切镜头不调 API；
`--force` 忽略断点重算；`--provider mimo|zhipu`（默认读 .env 有哪个用哪个，mimo 优先）；
`--workers` 并发（4K 源必须 1，1080p 可 2）；`--limit` 单次限量（分片执行用）。

### 取镜头：query.py（文案技能用这个，别整份读 JSON）

```bash
# 按用途+景别+最短时长筛（音画同步选画面）
python3 scripts/query.py assets/虎坚果酱/asset_index.json --usage texture --size 特写 --min-len 3 --limit 8
# 按关键词筛（涂抹/开罐/灌装…）
python3 scripts/query.py assets/虎坚果酱/asset_index.json --kw 涂抹,开罐 --limit 6
# 生成人读目录（每镜头一行，按用途分组）
python3 scripts/query.py assets/虎坚果酱/asset_index.json --catalog assets/虎坚果酱
```

输出含 `shot_id`（如 `C5396#0`）、文件绝对路径、可截取时间段——文案里直接写
「用 C5396 取 0-8 秒」即可，剪辑照着掐。

**索引体积参考**：148 视频 / 210 镜头 = 113 KB（5200 行只是缩进假象）。这个量级不需要数据库，
也不需要向量检索；需要的是 `query.py` 这种按条件取子集，避免把整份 JSON 塞进上下文。

### 路径与多人协作

索引里**只存相对路径**（`videos` 的 key 如 `桌拍素材/4.10/C5319.MP4`），素材根单独存一个
`asset_root` 字段，且**只是默认值**。解析优先级：

```
--root 参数  >  环境变量 ASSET_ROOT  >  索引里的 asset_root
```

因此同一份 `asset_index.json` 可以跨机器共享：素材本体留在各自本机（或共享盘挂载点），
每人在 `.env` 里写自己的 `ASSET_ROOT`，索引文件一个字都不用改。

- **素材本体永不上云**（23G 原片、码率 70Mbps），上云的只有文本产物：`asset_index.json` / `catalog.md` / `素材台账.md`。
- 剪辑要读原片时，把共享盘挂到本地固定挂载点（如 `/Volumes/素材盘`），`ASSET_ROOT` 指过去即可；
  4K 原片走网络剪会卡，实践中用「原片留盘 + 本地代理文件」的方式，代理不入索引。

## 流程（五步，脚本自动）

1. **摸底**：ffprobe 全量采集（时长/分辨率/帧率/体积）→ 存进 index 的 `manifest`。
2. **切镜头**：`fps=8,scale=320:-1` 降采样后场景检测（阈值 0.30），切点即镜头边界；
   短于 1.2s 的碎镜头并入前镜头；单视频镜头数上限 15（超出合并最小间隙），控成本。
3. **抽帧**：每镜头取中点 1 帧，640px 宽 JPEG（q=5），存 `frames/<文件名>/shot_NN.jpg`。
4. **VLM 打标**：每次调用带 6 帧多图 + 结构化 prompt，输出严格 JSON 数组；
   4 并发 + 空响应重试 3 次 + JSON 解析失败重试。字段规范见 `references/tag-spec.md`。
5. **产出**：`asset_index.json`（manifest + 每视频 shots 数组：start/end/描述/主体/景别/场景/画面文字/用途标签）
   + `素材台账.md`（按目录分组的人读视图 + 用途标签统计）。

## 打标用途标签（usage 枚举，固定 6 个）

`product`(罐体/包装) `process`(工艺/磨酱/灌装) `origin`(种植/产地/航拍) `texture`(质地/细节)
`hand`(手部动作) `other`(其他过场)。文案技能按 usage + 景别 + 描述关键词选镜头。

## 踩坑记录

- MiMo 鉴权用 `api-key` 请求头，**不是** `Authorization: Bearer`（mimo-case-pipeline 已踩过）。
- MiMo vision 偶发返回空 `content`，重试即可；脚本已内置。带推理 token，`max_tokens` 要给到 4000。
- **prompt 必须带品类上下文**：泛型识别会把油莎豆认成"咖啡豆"；给"素材来自虎坚果酱商家实拍"的提示后输出正确。
- 场景检测必须先 `fps=8` 降采样，否则 1080p60 原片全量解码慢 5 倍以上。
- **长镜头别扫全片**：>30s 的航拍/沙漠连续镜头，全片场景检测 22.9s 且常常 0 切点；
  等间隔 8s 采样（`--long-clip`，定点取帧 0.34s/帧）足够描述画面。
- **长任务分片执行**：大批量要拆成多个 <8 分钟的前台批次跑（断点续跑兜底），
  不要指望单个后台任务跨轮存活（实测被系统杀掉、任务 ID 消失）。
- **4K 源 + `--workers 2` 会 OOM**（exit 137）：4K 60fps 双路并发解码内存超限；
  4K 文件用 `--workers 1`，1080p 可以 2。
- 高码率原片（70Mbps）不要抽太多帧：一帧中点足够，景别变化大的镜头靠切镜头兜底。
