---
name: vibecut-assets
description: 电商短视频素材台。当需要查找/盘点已有素材、判断脚本缺哪些素材、给视频去硬字幕、把素材入库复用、查看或更新团队任务状态时使用。所有操作通过 `vibecut` CLI 完成，返回 JSON。
---

# VibeCut 素材台

你是编剧和外包，VibeCut 是片场和资产库。它记住团队已有哪些素材、哪些任务卡在哪、每个版本是谁改的。

## 何时使用

- 写完文案后，想知道**现有素材够不够**
- 需要找一段特定画面（"冲泡特写""产品包装"）
- 素材上有烧录口播字幕，需要**只去字幕、保留包装文字**
- 手上有新拍的/搜到的片段，需要**入库复用**
- 需要查看/更新团队任务进度

不要用它写文案、剪片子、做时间轴——那些不在这个工具里。

## 前置检查

```bash
vibecut --human task list            # 能返回 JSON 说明服务在跑
```

如果提示 `command not found`，改用 `python -m vibecut.cli`（在仓库根执行）。
如果报"连接不上 VibeCut 服务"，告诉用户先执行 `vibecut serve`，不要自己启动。

## 核心用法

### 1. 找素材

```bash
vibecut assets search --project 羽衣甘蓝 --query "冲泡特写" --limit 20
vibecut assets search --project 羽衣甘蓝 --tags "实拍,厨房" --layer L1
vibecut assets search --project 羽衣甘蓝 --scope all --query "包装"
```

返回：

```json
{"ok": true, "count": 3, "assets": [
  {"id": 12, "kind": "video", "layer": "L1", "tags": ["实拍","冲泡"],
   "note": "玻璃杯冲水特写", "duration_sec": 6.2,
   "file_url": "/api/assets/12/file", "thumb_url": "/api/assets/12/thumb"}
]}
```

### 2. 判断缺口

```bash
vibecut assets gap --project 羽衣甘蓝 --task Task3
vibecut assets gap --project 羽衣甘蓝 --task Task3 --requirements req.json
```

**优先传 `--requirements`**：你比脚本关键词更清楚需要什么。

```json
{"items": [
  {"id": "r1", "desc": "产品包装正面特写", "tags": ["包装"], "kind": "video", "action": "实拍"},
  {"id": "r2", "desc": "冲泡时粉末落入水中", "tags": ["冲泡"], "kind": "video", "action": "生成"}
]}
```

返回 `missing` 列表；每项带 `action`（搜索/生成/实拍），直接作为下一步任务。

### 3. 去字幕

先 `check`，再决定要不要 `run`。**`risk=high` 不要硬修**：说明字幕压在包装/营养表上，被遮住的内容已经不存在，修出来是猜的。应换片段、用字幕条盖住、或重新生成。

```bash
# 只判风险（快）
vibecut desub check --project 羽衣甘蓝 --file ./竞品片段.mp4 --start 4.36 --end 9.82

# 确认风险低再修复
vibecut desub run --project 羽衣甘蓝 --file ./竞品片段.mp4 --start 4.36 --end 9.82 --backend cv2
```

`check` 返回 `{risk, conflicts, removed_texts, packaging_texts}`。
`run` 返回 `{risk, qa, files:{result, overlay, contact_sheet}}`。

### 4. 入库

```bash
vibecut assets ingest --project 羽衣甘蓝 \
  --file ./新素材/包装特写.mp4 \
  --tags "实拍,包装" --layer L1 --grade A \
  --note "产品包装正面，光线可用"
```

- 默认复制进项目素材库；`--no-copy` 只登记原路径。
- 同一文件重复入库会返回 `duplicate: true`，不是错误。
- 标签用英文逗号分隔；越具体越容易被检索到。

### 5. 任务台账

```bash
vibecut task list --project 羽衣甘蓝
vibecut task list --project 羽衣甘蓝 --status 待审
vibecut task new --project 羽衣甘蓝 --name Task8 --brief "反常识角度：不是减肥"
vibecut task status --project 羽衣甘蓝 --name Task8 --status 待审 --by WorkBuddy
```

状态只有五个：`待素材`、`待初剪`、`待审`、`待发`、`已发`。改状态时带上 `--by`，团队看板会显示最后编辑人。

## 推荐工作流

```
1. assets gap --requirements req.json      → 拿到 missing 清单
2. 对每项 missing：搜索/生成/实拍            → 得到片段
3. 片段有硬字幕 → desub check → risk=low 才 desub run
4. assets ingest 入库（带 tags/layer/grade） → 下次可复用
5. 交给人剪辑，task status --status 待剪/待审
```

## 输出约定

- 所有命令返回 JSON；失败时 `{"ok": false, "error": "...", "hint": "..."}`。
- 退出码：0 成功，1 失败。
- 素材文件用返回的 `file_url` / `thumb_url`，不要自己拼绝对路径。
- 项目名必须与 `vibecut project list` 中的名字完全一致。

## 不要做

- 不要修改 `data_root` 下的文件（除了通过 `assets ingest`）。
- 不要在 `risk=high` 时强行 `desub run` 并宣称修好了。
- 不要为了"凑齐素材"虚构 `desc` 或标签。
- 不要创建状态之外的任务状态。
