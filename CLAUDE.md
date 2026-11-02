# VibeCut 电商素材台（vibecut-ecom）

给 3–8 人电商短视频团队用的**素材库 + 去字幕 + 任务台账 + 版本审核**。

> Agent 是编剧和外包，VibeCut 是片场和资产库。
> WorkBuddy 负责想和做，VibeCut 负责记住、复用、审核、记账。

## 与旧仓库 VIBECAP 的关系

- 旧仓库 `/Users/zgl/VIBECAP`（影视解说/口播/电商导演台）**已冻结**，不再加电商功能，只作参考实现与知识/课程资产。
- 新仓库**不 import 旧代码**。去字幕是整体搬运，搬完旧仓库不再维护该模块，因此没有双份维护。
- 电商功能一律在这里做，不要回旧仓库改。

## 运行

```bash
pip install -e .                      # 已装过；CLI 在 /opt/anaconda3/bin/vibecut
vibecut init
vibecut serve --host 0.0.0.0 --port 8766
```

浏览器 `http://<主机IP>:8766`。数据根默认 `~/VibeCutEcomData`（本机是 `/Users/apple/VibeCutEcomData`）。

```bash
# 测试
PYTHONPATH=src pytest -q

# 前端
cd web && npm install && npm run dev    # 代理到 8766
cd web && npm run build                 # FastAPI 直接托管 web/dist
```

## 代码结构

```
src/vibecut/
├── cli.py              # 唯一对外接口：Agent/人/脚本都走它
├── config.py           # data_root / host / port / ffmpeg
├── core/
│   ├── db.py           # SQLite 唯一真源：schema + WAL + 写锁
│   ├── paths.py        # 安全文件名 / Windows 长路径 / 原子写
│   ├── media.py        # ffmpeg 探测 / 元数据 / 缩略图
│   └── locks.py        # 跨进程文件锁
├── assets/
│   ├── store.py        # 入库（sha 去重）/ 检索 / 使用记录
│   └── gap.py          # 缺口分析（优先显式 requirements）
├── desub/              # 从旧 lib/desub 搬运：OCR/分类/轨迹/mask/修复/QA
├── tasks/state.py      # 五状态：待素材/待初剪/待审/待发/已发
├── reviews/            # M2：审核规则
└── server/             # FastAPI：assets / tasks / desub / health + SPA 托管
```

## 硬规则

1. **SQLite 是唯一真源**。不要再引入「JSON 文件 + manifest + DB」三套记账。
2. **素材文件在磁盘，库里只存项目内相对路径**。媒体永不进 DB，永不上传云端。
3. **不做时间轴、Elah、成片渲染、通用剪辑**。精修交给剪映。
4. **不做影视管线**（scene_map / VLM / BGE / storyboard_match）。
5. **缺口分析 v1 不引入 LLM**。WorkBuddy 用 `--requirements` 给需求，VibeCut 只核对有没有。
6. **`desub check` 的 `risk=high` 不要硬修**。字幕压在包装上时被遮住的内容已经不存在，应换片段/盖字幕条/重生成。
7. **Windows 是第一交付目标**。路径一律 `pathlib` + `core.paths.long_path()`，ffmpeg 一律走 `core.media`。
8. **CLI 不 import 服务端模块**，只走 HTTP；保证 Agent 侧行为稳定。

## 当前进度

**M1 已完成**：SQLite、CLI、素材入库/检索/缺口、去字幕 check/run、薄看板两页、旧数据迁移脚本。
羽衣甘蓝验证数据：13 资产 / 7 任务 / 22 抽卡记录。

**M2 待做**：版本（`version save/list`）、审核闸门（`review check`）、Windows 绿色包 + 开机自启、并发写冲突 409、任务详情页。

**M3 按需**：生成管理（Seedream/Seedance 抽卡入库）、跨项目资产复用配方、自动更新版本接口。

## 参考

- `README.md` — 快速开始、数据目录、迁移
- `skills/vibecut/SKILL.md` — 交给 WorkBuddy 的说明书
- 设计决策记录：`/Users/apple/.claude/plans/validated-marinating-bentley.md`
