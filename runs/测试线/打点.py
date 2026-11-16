#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轮次打点：记每轮测试各环节的真实耗时。

为什么单独做一个脚本：靠人记时间一定会漏，最后只剩"感觉挺快"。
这个脚本只做一件事——你说开始，它记下时刻；你说下一个环节，它算出差值。

用法：
    python3 runs/测试线/打点.py start 轮01 以真心守护至亲
    python3 runs/测试线/打点.py mark 选题
    python3 runs/测试线/打点.py mark 排清单_T9004
    python3 runs/测试线/打点.py show
    python3 runs/测试线/打点.py history

数据落在同目录 轮次.json，结构：
    {"rounds": [{"id": "轮01", "drama": "...", "start": "...", "end": "...",
                 "marks": [{"name": "选题", "t": "...", "dur": 123.4}]}]}
"""
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "轮次.json")


def now():
    return datetime.now()


def load():
    if not os.path.exists(STORE):
        return {"rounds": []}
    try:
        with open(STORE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"rounds": []}


def save(d):
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE)  # 原子写：中途断了不会写坏


def cur(d):
    if not d["rounds"]:
        print("还没有轮次，先 start 一轮")
        sys.exit(2)
    return d["rounds"][-1]


def fmt(sec):
    sec = float(sec)
    if sec < 60:
        return f"{sec:.0f}秒"
    m, s = divmod(int(sec), 60)
    return f"{m}分{s:02d}秒"


def cmd_start(args):
    if len(args) < 2:
        print("用法: start <轮次名> <剧名>")
        sys.exit(2)
    rid, drama = args[0], args[1]
    d = load()
    for r in d["rounds"]:
        if r["id"] == rid:
            print(f"{rid} 已存在，换一个名字")
            sys.exit(1)
    t = now()
    d["rounds"].append({
        "id": rid, "drama": drama,
        "start": t.isoformat(timespec="seconds"),
        "end": None, "marks": [],
    })
    save(d)
    print(f"[{t:%H:%M:%S}] 开始 {rid}（{drama}）")


def cmd_mark(args):
    if not args:
        print("用法: mark <环节名>")
        sys.exit(2)
    name = " ".join(args)
    d = load()
    r = cur(d)
    t = now()
    base = datetime.fromisoformat(r["marks"][-1]["t"]) if r["marks"] else \
        datetime.fromisoformat(r["start"])
    dur = (t - base).total_seconds()
    r["marks"].append({"name": name, "t": t.isoformat(timespec="seconds"), "dur": round(dur, 1)})
    save(d)
    total = (t - datetime.fromisoformat(r["start"])).total_seconds()
    print(f"[{t:%H:%M:%S}] {name}: 本环节 {fmt(dur)}  累计 {fmt(total)}")


def cmd_end(args):
    d = load()
    r = cur(d)
    t = now()
    r["end"] = t.isoformat(timespec="seconds")
    save(d)
    total = (t - datetime.fromisoformat(r["start"])).total_seconds()
    print(f"[{t:%H:%M:%S}] {r['id']} 结束，总耗时 {fmt(total)}")


def cmd_show(args):
    d = load()
    r = cur(d)
    t0 = datetime.fromisoformat(r["start"])
    t1 = datetime.fromisoformat(r["end"]) if r["end"] else now()
    print(f"{r['id']}｜{r['drama']}｜{t0:%m-%d %H:%M:%S} 起，"
          f"{'已结束' if r['end'] else '进行中'}")
    print("-" * 52)
    for m in r["marks"]:
        print(f"  {m['name']:<22} {fmt(m['dur']):>10}   → {m['t'][11:19]}")
    print("-" * 52)
    print(f"  {'合计':<22} {fmt((t1 - t0).total_seconds()):>10}")


def cmd_history(args):
    d = load()
    if not d["rounds"]:
        print("还没有轮次")
        return
    for r in d["rounds"]:
        t0 = datetime.fromisoformat(r["start"])
        t1 = datetime.fromisoformat(r["end"]) if r["end"] else now()
        tot = (t1 - t0).total_seconds()
        print(f"{r['id']:<8} {r['drama']:<16} {t0:%m-%d %H:%M}  "
              f"{fmt(tot):>8}  {len(r['marks'])} 个环节"
              f"{'' if r['end'] else '  (进行中)'}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd, args = sys.argv[1], sys.argv[2:]
    table = {"start": cmd_start, "mark": cmd_mark, "end": cmd_end,
             "show": cmd_show, "history": cmd_history}
    fn = table.get(cmd)
    if not fn:
        print(f"不认识的命令: {cmd}")
        print(__doc__)
        sys.exit(2)
    fn(args)


if __name__ == "__main__":
    main()
