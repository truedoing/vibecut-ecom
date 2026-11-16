#!/usr/bin/env python3
"""测试线算账：读 runs/测试线/台账.csv，算一次过率。

用法：python3 runs/测试线/算账.py
"""
import csv
import os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "台账.csv")

OK = "通过"


def to_num(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_minutes(s):
    """把 HH:MM 换成分钟数，用来兜底算耗时。"""
    s = (s or "").strip()
    if ":" not in s:
        return None
    parts = s.split(":")
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return None


def load_rows():
    if not os.path.exists(CSV_PATH):
        print(f"找不到台账：{CSV_PATH}")
        return []
    rows = []
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        for raw in csv.DictReader(f):
            if not (raw.get("编号") or "").strip():
                continue
            rows.append({(k or "").strip(): (v or "").strip() for k, v in raw.items()})
    return rows


def main():
    rows = load_rows()
    if not rows:
        print("台账还是空的。开跑第一条再来看。")
        return 0

    by_id = defaultdict(list)
    for r in rows:
        by_id[r["编号"]].append(r)

    total = len(by_id)

    # 每条片子的首版 = 版本号最小的那一行
    first_pass = []
    rounds = {}
    for tid, rs in by_id.items():
        rs = sorted(rs, key=lambda r: to_num(r.get("版本")) or 1)
        rounds[tid] = len(rs)
        if rs[0].get("判定") == OK:
            first_pass.append(tid)

    rate = len(first_pass) / total * 100

    # 整批过率：一批里所有编号都首版通过才算
    batches = defaultdict(set)
    for r in rows:
        b = r.get("批次")
        if b:
            batches[b].add(r["编号"])
    batch_ok = sum(1 for ids in batches.values() if ids and ids <= set(first_pass))

    # 人审耗时（谁判=人）
    mins = []
    for r in rows:
        if r.get("谁判") != "人":
            continue
        m = to_num(r.get("人审分钟"))
        if m is None:
            a, b = to_minutes(r.get("开始审")), to_minutes(r.get("审完"))
            if a is not None and b is not None and b >= a:
                m = b - a
        if m is not None:
            mins.append(m)

    # 机器预检拦截：只被机器拦过、从没到人的编号
    machine_ids = {r["编号"] for r in rows if r.get("谁判") == "机器"}
    human_ids = {r["编号"] for r in rows if r.get("谁判") == "人"}
    only_machine = machine_ids - human_ids

    # 返工类型分布
    fail_types = Counter()
    for r in rows:
        if r.get("判定") == OK:
            continue
        t = r.get("返工类型", "")
        if not t:
            continue
        for x in t.replace("、", "/").replace("，", "/").replace(",", "/").split("/"):
            x = x.strip()
            if x:
                fail_types[x] += 1

    # 流程卡点
    blockers = [(r["编号"], r["流程卡点"]) for r in rows if r.get("流程卡点")]

    print("=" * 46)
    print("测试线算账")
    print("=" * 46)
    print(f"进审条数        {total}")
    print(f"首版就通过      {len(first_pass)}  →  {', '.join(sorted(first_pass)) or '无'}")
    print(f"单条一次过率    {rate:.1f}%")
    print(f"平均审核轮次    {sum(rounds.values()) / total:.1f} 轮/条")
    if batches:
        print(f"整批过率        {batch_ok}/{len(batches)} 批")
        for b, ids in sorted(batches.items()):
            tag = "" if len(ids) >= 3 else f"（只有 {len(ids)} 条，批次不完整）"
            print(f"  {b}: {len(ids)} 条 {tag}")
    if mins:
        print(f"平均人审        {sum(mins) / len(mins):.1f} 分钟/版")
    if machine_ids:
        print(f"机器预检拦截    {len(only_machine)}/{total} 条"
              f"（{len(only_machine) / total * 100:.0f}%，没到人手上）")
    if fail_types:
        print("\n返工类型分布")
        for t, c in fail_types.most_common():
            print(f"  {t:<6} {c}")
    if blockers:
        print("\n流程卡点（这些就是写代码的待办）")
        for tid, b in blockers:
            print(f"  {tid}: {b}")

    print("\n判断（门槛事先定在测法文档第七节）")
    if rate >= 60:
        print("  ≥60%：20 条/天有戏，可以直接上并发和 UI。")
    elif rate >= 40:
        print("  40–60%：勉强。先把人审压到 3 分钟内，同时收紧选题。")
    else:
        print("  <40%：20 条/天不成立。停下来改选题和文案，别碰并发。")

    if total < 9:
        print(f"\n注意：只有 {total} 条，百分比误差很大。累积到 9–12 条再拿它下结论。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
