# VibeCut 短剧素材台（vibecut-ecom）

给短剧二创团队用的**素材分析建库 + 剧集包 + 选题/剪辑清单 + 装配出片 + 只读工作台**。

> 2026-11-13 起只保留短剧线，电商线运行代码已删除，历史留在 git。
> Agent 是编剧和外包，VibeCut 是片场、资产库和看板。
> 项目同时服务三块：**生成**（每天出片）、**教学**（方法和翻案讲得清）、**自媒体**（过程可传播）。

## 运行

```bash
# 只读工作台（Python 标准库，零依赖）
python3 skills/short-drama-supervisor/ui/server.py
# http://127.0.0.1:8765   支持 #pipeline / #assets / #review / #video 直达
```

素材根写在仓库根 `.env`，不写死在代码里：

```bash
SHORT_DRAMA_ASSET_ROOT=/Users/zgl/短剧
```

打标、装配脚本需要 ffmpeg / ffprobe；**工作台不调用它们**（时长读 MP4 盒头）。

## 代码结构

```
skills/short-drama-*/              # 8 个技能：选片 / 建包 / 索引 / 角色 / 选题 / 出片 / 装配 / 主管
  short-drama-supervisor/
    scripts/status_overview.py     # 状态计算：谁要重做、下一步做什么（网页也调它）
    scripts/progress.py            # 长跑任务进度上报：一任务一文件、原子写、带 pid
    ui/server.py                   # 只读 HTTP 服务：只处理 GET
    ui/index.html                  # 单文件工作台，无构建步骤
runs/工作台.json                   # 当前选中 + 剧名目录对照 + 共用层版本
runs/<剧>/<任务>/状态.json          # 一条片子的七环节进度（唯一真源）
runs/<剧>/<任务>/plan.json          # 剪辑清单：seg(label/type/ep/a/b/beats)
runs/<剧>/<任务>/build/             # 装配产物与自检图
assets/short-drama/<剧>/            # lines / shots / sentences / char_map / index.sqlite
packs/short-drama/<剧>/             # 剧集包
docs/                              # 方法论、进度台账、UI 三份文档
```

## 硬规则

1. **状态文件是唯一真源**。网页只画不算：`status_overview.py` 算，UI 不另算一套。
2. **UI 只读**：只处理 GET，POST/PUT/DELETE/PATCH 一律 405；页面不执行脚本、不写数据。
3. **媒体不入库、不上云**。原片在素材根，`runs/**/*.mp4` 可再生、已 gitignore。
4. **不做时间轴、通用剪辑、成片渲染**。精修交给剪映。
5. **done ≠ approved**。`pack` G0 / `script` G1 / `trailer` G3，人没点头不许往下。
6. **改清单 ≠ 重做出片**。只改选段/顺序就只重拼；改素材台词/上下文才重新出片。
7. **路径去本机化**。素材根写 `.env`，剧包不写绝对路径。
8. **换剧包是挂起，不是作废**。只改工作台 `active`，历史不删。
9. **UI 零依赖**：Python 标准库 `http.server` + 单文件 HTML；不为看板引入前端框架。
10. **文档三块**：决策留翻案、版本留口径、过程留故事。
11. **长跑脚本必须上报进度**：用 `progress.py`（一任务一文件、带 pid / 耗时 / done / total），
    页面只读这些文件；`runs/进度/` 是运行态，已 gitignore。
12. **剧编号与状态只读 `runs/剧注册表.json`**：只给活动剧编 J1..Jn，归档号让出；
    页面默认只看活动剧，归档单独一个子页签；不在前端自己编号或排序。

## 当前进度

- **技能链 v0.4**（批量管线）已定；状态台账、门禁、改动影响传播已验收。
- **工作台 v0.7**：总览一剧一行 + 四段进度条 + 阶段门禁；活动/归档子页签 + J 号；
  素材分析读覆盖率与 60% 目标线；深色监看风 + 实时 LIVE 面板；
  素材分析 / 待审文案 / 成片预览；已删电商线；预览页按「剧 / 片子」分组。
- **待做**：分镜台、原视频预览、30 部剧分页、素材分析与待审文案页继续打磨。

## 参考

- `README.md` — 快速开始、目录、数据流
- `docs/进度台账.md` — 技能线进度
- `docs/UI_技术决策.md` — UI 为什么这么选（含被推翻的判断）
- `docs/UI_版本演变.md` — UI 版本节点与口径变更
- `docs/UI_教学与自媒体素材.md` — 可讲课、可发内容的故事与数据
- `skills/short-drama-supervisor/SKILL.md` — 调度入口
