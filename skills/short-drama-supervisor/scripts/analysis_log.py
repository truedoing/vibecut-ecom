#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""素材分析的耗时与用量台账：每跑一轮脚本，落一条记录。

为什么要单独做（11-13）：
素材分析页要回答「OCR 跑了多久、画面打标跑了多久、一共花了多久」，
但产物 JSON 里从来只有结果（几句台词、几帧、几个镜头），没有耗时；
`runs/进度/` 的小文件跑完就被下一轮覆盖，历史只剩屏幕上那行字。
**结果能重建，耗时不能重建**——不记就永远没有了。

设计取舍：
- **一部剧一个文件**（`runs/分析记录/<剧>.json`），是个数组，主进程在跑完时追加一条。
  不按集记：并发跑 8 路时每集写一条要处理竞争，而耗时汇总本来就只要批次级精度。
- **每集的精确耗时写进产物 JSON 的 `elapsed_sec`**（extract_lines / tag_shots 各自写），
  台账只管批次总账。两处各有用途：单集耗时看哪一集特别慢，批次总账算总账。
- **`source` 分三种**，页面必须区别显示，不许混成一个数：
  `auto` 脚本自己记的；`manual` 事后按已知数字补记的；`estimate` 从文件时间戳推的（最不准）。
- 写失败一律吞掉：台账坏了不能把分析任务搞挂。

用法（脚本侧）：
    import analysis_log
    t0 = time.time()
    ...
    analysis_log.record(drama, "ocr_lines", started=t0, eps=18,
                        items=1284, note="3 路并行")

用法（命令行，事后补记）：
    python3 analysis_log.py --list 以真心守护至亲
    python3 analysis_log.py --add 父爱跨越山海 --stage ocr_lines --minutes 20.27 \
        --eps 18 --items 1284 --source manual --note "台账续八：净 25:52 中的 20:16"
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

try:  # 数据目录不靠数层级猜：见同目录 _env.py（换机器也不用改代码）
    from _env import data_root as _data_root
except Exception:  # 极端情况下退回老办法，至少不崩
    def _data_root():
        return Path(__file__).resolve().parents[3]

# 环节顺序＝素材分析的执行顺序，页面按这个顺序列，别按字典序
STAGES = [
    ("ocr_lines", "字幕识别", "抽帧 + 逐帧认字，把每集的台词连同时间码抠出来"),
    ("segment", "镜头切分", "按画面变化把每集切成镜头，后面判「谁在说话」要以镜头为单位"),
    ("tag_shots", "画面打标", "让模型看每个镜头的首中尾三帧，判谁在张嘴、什么表情、什么景别"),
    ("build_sentences", "台词与画面对齐", "把台词和镜头按时间对起来，得出「这句谁说的」"),
]
STAGE_LABEL = {k: v for k, v, _ in STAGES}
STAGE_ORDER = {k: i for i, (k, _, _) in enumerate(STAGES)}
SOURCE_LABEL = {"auto": "脚本记录", "manual": "事后补记", "estimate": "按文件时间推的"}


def root() -> Path:
    """数据目录。唯一出口是 _env.data_root()（11-16 收敛，理由同 progress.root）。"""
    return _data_root()


def dir_of() -> Path:
    return root() / "runs" / "分析记录"


def file_of(drama: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(drama))[:40]
    return dir_of() / f"{safe}.json"


def read(drama: str) -> list[dict]:
    p = file_of(drama)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(d, dict):
        d = d.get("records") or []
    return [r for r in d if isinstance(r, dict)] if isinstance(d, list) else []


def record(drama: str, stage: str, started: float | None = None,
           finished: float | None = None, eps=None, items=None, tokens: int | None = None,
           source: str = "auto", note: str = "") -> None:
    """追加一条批次记录。started/finished 是 time.time() 的时间戳。"""
    try:
        finished = finished if finished is not None else time.time()
        started = started if started is not None else finished
        sec = max(0.0, finished - started)
        item = {
            "stage": stage,
            "label": STAGE_LABEL.get(stage, stage),
            "at": datetime.fromtimestamp(finished).isoformat(timespec="seconds"),
            "elapsed_sec": round(sec, 1),
            "eps": eps,
            "items": items,
            "tokens": tokens,
            "source": source,
            "note": note,
        }
        rows = read(drama)
        rows.append(item)
        _save(drama, rows)
    except Exception:
        pass  # 台账坏了不能把分析任务搞挂


def _save(drama: str, rows: list[dict]) -> None:
    d = dir_of()
    d.mkdir(parents=True, exist_ok=True)
    dst = file_of(drama)
    fd, tmp = tempfile.mkstemp(prefix=".anallog_", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def bundle(drama: str) -> dict:
    """给页面用：按环节汇总耗时，同一环节多轮取最新一轮（重跑代表当前质量）。

    同时给出「每一轮」的明细，便于看到重跑了几次、每次多久。
    """
    rows = read(drama)
    by_stage: dict[str, list[dict]] = {}
    for r in rows:
        by_stage.setdefault(r.get("stage") or "?", []).append(r)

    out = []
    for key, label, why in STAGES:
        runs = sorted(by_stage.get(key, []), key=lambda x: x.get("at") or "")
        latest = runs[-1] if runs else None
        out.append({
            "stage": key,
            "label": label,
            "why": why,
            "sec": latest.get("elapsed_sec") if latest else None,
            "eps": latest.get("eps") if latest else None,
            "items": latest.get("items") if latest else None,
            "tokens": latest.get("tokens") if latest else None,
            "source": latest.get("source") if latest else None,
            "source_label": SOURCE_LABEL.get((latest or {}).get("source"), "") if latest else "",
            "at": latest.get("at") if latest else None,
            "runs": len(runs),
            "history": [{"at": r.get("at"), "sec": r.get("elapsed_sec"),
                         "eps": r.get("eps"), "source": r.get("source"),
                         "note": r.get("note") or ""} for r in runs],
        })
    # 其它没列进 STAGES 的环节也带上，别悄悄丢掉
    for key, runs in by_stage.items():
        if key in STAGE_ORDER:
            continue
        latest = sorted(runs, key=lambda x: x.get("at") or "")[-1]
        out.append({"stage": key, "label": STAGE_LABEL.get(key, key), "why": "",
                    "sec": latest.get("elapsed_sec"), "eps": latest.get("eps"),
                    "items": latest.get("items"), "tokens": latest.get("tokens"),
                    "source": latest.get("source"),
                    "source_label": SOURCE_LABEL.get(latest.get("source"), ""),
                    "at": latest.get("at"), "runs": len(runs), "history": []})
    return {"stages": out,
            "has_log": bool(rows),
            "record_count": len(rows)}


def main() -> int:
    ap = argparse.ArgumentParser(description="素材分析的耗时台账")
    ap.add_argument("--list", metavar="剧名")
    ap.add_argument("--add", metavar="剧名")
    ap.add_argument("--stage", choices=[k for k, _, _ in STAGES] + ["other"], default=None)
    ap.add_argument("--minutes", type=float, default=None, help="耗时（分钟），更长的用 --sec")
    ap.add_argument("--sec", type=float, default=None, help="耗时（秒）")
    ap.add_argument("--eps", type=int, default=None, help="这一轮跑了几集")
    ap.add_argument("--items", type=int, default=None, help="这一轮处理了多少帧/镜头")
    ap.add_argument("--tokens", type=int, default=None)
    ap.add_argument("--source", choices=list(SOURCE_LABEL), default="manual")
    ap.add_argument("--note", default="")
    a = ap.parse_args()

    if a.list:
        b = bundle(a.list)
        if not b["has_log"]:
            print(f"「{a.list}」还没有任何记录（{file_of(a.list)}）")
            return 1
        print(f"{a.list}　共 {b['record_count']} 条记录")
        for s in b["stages"]:
            if s["sec"] is None:
                continue
            print(f"  {s['label']:<12} {s['sec']/60:>7.1f} 分钟"
                  f"　{s['eps'] or '-'} 集　{s['items'] or '-'} 项"
                  f"　{s['source_label']}　{s['at']}　已跑 {s['runs']} 轮")
        return 0

    if a.add:
        if not a.stage:
            ap.error("--add 要带 --stage")
        sec = a.sec if a.sec is not None else (a.minutes or 0) * 60
        if sec <= 0:
            ap.error("要给 --minutes 或 --sec")
        record(a.add, a.stage, started=time.time() - sec, finished=time.time(),
               eps=a.eps, items=a.items, tokens=a.tokens,
               source=a.source, note=a.note)
        print(f"已记一条：{a.add}　{STAGE_LABEL.get(a.stage, a.stage)}　"
              f"{sec/60:.1f} 分钟　{a.eps or '-'} 集　({SOURCE_LABEL[a.source]})")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
