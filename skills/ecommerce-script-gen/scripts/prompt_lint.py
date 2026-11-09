#!/usr/bin/env python3
"""出片前的提示词体检。花那 3 元之前先跑这个。

用法：
  python3 prompt_lint.py --prompt-file p.txt --duration 15 --reverse-word 还真香
  python3 prompt_lint.py --json 文案.json            # 自动取 ai_shots[0].prompt
  python3 prompt_lint.py --json 文案.json --strict   # WARN 也算不通过

退出码：有 ERROR → 1；--strict 下有 WARN → 1；否则 0。

检查分三级：
  E  硬错误，改之前不许提交
  W  会掉分，值得改但不一定改
  I  仅提示统计数字，不下结论（情绪类机器判不准）
"""

import argparse
import io
import json
import re
import sys

SHOT_RE = re.compile(
    r"镜头\s*(\d+)\s*[（(]\s*([\d.]+)\s*秒?\s*[–—\-~至到]\s*([\d.]+)\s*秒?\s*[）)]\s*[：:]"
)
LINE_RE = re.compile(r"\{([^{}]*)\}")
CONCRETE = re.compile(r"生气|愤怒|高兴|开心|惊讶|吃惊|无奈|得意|温柔|激动|紧张|委屈|尴尬")
EXTERNAL = re.compile(
    r"音量|嗓门|压低|提高|喊|吵|咬重|一字一顿|上扬|垮下来|声调|语气|吼|嘟囔|含糊"
)
ACTION = re.compile(
    r"按|摔|转|指|瞪|端|拎|抓|放|塞|站|坐|咬|擦|笑|拧|耸|垮|停|走|抬|捂|抱|倒|递|推|拉|凑|探"
)
CAMERA_MOVE = re.compile(r"推近|推镜|拉远|摇镜|横移|跟拍|升降|环绕|变焦|手持晃动|甩镜")
UNEXECUTABLE = re.compile(r"表现得|显得很|看起来很|呈现出.{0,6}(感觉|情绪|氛围)|让人感觉")
FILLER_HEAD = re.compile(r"^[\s「『\"“]*[嗯啊哎哦噢唔嘿诶唉]\s*[，,、。…]")
PUNCT = re.compile(r"[，。；：！？—…、,.!?;:\s\"'“”‘’（）()《》【】]")


def net_len(text):
    return len(PUNCT.sub("", text))


def read_prompt(args):
    """返回 (提示词文本, 整份文案 doc 或 None)"""
    if args.prompt_file:
        return io.open(args.prompt_file, encoding="utf-8").read(), None
    d = json.load(io.open(args.json, encoding="utf-8"))
    shots = d.get("ai_shots") or []
    if not shots:
        sys.exit("✗ 这份文案里没有 ai_shots")
    p = (shots[0].get("prompt") or "").strip()
    if not p:
        sys.exit("✗ ai_shots[0].prompt 是空的（prompts_pending: true？先写提示词）")
    return p, d


def parse_shots(text):
    marks = list(SHOT_RE.finditer(text))
    shots = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end]
        shots.append(
            {
                "idx": int(m.group(1)),
                "start": float(m.group(2)),
                "end": float(m.group(3)),
                "body": body.strip(),
                "lines": [x.strip() for x in LINE_RE.findall(body)],
            }
        )
    return shots


PRODUCT_MENTION = re.compile(r"产品|罐|瓶|包装|酱料|标签")
TEXT_IN_VIDEO = re.compile(r"文字|字幕|标语|价格牌|大字|弹出字")


def check_doc(doc, E, W):
    """JSON 层检查（--json 时才跑）：结构模式 + 产品参考图 + 文字特效。"""
    if not doc:
        return
    for sh in doc.get("ai_shots") or []:
        p = sh.get("prompt") or ""
        sid = sh.get("shot_id", "?")
        if PRODUCT_MENTION.search(p) and not sh.get("product_ref"):
            E(
                f"{sid} 提示词提到产品但没挂 product_ref——"
                f"AI 自由发挥的产品＝别人的产品（标签必画错），"
                f"必须指向 assets/{{品类}}/product_refs/ 主参考图"
            )
        if TEXT_IN_VIDEO.search(p):
            W(
                f"{sid} 提示词里要求 AI 生成文字——出片后第一件事查错字"
                f"（只许短数字/短语 ≤2 处，长文案仍走后期叠）"
            )
        if (sh.get("character_ref") is None) and (not sh.get("product_ref")):
            E(f"{sid} character_ref 与 product_ref 都是空——这条镜头没有任何参考锚，画面必漂")
    if doc.get("video_structure") == "hook_plus_convert":
        tail = (doc.get("ai_shots") or [])[-1]
        d = tail.get("duration_sec") or 0
        if not (6 <= d <= 10):
            W(f"尾段副视频 {tail.get('shot_id')} 时长 {d}s，建议 8–10s（转化段够用就行，秒数就是钱）")
        covers = tail.get("covers") or []
        segs = doc.get("segments") or []
        last_ai = [s for s in segs if s.get("shot_source") == "AI" and s.get("seq") not in
                   ([c for c in covers] or [])]
        if covers and covers[0] <= (segs[-2]["seq"] if len(segs) >= 2 else 0):
            pass  # 覆盖倒数几句由写作时定，这里不硬卡
        tail_roles = [s.get("section_role") for s in segs if s.get("shot_source") == "AI"
                      and s.get("seq") in covers]
        if tail_roles and not any(r in ("convert", "cta", "close") for r in tail_roles):
            W(f"尾段副视频覆盖段 role={tail_roles}，不含转化/逼单段——副视频存在的意义就是逼单")


def run(args):
    text, doc = read_prompt(args)
    shots = parse_shots(text)
    errs, warns, infos = [], [], []

    def E(m):
        errs.append(m)

    def W(m):
        warns.append(m)

    def I(m):
        infos.append(m)

    check_doc(doc, E, W)

    if not shots:
        E("没有解析到任何「镜头N（a–b秒）：」片段——提示词结构不规范，模型会自由发挥")
        return errs, warns, infos, text, shots

    nd = args.no_dialogue  # 纯画面副视频：无台词，旁白后期配

    all_lines = [l for s in shots for l in s["lines"]]
    total = sum(net_len(l) for l in all_lines)

    # ── E1 台词总量 ──
    # 首尾帧融合转场片（提示词同时含「首帧图」「尾帧图」特征）：台词密度天然低，
    # 10s 里大半是镜头运动，带货念稿下限不适用——降为 W
    fusion = args.fusion or ("首帧图" in text and "尾帧图" in text)
    if nd:
        I("纯画面模式（--no-dialogue）：台词类检查跳过，旁白后期配")
    elif total < args.min_chars:
        msg = (
            f"台词总共 {total} 字，低于 {args.min_chars} 字。"
            f"{int(args.duration)} 秒片实测 45 字只念到 65% 就停，后半程空转（v1 事故）"
        )
        if fusion:
            W(msg + "（首尾帧融合转场片，念稿下限不适用——确认台词覆盖说话区间即可）")
        else:
            E(msg)

    # ── E2 每拍台词 ──
    if not nd:
        for s in shots:
            n = sum(net_len(l) for l in s["lines"])
            if not s["lines"]:
                E(f"镜头{s['idx']} 没有 {{}} 台词块——纯画面拍没有信息量，除非刻意留白"
                  f"（格式：镜头{s['idx']}（0-3.5秒）：中景。动作描写{{台词}}，台词写在花括号里）")
            elif n < args.min_beat:
                E(f"镜头{s['idx']} 台词只有 {n} 字，低于 {args.min_beat} 字，这一拍撑不住")
            elif n < 10:
                I(f"镜头{s['idx']} 台词 {n} 字，偏短（语气短句可以，凑数不行）")

    # ── E3 花括号配对 ──
    if text.count("{") != text.count("}"):
        E(f"花括号不配对：{{ ×{text.count('{')}，}} ×{text.count('}')}——漏一个结尾，后面全被当台词念")
    for s in shots:
        if s["body"].count("{") != s["body"].count("}"):
            E(f"镜头{s['idx']} 内部花括号不配对")

    # ── E4 台词里混进标注 ──
    for l in all_lines:
        if re.search(r"\d+\s*[–—\-~]\s*\d+\s*秒|第[一二三四五]秒", l):
            E(f"台词里出现时间码：「{l[:18]}…」——模型不认，会念出来")
        if len(l) > 60:
            W(f"单句台词 {len(l)} 字偏长，一口气念不完容易吞字：「{l[:18]}…」")    # ── E5 时间轴 ──
    if shots[0]["start"] > 0.3:
        W(f"第一镜从 {shots[0]['start']}s 起，前 {shots[0]['start']}s 是空的")
    if abs(shots[-1]["end"] - args.duration) > 0.6:
        W(f"最后一镜结束于 {shots[-1]['end']}s，与设定时长 {args.duration}s 差 {abs(shots[-1]['end'] - args.duration):.1f}s")
    for a, b in zip(shots, shots[1:]):
        gap = b["start"] - a["end"]
        if abs(gap) > 0.15:
            kind = "空档" if gap > 0 else "重叠"
            W(f"镜头{a['idx']}→{b['idx']} 有 {abs(gap):.1f}s {kind}（{a['end']}s / {b['start']}s）")
    for s in shots:
        if s["end"] - s["start"] < 2:
            W(f"镜头{s['idx']} 只有 {s['end'] - s['start']:.1f}s，短于 2 秒，切点会显得跳")

    # ── E6 反转落点 ──
    if args.reverse_word and not nd:
        last = " ".join(shots[-1]["lines"])
        if args.reverse_word not in last:
            E(
                f"最后一拍台词里没有反转词「{args.reverse_word}」——"
                f"整条片子最贵的那 3 秒没收住（v4 事故：反转没说完就切了）"
            )

    # ── W1 镜首单字语气词 ──
    for s in shots[1:]:
        if s["lines"] and FILLER_HEAD.match(s["lines"][0]):
            W(
                f"镜头{s['idx']} 台词以单字语气词「{s['lines'][0][:3]}」开头——"
                f"接上一镜会前粘，听成上一拍的话（v3「行」/v5「嗯」两次事故）"
            )

    # ── W2 每拍身体动作 ──
    for s in shots:
        desc = LINE_RE.sub("", s["body"])
        if not ACTION.search(desc):
            W(f"镜头{s['idx']} 没有具体身体动作——只有表情和语气，模型会演成站桩说话")

    # ── W3 一镜一运镜 ──
    for s in shots:
        moves = CAMERA_MOVE.findall(LINE_RE.sub("", s["body"]))
        if len(moves) > 1:
            W(f"镜头{s['idx']} 写了 {len(moves)} 个运镜（{'/'.join(moves)}），一镜只该有一个")

    # ── W4 不可执行指令 ──
    for s in shots:
        hit = UNEXECUTABLE.search(LINE_RE.sub("", s["body"]))
        if hit:
            W(f"镜头{s['idx']} 出现「{hit.group(0)}」这类抽象指令，模型无法执行，要换成动作/音量/表情")

    # ── W5 一致性声明 ──
    if not nd:
        if "自始至终" not in text and "同一个人" not in text:
            W("没写人物一致性声明（「自始至终是同一个人」「五官不要混淆」）——多拍正反打最容易换脸")
        if "第三人" not in text and len(shots) >= 3:
            W("没写「不要第三人」——多人场景容易出现背景闲人，出片后要人工挡")

    # ── W6 产品穿帮 ──
    if not re.search(r"标签.{0,14}(不可读|不必清晰|不可辨认|虚焦|虚化)|虚[焦化].{0,6}罐", text):
        W("没约束产品标签可读性——AI 造的包装文字是乱的，一旦清晰就是穿帮")

    # ── I 情绪外化程度 ──
    concrete = len(CONCRETE.findall(text))
    external = len(EXTERNAL.findall(text))
    I(f"情绪词：抽象 {concrete} 处 / 外化 {external} 处（外化 > 抽象才算可演）")
    if concrete > external:
        W("抽象情绪词多于外化指令——「恼火」「得意」这类词模型只会演成皱眉，要翻成音量/动作/表情")

    return errs, warns, infos, text, shots


def main():
    ap = argparse.ArgumentParser(description="出片前提示词体检")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--prompt-file")
    src.add_argument("--json", help="文案 JSON，取 ai_shots[0].prompt")
    ap.add_argument("--duration", type=float, default=15.0)
    ap.add_argument("--min-chars", type=int, default=60, help="台词总字数下限（15s 片）")
    ap.add_argument("--min-beat", type=int, default=6, help="单拍台词字数下限（反转句通常很短，别卡太死）")
    ap.add_argument("--reverse-word", default="", help="最后一拍必须出现的反转词")
    ap.add_argument("--no-dialogue", action="store_true",
                    help="纯画面副视频（无台词，旁白后期配）：跳过台词类检查")
    ap.add_argument("--fusion", action="store_true",
                    help="融合转场片（AI 只做镜头运动，台词密度天然低）：念稿下限降为提醒")
    ap.add_argument("--strict", action="store_true", help="WARN 也算不通过")
    ap.add_argument("--quiet", action="store_true", help="只输出结论行")
    args = ap.parse_args()

    errs, warns, infos, text, shots = run(args)

    total = sum(net_len(l) for s in shots for l in s["lines"])
    head = f"prompt_lint · {len(shots)} 拍 / {total} 字 / {args.duration:.0f}s"
    if shots:
        head += f" / 轴 {shots[0]['start']:.1f}–{shots[-1]['end']:.1f}s"

    if not args.quiet:
        print(head)
        for m in errs:
            print(f"  [E] {m}")
        for m in warns:
            print(f"  [W] {m}")
        for m in infos:
            print(f"  [I] {m}")
        if errs or warns:
            print()

    if errs:
        print(f"✗ 不通过 · {len(errs)} ERROR / {len(warns)} WARN —— 改完再提交，这一版别花那 3 元")
        return 1
    if warns and args.strict:
        print(f"✗ 严格模式不通过 · {len(warns)} WARN")
        return 1
    print(f"✓ 通过 · 0 ERROR / {len(warns)} WARN —— 可以提交，记得先 --dry-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
