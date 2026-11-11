#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
query.py —— 台词/镜头查询入口（查 SQLite 缓存，缓存由 build_index.py 从 JSON 重建）

排清单前找句子、对画面，一条命令出结果，不再手翻 JSON。

用法：
  python3 query.py --drama 以真心守护至亲 --kw 我们没妈,保安          # 关键词（子串，逗号分隔任一命中）
  python3 query.py --drama 以真心守护至亲 --who 许晴 --ep 11          # 限定人物/集数
  python3 query.py --drama 以真心守护至亲 --ep 1 --from 55 --to 80    # 整段导出（配上下文）
  python3 query.py --drama 以真心守护至亲 --kw 合同 --ctx 2           # 前后各带2条
  python3 query.py --drama 以真心守护至亲 --kw 认 --json              # 机器可读
"""
import argparse, json, os, sqlite3, sys

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drama", required=True)
    ap.add_argument("--root", default=None)
    ap.add_argument("--kw", default="", help="关键词，逗号分隔任一命中（子串匹配）")
    ap.add_argument("--who", default="", help="限定画面人物（人物名或'分不清'）")
    ap.add_argument("--ep", type=int, nargs="*", default=None)
    ap.add_argument("--from", dest="t_from", type=float, default=None)
    ap.add_argument("--to", dest="t_to", type=float, default=None)
    ap.add_argument("--ctx", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    root = a.root or os.path.join(os.getcwd(), "assets", "short-drama")
    db = os.path.join(root, a.drama, "index.sqlite")
    if not os.path.exists(db):
        sys.exit(f"缓存不存在：{db}\n先跑 build_index.py --drama {a.drama}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    where, args = [], []
    if a.ep:
        where.append(f"l.ep IN ({','.join('?'*len(a.ep))})")
        args += a.ep
    if a.t_from is not None:
        where.append("l.t >= ?"); args.append(a.t_from)
    if a.t_to is not None:
        where.append("l.t <= ?"); args.append(a.t_to)
    if a.who:
        where.append("s.person LIKE ?"); args.append(f"%{a.who}%")
    if a.kw:
        kws = [k.strip() for k in a.kw.split(",") if k.strip()]
        where.append("(" + " OR ".join(["l.text LIKE ?"] * len(kws)) + ")")
        args += [f"%{k}%" for k in kws]
    sql = ('SELECT l.ep, l.t, l.text, s."end" AS shot_end, s.start AS shot_start, '
           "s.shot_size, s.person, s.action FROM lines l "
           'LEFT JOIN shots s ON s.ep = l.ep AND l.t >= s.start AND l.t < s."end" ')
    if where:
        sql += "WHERE " + " AND ".join(where)
    sql += " ORDER BY l.ep, l.t"
    rows = conn.execute(sql, args).fetchall()

    hits = [dict(r) for r in rows]
    if a.json:
        print(json.dumps({"drama": a.drama, "count": len(hits), "hits": hits},
                         ensure_ascii=False, indent=1))
        return
    if not hits:
        print("没命中。换关键词、放宽集数或时间范围。")
        return
    for h in hits:
        shot = f"[{h['shot_start']:.2f}-{h['shot_end']:.2f} {h['shot_size']}] {h['person']}" \
            if h["shot_start"] is not None else "[无镜头]"
        print(f"ep{h['ep']:02d} {h['t']:6.1f}s  《{h['text']}》  {shot}")
        if a.ctx:
            ctx_rows = conn.execute(
                "SELECT t,text FROM lines WHERE ep=? AND t>? ORDER BY t LIMIT ?",
                (h["ep"], h["t"], a.ctx)).fetchall()
            for c in ctx_rows:
                print(f"        之后 {c['t']:6.1f} {c['text']}")
        print()

if __name__ == "__main__":
    main()
