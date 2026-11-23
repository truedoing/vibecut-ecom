#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按说话人查台词，给微剧本创作当事实底座。

为什么要有：写微剧本要先搞清楚「谁在哪一集受了辱、谁在哪一集翻了身」。
以前这个靠按画面主体猜，一致地一起错（D8/D9 的根因）；现在说话人表按
声纹+人脸定稿了（主角 86.6%），本脚本直接读它的「最终判断」列。

用法：
  python3 who_said.py                          # 列出所有说话人及句数
  python3 who_said.py 许宁                      # 列出许宁的关键台词（带拒绝/反击词优先）
  python3 who_said.py 许宁 --ep 9              # 只看第 9 集
  python3 who_said.py 许宁 --all               # 全部台词，不只关键句
  python3 who_said.py --table /path/to.csv     # 指定说话人表（默认找交付包/工具目录）
"""
import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import data_root as _root  # noqa: E402
ROOT = str(_root())

# 找说话人表：交付包 → 工具目录 → 环境变量
CANDIDATES = [
    os.environ.get("SPEAKER_TABLE", ""),
    os.path.join(ROOT, "tools", "face_label", "说话人表_修正后.csv"),
    os.path.join(ROOT, "tools", "speaker_label", "output", "说话人表.csv"),
]

# 关键句信号：拒绝/顶撞/翻身——这些是微剧本最值钱的台词
KEY = re.compile(
    r"不认|不许|不准|凭什么|你也配|丢不起|没资格|闭嘴|滚|出去|少来|休想|"
    r"我自己|我偏|我就不|轮不到|用不着|我不|绝不|不结了|不要了|"
    r"欢迎|正式|入职|签|赢了|输了|道歉|真相|证|工牌"
)


def find_table(explicit):
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for p in CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    # 新表用「最终判断」，旧表用「who」
    col = "最终判断" if rows and "最终判断" in rows[0] else "who"
    for r in rows:
        r["_who"] = (r.get(col) or "").strip()
        r["_ep"] = int(float(r.get("ep") or 0))
        r["_t"] = float(r.get("t") or 0)
        r["_text"] = (r.get("text") or "").strip()
        r["_doubt"] = (r.get("doubt") or "").strip()
    return rows, col


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("who", nargs="?", help="说话人名；不给就列出所有说话人")
    ap.add_argument("--ep", type=int, help="只看某一集")
    ap.add_argument("--all", action="store_true", help="全部台词，不只关键句")
    ap.add_argument("--table", help="说话人表路径")
    a = ap.parse_args()

    path = find_table(a.table)
    if not path:
        print("找不到说话人表。用 --table 指定，或设 SPEAKER_TABLE 环境变量。")
        sys.exit(2)
    rows, col = load(path)

    if not a.who:
        from collections import Counter
        c = Counter(r["_who"] for r in rows if r["_who"])
        print(f"说话人表：{path}")
        print(f"归属列：{col}    总句数：{len(rows)}\n")
        print("说话人        句数")
        for who, n in c.most_common():
            print(f"{who:<12} {n}")
        return

    sel = [r for r in rows if r["_who"] == a.who]
    if a.ep is not None:
        sel = [r for r in sel if r["_ep"] == a.ep]
    key = [r for r in sel if KEY.search(r["_text"])] if not a.all else sel
    key.sort(key=lambda r: (r["_ep"], r["_t"]))

    print(f"【{a.who}】" + (f" 第{a.ep}集" if a.ep else "") +
          f"：共 {len(sel)} 句，{'全部' if a.all else '关键句'} {len(key)} 条")
    if not a.all:
        print("（要看全部加 --all）\n")
    last_ep = None
    for r in key:
        if r["_ep"] != last_ep:
            print(f"\n── ep{r['_ep']:02d} ──")
            last_ep = r["_ep"]
        flag = " [待听辨]" if r["_doubt"] else ""
        print(f"  {r['_t']:>5.1f}s  {r['_text']}{flag}")


if __name__ == "__main__":
    main()
