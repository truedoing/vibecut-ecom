# VibeCut 电商素材台

给电商短视频团队用的**素材库 + 去字幕 + 任务台账 + 版本审核**。

它不剪片子、不做时间轴。它解决的是团队共同记忆的问题：素材找不到、缺了才去搜、搜完还要去字幕、版本混乱、审核靠口头。

> Agent 是编剧和外包，VibeCut 是片场和资产库。
> WorkBuddy 负责想和做，VibeCut 负责记住、复用、审核、记账。

## 快速开始

```bash
# 1. 安装（Python 3.10+，需要 ffmpeg）
pip install -e .
# 如果 vibecut 不在 PATH 上（例如 conda 未激活）：
#   /opt/anaconda3/bin/vibecut ...   或   python -m vibecut.cli ...

# 2. 初始化数据目录（默认 ~/VibeCutEcomData）
vibecut init

# 3. 启动本地服务（仅本机）
vibecut serve --host 127.0.0.1 --port 8766
# 团队局域网访问：
#   vibecut serve --host 0.0.0.0 --port 8766
#   其他人浏览器打开 http://<主机IP>:8766

# 4. 另开一个终端
vibecut project add 羽衣甘蓝 --media-root "/path/to/参考视频"
vibecut assets ingest --project 羽衣甘蓝 --file ./素材.mp4 \
  --tags "实拍,冲泡" --layer L1 --note "玻璃杯冲水特写"
vibecut assets search --project 羽衣甘蓝 --query "冲泡"
```

浏览器打开 `http://127.0.0.1:8766` 可以看到素材库与任务看板。

## 它解决什么

| 能力 | 命令 | 典型场景 |
|---|---|---|
| 素材入库 | `vibecut assets ingest` | 新拍的、搜到的片段登记进团队库 |
| 素材检索 | `vibecut assets search` | "有没有冲泡特写？"跨项目找 |
| 缺口清单 | `vibecut assets gap` | 文案定了，先看素材够不够 |
| 去字幕 | `vibecut desub check/run` | 参考片段有烧录字幕，只去字幕保留包装 |
| 任务台账 | `vibecut task list/status` | 待素材 → 待初剪 → 待审 → 待发 → 已发 |
| 跨项目复用 | `assets search --scope all` | 上一个品的爆款镜头，这个品还能用 |

## 给 WorkBuddy 用

把 [`skills/vibecut/SKILL.md`](skills/vibecut/SKILL.md) 交给 WorkBuddy。它描述了何时调用、命令示例、输出格式，以及几条硬规则（比如 `risk=high` 的片段不要硬修）。

推荐工作流：

```
assets gap --requirements req.json   → 缺什么
  → 搜索/生成/实拍                  → 拿到片段
  → desub check                     → risk=low 才 run
  → assets ingest                   → 入库复用
  → task status                     → 交给剪辑
```

## 数据目录

```
~/VibeCutEcomData/
├── vibecut.db                  # SQLite，唯一真源
├── projects/<项目>/
│   ├── assets/files/           # 入库素材
│   ├── assets/thumbs/          # 缩略图
│   ├── tasks/<任务>/           # 任务资料
│   └── desub/<报告>/           # 去字幕产物
└── desub/                      # 不属于任何项目的去字幕产物
```

素材文件永远在磁盘，库里只存项目内相对路径。备份只需拷 `data_root`。

### 配置

优先级：命令行参数 > 环境变量 > `~/.vibecut-ecom.json` > 默认值。

```json
{
  "data_root": "/path/to/data",
  "host": "127.0.0.1",
  "port": 8766,
  "ffmpeg": "/path/to/ffmpeg"
}
```

ffmpeg 探测顺序：`VIBECUT_FFMPEG` → 配置 → `tools/ffmpeg/bin` → PATH → 常见安装位置。

## 从旧 VIBECAP 迁移

```bash
python scripts/migrate_from_vibecut.py \
  --src /Users/zgl/VIBECAP/羽衣甘蓝 \
  --dst ~/VibeCutEcomData \
  --dry-run     # 先看报告，再去掉 dry-run
```

导入资产（保留旧 `asset_id` 为 `legacy_id`）、任务脚本、参考图选择、抽卡记录；幂等，可重复执行。

## 开发

```bash
# 后端测试
PYTHONPATH=src pytest -q

# 前端开发（代理到 8766）
cd web && npm install && npm run dev

# 前段构建后，FastAPI 会直接托管 web/dist
cd web && npm run build
```

## 不做

时间轴、成片渲染、通用剪辑、影视管线、多租户 SaaS、素材上云。
