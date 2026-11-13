#!/usr/bin/env python3
"""把台词库和镜头标签合成句级表。

一句台词 = 第几秒 + 谁说的 + 什么情绪 + 画面上是什么 + 是不是画外音。

**两条真源都不动**：
  lines/lines_epNN.json  字幕原文（t + text）
  shots/shots_epNN.json  镜头边界与画面标签
本脚本产出的 sentences_epNN.json 是**视图**，随时可全量重建，不作为真源。

兼容两代镜头标签：
  v1（只有 action/who）→ 说话人靠关键词兜底，情绪从 action 抽
  v3+（有 speaking/speaker）→ 说话人用结构化字段，情绪从 action 抽
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# 进度上报给主管的网页看；找不到模块就降级，不影响合表本身。
try:
    sys.path.insert(0, str(ROOT / "skills" / "short-drama-supervisor" / "scripts"))
    import progress
except Exception:
    class _NoProgress:
        def __getattr__(self, _name):
            return lambda *a, **k: None
    progress = _NoProgress()

# --- 情绪：粗分六类，够剪片子用，不追求心理学精度 ---
EMO_TABLE = [
    ("愤怒", r"怒|愤|吼|瞪|骂|斥|厉声|发火|拍桌|指责|质问|凶|不满|抗拒|抵触|强硬|"
             r"顶撞|厌烦|不耐烦|冷嘲|凶巴巴|拒绝|反驳|顶嘴|嫌弃"),
    ("悲伤", r"哭|泪|泣|悲|伤|痛|哽|委屈|绝望|黯然|垂泪|哽咽|难过|伤心|失落|"
             r"自责|懊悔|心酸|苦涩|凄凉"),
    ("震惊", r"震惊|惊讶|愣|呆住|愕|不敢置信|一震|吃惊|错愕|怔"),
    ("恐惧", r"怕|恐|慌|颤|抖|畏惧|惶恐|紧张|不安|瑟缩"),
    ("喜悦", r"笑|喜|欣慰|高兴|开心|温柔|亲切|激动地笑"),
    ("冷淡", r"冷|蔑|讽|不屑|漠然|面无表情|淡然|冷淡|疏离|平静地"),
    ("激动", r"激动|亢奋|失控|情绪起伏|情绪激烈|急躁"),
]
INT_STRONG = re.compile(r"哭喊|嚎|嘶|大喊|吼叫|崩溃|绝望|暴怒|激动地|猛地|大声|尖叫|怒视|拍桌|厉声|哭诉|痛哭")
INT_WEAK = re.compile(r"微微|轻声|默默|静静|含泪|忍着|抿|淡然|平静|迟疑|低头|小声|勉强|淡淡")

# --- 说话人降级兜底（v1 标签用）：从动作里找“谁在说” ---
SPEAK = re.compile(
    r"说话|说：|说道|诉说|哭诉|控诉|倾诉|讲|告诉|问|答|喊|吼|嚷|叫住|叫喊|应声|"
    r"反驳|嘟囔|喃喃|自语|念叨|念|劝|骂|解释|请求|质问|警告|命令|开口|张嘴|"
    r"回应|接过话|打断|补充|低声|大声|哽咽")
NOBODY = re.compile(r"背对镜头|仅见手臂|仅见双手|只见手|背影|看不清|无人|空镜|背对|只有手|半张脸")
PREFIXES = ["老年女性", "中年女性", "年轻女性", "老年男性", "中年男性", "年轻男性",
            "女孩", "男孩", "老人", "妇女", "男子", "女子"]
ALIAS = {"老年女性": ["老年女性", "老人", "老太太"], "中年女性": ["中年女性", "妇女"],
         "年轻女性": ["年轻女性", "女子", "女孩"], "老年男性": ["老年男性", "老人", "老头"],
         "中年男性": ["中年男性", "男子"], "年轻男性": ["年轻男性", "男子", "男孩"]}


def prefix_of(who: str) -> str:
    for p in PREFIXES:
        if who.startswith(p):
            return p
    return who.split()[0] if who.split() else ""


def emotion_of(text: str) -> tuple[str, str]:
    """从描述里抽情绪与强度。返回 (情绪, 强度)；强度 强/中/弱。"""
    for name, pat in EMO_TABLE:
        if re.search(pat, text):
            if INT_STRONG.search(text):
                return name, "强"
            if INT_WEAK.search(text):
                return name, "弱"
            return name, "中"
    if INT_STRONG.search(text):
        return "剧烈", "强"
    return "", ""


def speaker_by_keyword(action: str, whos: list[str]) -> tuple[str, str]:
    """v1 降级：从自由描述里猜谁在说。返回 (人物描述, 依据级别)。"""
    if not SPEAK.search(action):
        return "", "none"
    for w in whos:
        p = prefix_of(w)
        for alias in ALIAS.get(p, [p]):
            for m in re.finditer(re.escape(alias), action):
                if SPEAK.search(action[m.end(): m.end() + 12]):
                    return w, "keyword"
    if len(whos) == 1 and SPEAK.search(action):
        return whos[0], "keyword"
    return "", "none"


def load_char_map(base: Path) -> list[dict]:
    """读剧库里的外观→人名规则表。

    char_map.json 是人工维护的映射，不是模型产物；缺文件时返回空表，全部降级成外观串。
    每条形如 {"name": "许宁", "rules": ["婚纱", "马尾"], "sex": "女"}。
    sex 可选，写了就先按描述里的「男性/女性」过滤，避免「女性」描述命中男性角色。
    """
    f = base / "char_map.json"
    if not f.exists():
        return []
    data = json.loads(f.read_text(encoding="utf-8"))
    out = []
    for name, spec in (data.get("chars") or {}).items():
        rules = spec.get("rules") or []
        if rules:
            out.append({"name": name, "rules": rules, "sex": spec.get("sex", "")})
    return out


def name_of(desc: str, rules: list[dict]) -> str:
    """把「年轻女性 白色T恤 马尾」翻成人名。

    改自 11-12：原来是「命中一个关键词就返回第一个」，模型换个说法就漏（同一集认出的人
    从 19 句掉到 12 句）。现在按命中的关键词个数打分，取得分最高的；并列时保持表内顺序。
    """
    if not desc:
        return ""
    desc_sex = "男" if "男性" in desc or "男子" in desc else ("女" if "女性" in desc or "女子" in desc else "")
    best, best_score = "", 0
    for c in rules:
        if c["sex"] and desc_sex and c["sex"] != desc_sex:
            continue
        score = sum(1 for k in c["rules"] if k in desc)
        if score > best_score:
            best, best_score = c["name"], score
    return best


def has_speaker(shot: dict) -> bool:
    """这个镜头里有没有人在动嘴。新版看 speaking 字段，旧版看动作关键词。"""
    tag = shot.get("tag", {}) or {}
    if "speaking" in tag:
        return tag.get("speaking") is True
    whos = [w for w in (tag.get("who") or []) if not NOBODY.search(w)]
    return bool(speaker_by_keyword(tag.get("action", ""), whos)[0])


def shot_by_coverage(i: int, lines: list[dict], shots: list[dict],
                     duration: float | None) -> tuple[dict | None, bool]:
    """取这句台词该归哪个镜头，返回 (镜头, 是否跨了边界)。

    11-12 定的规则（前两版都栽过，写下来别再改回去）：
      ① 先看台词的**起点**落在哪个镜头 —— 字幕出现那一刻就在说话的人，最可信。
      ② 只有起点那个镜头里**没人动嘴**时，才往后找覆盖更长的镜头。
         ep15 33.0s「不是舍不得他」起点镜头是许宁低头流泪（没说话），主体落在下一镜头才对。
      ③ 绝不无条件取「覆盖更长」的那个：17.0s「你别来求」下一句隔了 5 秒，区间被拉长，
         覆盖算法会把它推到后面走路的背影镜头上，而实际上它就在起点镜头里说的。

    跨了边界的一律返回 True，写进表里标出来，交人核。
    """
    t0 = lines[i]["t"]
    t1 = lines[i + 1]["t"] if i + 1 < len(lines) else (duration or t0 + 3.0)
    if t1 <= t0:
        t1 = t0 + 1.0

    start = next((s for s in shots if s["start"] <= t0 < s["end"]), None)
    cover, cover_ov = None, 0.0
    for s in shots:
        ov = min(t1, s["end"]) - max(t0, s["start"])
        if ov > cover_ov:
            cover, cover_ov = s, ov

    if start is None:                       # 起点落在镜头之外（集尾留白）
        return cover, cover is not None
    crossed = cover is not None and cover is not start
    if has_speaker(start):
        # ④ 字幕比画面早闪：句子起点贴着镜头结尾（<0.6 秒），而下一个镜头里确实有人在说话，
        #    那就是字幕先出来、声音在下一镜头。ep15 3.0s「晴晴你姐姐让你住」实测就是这种：
        #    这句记在 3.0 秒，可 4.2 秒的画面上是老人正脸在说，年轻人那张脸已经切走了。
        #    17.0s「你别来求」不受影响——它下一个镜头是两人走路的背影，没人动嘴。
        idx = shots.index(start)
        nxt = shots[idx + 1] if idx + 1 < len(shots) else None
        if nxt and (start["end"] - t0) < 0.6 and has_speaker(nxt):
            return nxt, True
        return start, crossed
    if cover is not None and cover is not start and has_speaker(cover):
        return cover, True
    return start, crossed


def build_ep(lines: list[dict], shots: list[dict],
             rules: list[dict] | None = None,
             duration: float | None = None) -> list[dict]:
    rows = []
    for i, l in enumerate(lines):
        s, crossed = shot_by_coverage(i, lines, shots, duration)
        row = {"t": l["t"], "text": l["text"], "shot": None, "who": "", "name": "",
               "kind": "unknown", "emotion": "", "intensity": "",
               "size": "", "evidence": "", "cross": crossed, "_shot_id": None}
        if not s:
            rows.append(row)
            continue
        tag = s.get("tag", {})
        whos = [w for w in (tag.get("who") or []) if not NOBODY.search(w)]
        row["shot"] = [s["start"], s["end"]]
        row["size"] = tag.get("shot_size", "")
        row["_shot_id"] = f"{s['start']}-{s['end']}"
        row["evidence"] = tag.get("mouth_note", "") or tag.get("action", "")

        # v5 标签：模型直接给了 emotion/intensity，优先用它；老标签才回退正则猜
        structured = (tag.get("emotion") or "").strip()
        if structured and structured not in ("无", "未识别"):
            row["emotion"] = structured
            row["intensity"] = (tag.get("intensity") or "").strip()
        else:
            emo, inten = emotion_of(" ".join([tag.get("action", "")] + (tag.get("who") or [])))
            row["emotion"], row["intensity"] = emo, inten

        # 新版标签：结构化字段优先
        if "speaking" in tag:
            if not tag.get("speaking"):
                row["kind"] = "off_screen"      # 画面里没人动嘴 → 画外音
            else:
                sp = str(tag.get("speaker", "none"))
                idx = int(sp) - 1 if sp.isdigit() else -1
                if 0 <= idx < len(tag.get("who") or []):
                    row["who"] = (tag.get("who") or [])[idx]
                    row["kind"] = "on_screen"
                else:
                    row["who"] = " / ".join(whos)[:24]
                    row["kind"] = "on_screen" if whos else "unknown"
            row["name"] = name_of(row["who"], rules or [])
            rows.append(row)
            continue

        # 旧版标签：关键词兜底
        who, how = speaker_by_keyword(tag.get("action", ""), whos)
        if tag.get("people", 0) == 0 or not whos:
            row["kind"] = "off_screen"
        elif who:
            row["who"], row["kind"] = who, "on_screen"
        else:
            row["kind"] = "unknown"
        row["name"] = name_of(row["who"], rules or [])
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drama", required=True)
    ap.add_argument("--ep", type=int, nargs="*")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    base = ROOT / "assets" / "short-drama" / args.drama
    out_dir = Path(args.out) if args.out else base / "sentences"
    out_dir.mkdir(parents=True, exist_ok=True)

    rules = load_char_map(base)
    print(f"外观→人名规则：{len(rules)} 个人物"
          + ("" if rules else "（缺 char_map.json，只能留外观串）"))

    eps = args.ep or sorted(int(re.search(r"ep(\d+)", p.name).group(1))
                            for p in (base / "lines").glob("lines_ep*.json"))

    total_eps = len(eps)
    done_eps = 0
    progress.start("build_sentences", args.drama, None, total=total_eps, unit="集")

    total = {"on_screen": 0, "off_screen": 0, "unknown": 0}
    named = 0
    suspect = 0
    crossed = 0
    dense_all: list[dict] = []
    emo_stat: dict[str, int] = {}
    multi_shot: dict[str, set] = {}

    for ep in eps:
        lf = base / "lines" / f"lines_ep{ep:02d}.json"
        sf = base / "shots" / f"shots_ep{ep:02d}.json"
        if not (lf.exists() and sf.exists()):
            print(f"ep{ep:02d} 跳过：缺台词库或镜头标签")
            done_eps += 1
            progress.update("build_sentences", args.drama, None,
                            done=done_eps, total=total_eps,
                            note=f"跳过 {done_eps}/{total_eps} 集")
            continue
        lines = json.load(open(lf, encoding="utf-8"))["lines"]
        shots_j = json.load(open(sf, encoding="utf-8"))
        shots = shots_j["shots"]
        dur = json.load(open(lf, encoding="utf-8")).get("duration")
        rows = build_ep(lines, shots, rules, dur)
        crossed += sum(1 for r in rows if r.get("cross"))

        # 串台检测：相邻两个镜头的人物描述一字不差，多半是批量送图时把上一组抄了过来
        for i in range(1, len(shots)):
            if shots[i].get("tag", {}).get("who") == shots[i - 1].get("tag", {}).get("who"):
                suspect += 1

        for r in rows:
            total[r["kind"]] += 1
            if r["name"]:
                named += 1
            emo_stat[r["emotion"]] = emo_stat.get(r["emotion"], 0) + 1
            if r["_shot_id"]:
                multi_shot.setdefault(r["_shot_id"], set()).add(r["who"] or r["kind"])

        # 一镜多句：正常一句一镜，一个镜头里塞进 4 句以上就要怀疑镜头切分漏了一刀。
        # ep15 23.6-29.1 实测：24.5 秒还是周德海的手递钥匙，26 秒已经是林秀兰正脸，
        # 两段被合成一个镜头，于是「难受就跟妈说」被判给了周德海。
        per_shot: dict[str, int] = {}
        for r in rows:
            if r["_shot_id"]:
                per_shot[r["_shot_id"]] = per_shot.get(r["_shot_id"], 0) + 1
        dense = [{"shot": k, "lines": v} for k, v in per_shot.items() if v >= 4]
        dense_ep = [d for d in dense]
        dense_all.extend(dense_ep)

        payload = {
            "ep": ep,
            "drama": args.drama,
            "sources": {"lines": f"lines/lines_ep{ep:02d}.json",
                        "shots": f"shots/shots_ep{ep:02d}.json",
                        "char_map": "char_map.json" if rules else None,
                        "shots_prompt": shots_j.get("prompt_version")},
            "counts": {"lines": len(rows),
                       "on_screen": sum(1 for r in rows if r["kind"] == "on_screen"),
                       "off_screen": sum(1 for r in rows if r["kind"] == "off_screen"),
                       "unknown": sum(1 for r in rows if r["kind"] == "unknown"),
                       "named": sum(1 for r in rows if r["name"])},
            "dense_shots": dense_ep,
            "rows": [{k: v for k, v in r.items() if k != "_shot_id"} for r in rows],
        }
        (out_dir / f"sentences_ep{ep:02d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        done_eps += 1
        progress.update("build_sentences", args.drama, None,
                        done=done_eps, total=total_eps,
                        note=f"已对齐 {done_eps}/{total_eps} 集")

    n = sum(total.values())
    progress.finish("build_sentences", args.drama, None, note=f"{n} 句")
    print(f"\n=== {args.drama} 共 {n} 句 → {out_dir} ===")
    for k, label in [("on_screen", "画面内说话"), ("off_screen", "画外音"),
                     ("unknown", "定不了")]:
        print(f"  {label:<8} {total[k]:>4} 句  {total[k]/max(n,1)*100:>5.1f}%")
    print(f"  {'查到人名':<8} {named:>4} 句  {named/max(n,1)*100:>5.1f}%"
          f"   （其余只有外观描述，规则表里没匹配上）")
    print("\n  情绪分布:", "  ".join(f"{k}{v}" for k, v in
                                 sorted(emo_stat.items(), key=lambda x: -x[1])))
    if crossed:
        print(f"\n  跨镜头台词 {crossed} 句（起点和主体不在同一镜头，已按起点优先算，标了 cross）")
    if dense_all:
        print(f"\n  一镜多句的镜头 {len(dense_all)} 个（一个镜头塞了 4 句以上，可能漏切了一刀，需人核）：")
        for d in dense_all[:6]:
            print(f"    {d['shot']} 秒  {d['lines']} 句")
    if suspect:
        print(f"\n  提示：{suspect} 处相邻镜头的人物描述一字不差，可能是一批送图时抄了上一组，需人核")
    risky = {k: v for k, v in multi_shot.items() if len(v) > 1}
    if risky:
        print(f"\n  注意：{len(risky)} 个镜头里出现多个不同说话人（同一镜头多句只能归一人，需人核）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
