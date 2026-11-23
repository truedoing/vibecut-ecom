# -*- coding: utf-8 -*-
"""大模型推理层：只看台词上下文（不看声音不看画面），按称呼/身份/剧情推说话人。
角色固定四人：林秀兰(妈,老年)、许宁(女儿)、周承(周家儿子,年轻男)、许晴(妹妹,年轻女)。
输入上下文含目标句(用 >> 标出)和前后各几句。输出 JSON：{id, who, reason}。
不把结果当真值，只当三层接力里的一层判断。
"""
import csv
import json
import os
import re
import sys
import urllib.request

URL = "https://api.xiaomimimo.com/v1/chat/completions"
MODEL = "mimo-v2.6-pro"
KEY = os.environ["MIMO_API_KEY"]

PROMPT = """你是短剧台词分析助手。下面是一部家庭伦理短剧的一段连续台词，其中一句用 >> 标出。
剧中固定四个角色：
- 林秀兰：母亲，老年女性， often 被叫"妈"
- 许宁：女儿，年轻女性，家中排行姐姐
- 周承：周家儿子，年轻男性，许宁的丈夫/对象
- 许晴：妹妹，年轻女性，许宁的妹妹，管许宁叫"姐"

请根据称呼、身份、说话逻辑，判断 >> 这一句最可能是谁说的。只从上述四人中选一个；实在判断不了填"不确定"。
对每一句输出一行 JSON：{"who":"...","reason":"一句话理由"}
理由要简短（20字内），说明靠什么线索判断的（如"被叫姐""自称妈""对女儿说话"等）。

台词（按时间顺序，目标句已标 >>）：
{ctx}

只输出这一句的 JSON，不要别的。"""


def ask(ctx):
    payload = {"model": MODEL,
               "messages": [{"role": "user", "content": PROMPT.replace("{ctx}", ctx)}],
               "temperature": 0.2, "max_tokens": 200}
    txt = ""
    for attempt in range(3):  # 偶发空响应，重试
        try:
            req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                         headers={"api-key": KEY, "Content-Type": "application/json"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read())
            txt = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            txt = ""
        if txt.strip():
            break
    m = re.search(r"\{.*?\}", txt, re.S)
    if not m:
        return {"who": "解析失败", "reason": txt[:60]}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"who": "解析失败", "reason": txt[:60]}


def mark_target(ctx, t):
    # context 形如 "1s:甲 || 3s:乙 || 5s:目标"，给目标时间点前面加 >>
    parts = ctx.split(" || ")
    key = f"{t:.0f}s:"
    out = []
    for p in parts:
        if p.startswith(key):
            p = ">> " + p
        out.append(p)
    return "\n".join(out)


if __name__ == "__main__":
    rows = list(csv.DictReader(open("真值样本.csv", encoding="utf-8-sig")))
    if len(sys.argv) == 2:  # 单句测试：python llm_reason.py 0
        i = int(sys.argv[1])
        r = next(x for x in rows if int(x["id"]) == i)
        print("目标句:", r["text"])
        print("上下文:\n", mark_target(r["context"], float(r["t"])))
        print("模型:", ask(mark_target(r["context"], float(r["t"]))))
    else:  # 批量：python llm_reason.py batch 0 25
        lo, hi = int(sys.argv[2]), int(sys.argv[3])
        for r in rows:
            i = int(r["id"])
            if not (lo <= i < hi):
                continue
            res = ask(mark_target(r["context"], float(r["t"])))
            r["who_大模型"] = res.get("who", "")
            r["llm_reason"] = res.get("reason", "")
            print(i, r["who_声纹"], "->", r["who_大模型"], res.get("reason", ""), flush=True)
        with open("真值样本.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
