#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把全剧台词压成一张「冲突地图」。

为什么要有这个：一轮三条片子，最花时间的不是排清单，是「读完全剧、想清楚这部剧能切几个角度」。
上一轮实测这一项 1 分 35 秒，占整轮四分之一；10 条时它还要涨，因为要 10 个不重复的角度。
而读台词这件事本身有 80% 是重复劳动——每一集都在找「谁跟谁在争什么、最狠的是哪一句、最后怎么收」。

这张表就是那 80% 的答案：每集给出
  · 谁在说话（按剧包人物表统计，看出这一集是谁的场）
  · 最狠的几句（按拒绝/顶撞词打分，不是按我的印象）
  · 这一集最后一句（钩子，也是天然的段落出点）

它不给答案，给的是「选角度的素材」。看到「ep9 全是周家在赶人、ep12 全是妹妹在拿房」，
角度自己就浮出来了：站在谁的眼睛看。

用法：
  python3 storymap.py 以真心守护至亲                 # 1-17 集（默认避开泄底集）
  python3 storymap.py 以真心守护至亲 --to 20
  python3 storymap.py 以真心守护至亲 --out runs/以真心守护至亲/冲突地图.md
"""
import argparse
import json
import os
import re
import sys

# 数据目录只认 scripts/_env.py 一个出口（11-16 统一）：技能装进 ~/.workbuddy/skills
# 或专家包之后，从 __file__ 往上数 parents 会一路翻到 C:\，永远碰不到工作台。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _env import data_root as _data_root  # noqa: E402

ROOT = str(_data_root())

# 有人对主角说「不」——这类句子是预告片最值钱的东西，因为它是「冲突还没解决」的证据。
# 刻意不用裸「别」：中文里「别慌」「别累着」「别硬撑」是关心，不是拒绝。
# 上一版拿裸「别」当信号，结果把「你别慌慢一点就好」排成了最狠的一句——那是全剧最暖的一句。
CARE = re.compile(r"别慌|慢点|慢一点|小心|别累|注意身体|早点|冷不冷|饿不饿|别硬撑|别为难")
REJECT = re.compile(r"别(想|来|管|拿|耽误|说|问|碰|提|找|惦记)|不许|不准|滚|凭什么|你也配|"
                    r"丢不起|没资格|闭嘴|出去|少来|休想|做梦|不认|轮不到|不配|不能留")
# 主角顶回去
FIGHT = re.compile(r"我偏|我就要|谁说|我告诉你|你试试|我凭什么|轮不到我|用不着|我自己|我不欠|"
                   r"我不|绝不|一样也不能")


def pack_of(drama):
    p = os.path.join(ROOT, "packs", "short-drama", drama, "drama-pack.json")
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def lines_of(drama, ep):
    p = os.path.join(ROOT, "assets", "short-drama", drama, "lines", f"lines_ep{ep:02d}.json")
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def last_ep(drama):
    """这个剧的台词库到第几集。默认范围不能再写死 17——那是《八零憨妻》的值，
    第二部剧一来就只读前 17 集，后面的集数当不存在（11-16 修）。"""
    d = os.path.join(ROOT, "assets", "short-drama", drama, "lines")
    eps = []
    if os.path.isdir(d):
        for f in os.listdir(d):
            m = re.match(r"lines_ep(\d+)\.json$", f)
            if m:
                eps.append(int(m.group(1)))
    if not eps:
        raise SystemExit(f"{drama} 还没有台词库（lines/lines_epNN.json），先跑台词抽取")
    return max(eps)


def names_of(pack):
    out = []
    for c in pack.get("characters", []) or []:
        n = c.get("name")
        if n and len(n) >= 2:
            out.append(n)
    return out


def score(txt):
    if CARE.search(txt):
        return -2.0            # 关心的话不是冲突，别混进「最狠三句」
    s = 0.0
    if REJECT.search(txt):
        s += 2.0
    if FIGHT.search(txt):
        s += 2.0
    if 4 <= len(txt) <= 22:
        s += 1.0
    elif len(txt) < 5:
        s -= 0.5              # 「妈伸手一挡」这种碎片是转录切碎的，不是一句台词
    if txt.endswith(("？", "?", "！", "!")):
        s += 0.8
    return s


def heat(ep_doc, names):
    """这一集的几个数：谁说得多、有多少句是「不」、平均句长。"""
    ls = ep_doc["lines"]
    who = {}
    for l in ls:
        for n in names:
            if n in l["text"]:
                who[n] = who.get(n, 0) + 1
    reject = sum(1 for l in ls if REJECT.search(l["text"]) and not CARE.search(l["text"]))
    fight = sum(1 for l in ls if FIGHT.search(l["text"]) and not CARE.search(l["text"]))
    return who, reject, fight


def build(drama, ep_from, ep_to, topn=3):
    pack = pack_of(drama)
    names = names_of(pack)
    arc = {a.get("no"): a for a in (pack.get("story_arc", {}) or {}).get("acts", [])}

    rows, role_tot = [], {}
    for ep in range(ep_from, ep_to + 1):
        doc = lines_of(drama, ep)
        if not doc:
            continue
        ls = doc["lines"]
        if not ls:
            continue
        who, rej, fgt = heat(doc, names)
        for n, c in who.items():
            role_tot[n] = role_tot.get(n, 0) + c
        ranked = sorted(ls, key=lambda l: -score(l["text"]))
        tops = [l for l in ranked[:topn]]
        rows.append({
            "ep": ep,
            "dur": doc.get("duration", 0),
            "n": len(ls),
            "who": sorted(who.items(), key=lambda x: -x[1])[:3],
            "rej": rej, "fgt": fgt,
            "tops": tops,
            "top_score": round(score(ranked[0]["text"]), 1) if ranked else 0,
            "last": ls[-1],
            "first": ls[0],
        })
    return rows, role_tot, arc


def render(drama, rows, role_tot, arc, ep_from, ep_to):
    tot = sum(r["dur"] for r in rows)
    out = [f"# {drama} 冲突地图（ep{ep_from}–ep{ep_to}）", ""]
    out.append(f"可切点素材 {len(rows)} 集 / {tot:.0f}s = {tot/60:.1f} 分钟。"
               f"下面每一行都是一个「可以站进去的眼睛」。")
    out.append("")

    if role_tot:
        top = sorted(role_tot.items(), key=lambda x: -x[1])[:8]
        out.append("## 谁在撑这部剧")
        out.append("")
        out.append("| 角色 | 被提到次数 | 暗示 |")
        out.append("|---|---|---|")
        for n, c in top:
            hint = "主场，适合「她的视角」" if c > max(role_tot.values()) * 0.5 else ""
            out.append(f"| {n} | {c} | {hint} |")
        out.append("")
        out.append("> 换个角度看：出现最多的那个是主角（顺叙视角），"
                   "**第二多的往往才是最好的角度**——因为观众对她好奇但看不透。")
        out.append("")

    if arc:
        out.append("## 故事弧（剧包里定的幕）")
        out.append("")
        for no in sorted(arc):
            a = arc[no]
            out.append(f"- 第{no}幕 **{a.get('name','')}**（ep{a.get('ep_from')}–{a.get('ep_to')}）："
                       f"{a.get('summary','')}")
        out.append("")

    out.append("## 每集：谁在争、最狠哪句、怎么收")
    out.append("")
    out.append("> 「拒绝」＝这一集有几句在把主角往外推；「反击」＝主角顶回去几次。"
               "两个数都只看字面，不带上下文，用来找**冲突最密的那几集**，不当结论。"
               "「被点名」是角色名在台词里被直呼的次数——台词库没有说话人标注，"
               "所以这一列只能看个大概，别当成「谁在场」。")
    out.append("")
    out.append("| 集 | 秒 | 句 | 被点名 | 拒绝 | 反击 | 最狠三句（按字面冲突打分） | 收在哪 |")
    out.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        who = "、".join(f"{n}×{c}" for n, c in r["who"]) or "—"
        if r["top_score"] < 1.5:
            # 字面冲突弱：可能是过场集，也可能是「质问型」冲突（台词里没有拒绝词，
            # 比如「谁让她坐这的」）。这里只提醒，不当判死。
            tops = "（字面冲突弱，多半是过场；也可能只是没用到拒绝词，得回台词看）"
        else:
            tops = "<br>".join(f"「{l['text'][:26]}」（{l['t']:.0f}s）" for l in r["tops"])
        out.append(f"| ep{r['ep']} | {r['dur']:.0f} | {r['n']} | {who} | "
                   f"{r['rej']} | {r['fgt']} | {tops} | 「{r['last']['text'][:22]}」 |")
    out.append("")

    # 给几条「角度骨架」，不是成品选题——角度最后还得人定
    out.append("## 可以站的角度（骨架，不是选题）")
    out.append("")
    hot = sorted(rows, key=lambda r: -(r["rej"] + r["fgt"]))[:4]
    for r in hot:
        lead = r["who"][0][0] if r["who"] else "主角"
        out.append(f"- **ep{r['ep']} 的场（{r['rej']} 句拒绝 / {r['fgt']} 句反击）**："
                   f"围绕「{r['tops'][0]['text'][:20]}」，"
                   f"可以站在 {lead} 的对面看，也可以站在 {lead} 里面看")
    out.append("")
    out.append("> 三条片子的角度必须**答不同的问题**，不是换个人称重讲一遍。")
    out.append("> 判据：把三条的一句话简介并排放，如果两句话的主语换了、谓语一样，就是同一条。")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("drama")
    ap.add_argument("--from", dest="ep_from", type=int, default=1)
    ap.add_argument("--to", dest="ep_to", type=int, default=None,
                    help="到第几集（默认取该剧台词库的最后一集；不再写死 17）")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--out")
    args = ap.parse_args()

    if args.ep_to is None:
        args.ep_to = last_ep(args.drama)

    rows, role_tot, arc = build(args.drama, args.ep_from, args.ep_to, args.top)
    if not rows:
        raise SystemExit(f"{args.drama} 在 ep{args.ep_from}–{args.ep_to} 没有台词库")
    md = render(args.drama, rows, role_tot, arc, args.ep_from, args.ep_to)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        open(args.out, "w", encoding="utf-8").write(md)
        print(f"写出 {os.path.relpath(args.out, ROOT)}（{len(rows)} 集）")
    else:
        print(md)


if __name__ == "__main__":
    main()
