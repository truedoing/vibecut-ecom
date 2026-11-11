#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_index.py —— 台词/镜头 JSON → SQLite 派生缓存

定位（先读这段再用）：
  JSON 是唯一真源，这个库只是缓存，可随时删掉重建（全量重建 < 1 秒）。
  库永不被手工编辑；任何不放心，重跑本脚本一次。
  预留了向量表 vectors（BGE 等语义检索的参数位），当前恒为空——
  触发条件写在 SKILL.md：台词库跨剧超 3000 条、或出现真实的"按语义找段落"需求才启用。

用法：
  python3 build_index.py --drama 以真心守护至亲
  python3 build_index.py --drama 以真心守护至亲 --root /某/assets/short-drama --db /某/index.sqlite
"""
import argparse, glob, json, os, re, sqlite3, sys, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta   (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS lines  (ep INTEGER, t REAL, text TEXT, PRIMARY KEY (ep, t));
CREATE TABLE IF NOT EXISTS shots  (ep INTEGER, start REAL, "end" REAL,
                                   shot_size TEXT, who TEXT, person TEXT,
                                   action TEXT, place TEXT, props TEXT);
CREATE INDEX IF NOT EXISTS idx_shots_ep ON shots(ep, start);
-- 预留：语义检索向量。ref = 'line:ep:t'，emb = 嵌入向量。启用前恒为空。
CREATE TABLE IF NOT EXISTS vectors(ref TEXT PRIMARY KEY, emb BLOB);
"""

def map_person(who_list, chars):
    """镜头 who（特征串列表）→ 人名。每段特征独立映射，多人在场用 / 连接。"""
    names = []
    for feat in (who_list or []):
        hit = None
        for name, cfg in chars.items():
            if any(rule and rule in feat for rule in cfg.get("rules", [])):
                hit = name
                break
        if hit and hit not in names:
            names.append(hit)
    return "/".join(names) if names else "分不清"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drama", required=True)
    ap.add_argument("--root", default=None, help="assets/short-drama 根目录，默认自动找")
    ap.add_argument("--db", default=None, help="库文件路径，默认 <素材目录>/index.sqlite")
    a = ap.parse_args()

    root = a.root or os.path.join(os.getcwd(), "assets", "short-drama")
    base = os.path.join(root, a.drama)
    if not os.path.isdir(base):
        sys.exit(f"找不到素材目录：{base}")
    db = a.db or os.path.join(base, "index.sqlite")

    cm_path = os.path.join(base, "char_map.json")
    chars = json.load(open(cm_path, encoding="utf-8")).get("chars", {}) if os.path.exists(cm_path) else {}

    line_files = sorted(glob.glob(os.path.join(base, "lines", "lines_ep*.json")),
                        key=lambda p: int(re.search(r"(\d+)", os.path.basename(p)).group(1)))
    shot_files = sorted(glob.glob(os.path.join(base, "shots", "shots_ep*.json")),
                        key=lambda p: int(re.search(r"(\d+)", os.path.basename(p)).group(1)))
    if not line_files:
        sys.exit(f"没有台词文件：{base}/lines/")

    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM lines")
    conn.execute("DELETE FROM shots")

    n_lines = n_shots = n_known = 0
    for p in line_files:
        ep = json.load(open(p, encoding="utf-8")).get("ep")
        for ln in json.load(open(p, encoding="utf-8")).get("lines", []):
            conn.execute("INSERT OR REPLACE INTO lines(ep,t,text) VALUES(?,?,?)",
                         (ep, ln["t"], ln["text"]))
            n_lines += 1
    for p in shot_files:
        d = json.load(open(p, encoding="utf-8"))
        ep = d.get("ep")
        for s in d.get("shots", []):
            person = map_person(s["tag"].get("who"), chars)
            if person not in ("分不清",):
                n_known += 1
            conn.execute(
                'INSERT INTO shots(ep,start,"end",shot_size,who,person,action,place,props) '
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (ep, s["start"], s["end"], s["tag"].get("shot_size", ""),
                 "/".join(s["tag"].get("who") or []), person,
                 s["tag"].get("action", ""), s["tag"].get("place", ""),
                 ",".join(s["tag"].get("props") or [])))
            n_shots += 1

    conn.execute("CREATE TABLE IF NOT EXISTS persons(name TEXT PRIMARY KEY, role TEXT, rules TEXT)")
    for name, cfg in chars.items():
        conn.execute("INSERT OR REPLACE INTO persons(name,role,rules) VALUES(?,?,?)",
                     (name, cfg.get("role", ""), ",".join(cfg.get("rules", []))))

    conn.executemany("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", [
        ("drama", a.drama),
        ("built_at", datetime.datetime.now().isoformat(timespec="seconds")),
        ("source", "JSON（唯一真源），本库为派生缓存，可随时重建"),
        ("lines", str(n_lines)), ("shots", str(n_shots)),
    ])
    conn.commit()
    conn.close()
    print(f"已建 {db}")
    print(f"  台词 {n_lines} 条 / 镜头 {n_shots} 个 / 人物可识别率 "
          f"{n_known*100//max(n_shots,1)}%（{n_known}/{n_shots}）")
    print(f"  人物规则：{', '.join(chars)}")

if __name__ == "__main__":
    main()
