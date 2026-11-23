# -*- coding: utf-8 -*-
"""大模型推理层（批量版）：一次请求判 12 句，避免逐句调用触发限流。
只看台词上下文，按称呼/身份/剧情推说话人。结果存表，不当真值。
"""
import csv
import json
import os
import re
import sys
import time
import urllib.request

URL = "https://api.xiaomimimo.com/v1/chat/completions"
MODEL = "mimo-v2.6-flash"  # 配 reasoning_effort=low，快且够用
KEY = os.environ["MIMO_API_KEY"]
BATCH = 8

HEAD = """你是短剧台词分析助手。下面是家庭伦理短剧《以真心守护至亲》的多段连续台词。
固定四个角色：林秀兰(妈,老年女,常被叫"妈")、许宁(女儿,年轻女,姐姐)、
周承(周家儿子,年轻男,许宁丈夫)、许晴(妹妹,年轻女,管许宁叫"姐")。
每段用 ===编号=== 分隔，目标句前有 >> 标记。
请逐段判断 >> 那句是谁说的，只从四人中选；判断不了填"不确定"。
输出严格 JSON 数组，每个元素 {"id":编号, "who":"...", "reason":"20字内理由"}，共 N 个，不要别的文字。

"""

HEAD = HEAD.replace("共 N 个", "共 {n} 个")


def build_prompt(items):
    blocks = []
    for r in items:
        ctx = r["context"]
        t = float(r["t"])
        key = f"{t:.0f}s:"
        lines = []
        for p in ctx.split(" || "):
            lines.append((">> " + p) if p.startswith(key) else p)
        blocks.append(f"==={r['id']}===\n" + "\n".join(lines))
    return HEAD.replace("{n}", str(len(items))) + "\n\n".join(blocks)


def ask(items):
    prompt = build_prompt(items)
    payload = {"model": MODEL,
               "messages": [{"role": "user", "content": prompt}],
               "temperature": 0.2, "max_tokens": 4000,
               "reasoning_effort": "low"}  # 推理模型：关低思考，否则token全被reasoning吃掉、正文为空
    txt = ""
    for attempt in range(2):
        try:
            req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                         headers={"api-key": KEY, "Content-Type": "application/json"},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read())
            txt = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        except Exception:
            txt = ""
        if txt.strip():
            break
    m = re.search(r"\[.*\]", txt, re.S)
    if not m:
        return None, txt[:120]
    try:
        return json.loads(m.group(0)), ""
    except Exception:
        return None, txt[:120]


if __name__ == "__main__":
    rows = list(csv.DictReader(open("真值样本.csv", encoding="utf-8-sig")))
    # 只处理还没结果或解析失败的
    todo = [r for r in rows if not r.get("who_大模型") or r["who_大模型"] in ("解析失败", "")]
    print("待判", len(todo), "句，分", (len(todo) + BATCH - 1) // BATCH, "批", flush=True)
    done = 0
    for start in range(0, len(todo), BATCH):
        chunk = todo[start:start + BATCH]
        result, err = ask(chunk)
        if result:
            by_id = {str(x.get("id")): x for x in result if isinstance(x, dict)}
            for r in chunk:
                got = by_id.get(r["id"], {})
                r["who_大模型"] = got.get("who", "解析失败")
                r["llm_reason"] = got.get("reason", "")
            done += len(chunk)
            print(f"批次 {start//BATCH+1}: +{len(chunk)}  累计{done}", flush=True)
        else:
            print(f"批次 {start//BATCH+1}: 失败 {err}", flush=True)
        time.sleep(2)  # 批间缓冲，防限流
        with open("真值样本.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
    ok = sum(1 for r in rows if r.get("who_大模型") and r["who_大模型"] not in ("解析失败", ""))
    print("完成，大模型有效结果", ok, "/", len(rows))
