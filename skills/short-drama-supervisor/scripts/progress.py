#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长跑任务的进度上报：每个任务写自己的一个小文件，网页只读这个目录。

为什么要单独做这件事（11-12 的事故）：脚本原来只把「ep15 已打标 24/45 帧」
打印到屏幕，进程在 20:15 静默死掉，48 分钟没人发现。屏幕上的东西没人读得到。

设计取舍：
- **一个任务一个文件**，不共写一个大 JSON。8 路并发时共写一个文件必然互相覆盖
  （读-改-写竞争），分开写天然没有竞争，也不用加锁。
- **原子写**：先写同目录的 .tmp 再 os.replace，网页永远不会读到写了一半的文件。
- **带 pid 和更新时间**：网页能自己判断「标着在跑、但三分钟没动静」＝疑似中断，
  也能直接查这个进程还活着没有。这是进度页最要紧的一条信息。
- 上报失败一律静默吞掉：**进度记录绝不能把主线任务搞挂**。

用法（脚本侧）：
    import progress
    progress.start("tag_shots", drama, ep, total=45, unit="帧")
    progress.update("tag_shots", drama, ep, done=24, total=45)
    progress.finish("tag_shots", drama, ep, note="15 个镜头")

    # 长阻塞（ffmpeg 装配 / 抽帧）期间每 30 秒 touch 一次，避免被判卡死
    stop = progress.heartbeat("mix_trailer", drama, task, every=30, note="ffmpeg 装配中")
    ...
    stop()
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path


def root() -> Path:
    """按 runs/工作台.json 这个标志找仓库根。

    技能是软链到用户级目录的，用 __file__.parents[3] 会指错地方，
    所以从脚本所在位置往上找，找到带工作台的那个目录为止。
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "runs" / "工作台.json").exists():
            return parent
    return Path(__file__).resolve().parents[3]


def dir_of() -> Path:
    return root() / "runs" / "进度"


LABELS = {
    "tag_shots": "镜头打标（看图认表情和嘴型）",
    "segment": "切镜头",
    "ocr_lines": "字幕识别",
    "build_sentences": "台词与画面对齐",
    "mix_trailer": "拼成片",
    "weave_video": "AI 出片",
}


def _safe(s: str, n: int = 32) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(s))[:n]


def _key(job: str, drama: str, ep) -> str:
    """文件名带上剧名 + 集号/任务名。多部剧同时跑时不会互相覆盖。

    ep 允许是整数（按集跑）或字符串（拼成片这类按任务跑）。
    """
    if ep is None:
        ep_part = ""
    elif isinstance(ep, int) or (isinstance(ep, str) and ep.isdigit()):
        ep_part = f"_ep{int(ep):02d}"
    else:
        ep_part = "_" + _safe(ep, 40)
    return f"{_safe(drama, 24)}_{_safe(job, 24)}{ep_part}"[:80]


def _write(job: str, drama: str, ep: int | None, payload: dict) -> None:
    try:
        d = dir_of()
        d.mkdir(parents=True, exist_ok=True)
        dst = d / f"{_key(job, drama, ep)}.json"
        data = {
            "job": job,
            "label": LABELS.get(job, job),
            "drama": drama,
            "ep": ep,
            "pid": os.getpid(),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        data.update(payload)
        # 同一个任务可能被重跑：start 会带新的 started_at，update/finish/fail
        # 不带，沿用文件里已有的开跑时间。这样「用时」才准。
        if dst.exists():
            try:
                old = json.loads(dst.read_text(encoding="utf-8"))
                if old.get("started_at") and not data.get("started_at"):
                    data["started_at"] = old["started_at"]
                if not data.get("unit") and old.get("unit"):
                    data["unit"] = old["unit"]
                # finish/fail 不带 done/total，沿用最后一次 update 的值，
                # 否则完成记录会丢掉「45/45 帧」这类数字。
                for k in ("done", "total"):
                    if k not in data and old.get(k) is not None:
                        data[k] = old[k]
            except (OSError, json.JSONDecodeError):
                pass
        fd, tmp = tempfile.mkstemp(prefix=".progress_", suffix=".tmp", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, dst)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception:
        pass  # 进度上报不许影响主线


def start(job: str, drama: str, ep: int | None = None,
          total: int = 0, unit: str = "步", note: str = "") -> None:
    now = datetime.now().isoformat(timespec="seconds")
    _write(job, drama, ep, {"state": "running", "done": 0, "total": total,
                            "unit": unit, "note": note,
                            "started_at": now})


def update(job: str, drama: str, ep: int | None = None,
           done: int = 0, total: int = 0, note: str = "") -> None:
    _write(job, drama, ep, {"state": "running", "done": done,
                            "total": total, "unit": "", "note": note})


def finish(job: str, drama: str, ep: int | None = None, note: str = "") -> None:
    _write(job, drama, ep, {"state": "done", "note": note})


def fail(job: str, drama: str, ep=None, note: str = "") -> None:
    _write(job, drama, ep, {"state": "failed", "note": note})


def touch(job: str, drama: str, ep=None, note: str = "") -> None:
    """只刷新心跳，不动 done/total。长阻塞调用（ffmpeg）期间用。"""
    _write(job, drama, ep, {"state": "running", "note": note})


def heartbeat(job: str, drama: str, ep=None, every: int = 30,
              note: str = "") -> "Callable[[], None]":
    """在长阻塞期间每 every 秒 touch 一次；返回 stop 函数。

    ffmpeg 装配整片可能跑 3–5 分钟，这段时间没有 done/total 更新；
    没有心跳会被页面误判成「疑似中断」。
    """
    stop_evt = threading.Event()

    def run():
        while not stop_evt.wait(every):
            touch(job, drama, ep, note)

    t = threading.Thread(target=run, daemon=True)
    t.start()

    def stop():
        stop_evt.set()
        t.join(timeout=2)   # 等当前这一轮写完，避免 finish 之后又被 touch 回 running

    return stop


def read_all() -> list[dict]:
    """网页侧：把目录里所有进度记录读成一份列表，并算出「疑似中断」。"""
    from datetime import datetime as dt
    out = []
    for p in sorted(dir_of().glob("*.json")) if dir_of().exists() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        d["file"] = p.name
        now = dt.now()
        # 进程还活着吗
        alive = False
        try:
            os.kill(int(d.get("pid") or 0), 0)
            alive = True
        except (OSError, ValueError, TypeError):
            pass
        d["alive"] = alive
        try:
            updated = dt.fromisoformat(d["updated_at"])
            age = (now - updated).total_seconds()
        except (ValueError, TypeError, KeyError):
            updated = None
            age = -1
        d["age_sec"] = round(age)

        # 耗时与 ETA：网页上「跑了多久、还剩多久」比百分比更有用
        try:
            started = dt.fromisoformat(d["started_at"]) if d.get("started_at") else None
        except (ValueError, TypeError):
            started = None
        elapsed = None
        if started:
            end = now if d.get("state") == "running" else (updated or now)
            elapsed = max(0.0, (end - started).total_seconds())
        d["elapsed_sec"] = round(elapsed) if elapsed is not None else None
        total = int(d.get("total") or 0)
        done = int(d.get("done") or 0)
        if d.get("state") == "running" and total > 0 and done > 0 and elapsed:
            d["eta_sec"] = round(elapsed / done * (total - done))
            d["rate"] = round(done / elapsed, 2) if elapsed else None
        else:
            d["eta_sec"] = None

        if d.get("state") == "running" and (not alive or age > 180):
            d["suspect"] = True
        out.append(d)
    return out
