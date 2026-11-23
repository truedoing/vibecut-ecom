# -*- coding: utf-8 -*-
"""以张老师人工真值为准，算声纹/人脸/大模型各自准确率 + 三层接力准确率。
关键：人工真值里出现主角之外的辅助角色（周叔/苏秘书/陈助理/方主管/房东/其他），
而三层候选名单只有四个主角——凡是真值为辅助角色的句子，三层必然全错。单列报告。"""
import csv, re
from collections import Counter

F = "人工真值_张老师确认.csv"
rows = list(csv.DictReader(open(F, encoding="utf-8-sig")))
MAIN = ["林秀兰", "许宁", "周承", "许晴"]
INVALID = {"(无脸)", "不确定", "—", "", "解析失败"}

def parse_who_sv(blob):
    """who_sv 单元格里塞了四行：声纹X/人脸Y/推理Z/建议:W"""
    sv = re.search(r"声纹\s*([^\n]+)", blob)
    fc = re.search(r"人脸\s*([^\n]+)", blob)
    llm = re.search(r"推理\s*([^\n]+)", blob)
    sug = re.search(r"建议:([^\s(（]+)", blob)
    g = lambda m: (m.group(1).strip() if m else "")
    return g(sv), g(fc), g(llm), g(sug)

data = []
for r in rows:
    sv, fc, llm, sug = parse_who_sv(r["who_sv"])
    truth = r["人工真值"].strip()
    data.append(dict(id=int(r["id"]), ep=r["ep"], text=r["text"],
                     sv=sv, fc=fc, llm=llm, sug=sug, truth=truth))

# 只统计人工判了的（排除空）
labeled = [d for d in data if d["truth"]]
print("总句", len(data), "已人工判定", len(labeled), "未判", [d["id"] for d in data if not d["truth"]])

# 真值是否主角
def is_main(t): return t in MAIN
main_rows = [d for d in labeled if is_main(d["truth"])]
aux_rows = [d for d in labeled if not is_main(d["truth"])]
print(f"真值=四主角 {len(main_rows)} 句；真值=辅助角色 {len(aux_rows)} 句")
print("辅助角色真值分布:", Counter(d["truth"] for d in aux_rows))

def acc(subset, key, valid_filter=None):
    pool = [d for d in subset if d[key] not in INVALID]
    if valid_filter: pool = [d for d in pool if valid_filter(d)]
    ok = sum(1 for d in pool if d[key] == d["truth"])
    return ok, len(pool)

print("\n=== 全 95 句（含辅助角色）各层准确率 ===")
for k, name in [("sv", "声纹"), ("fc", "人脸"), ("llm", "大模型"), ("sug", "三层接力建议")]:
    ok, n = acc(labeled, k)
    print(f"{name:<8} {ok}/{n} = {ok/n*100:.1f}%" if n else f"{name}: 无有效样本")

print("\n=== 只看真值在四主角内的 84 句（三层候选名单覆盖得到）===")
for k, name in [("sv", "声纹"), ("fc", "人脸"), ("llm", "大模型"), ("sug", "三层接力建议")]:
    ok, n = acc(main_rows, k)
    print(f"{name:<8} {ok}/{n} = {ok/n*100:.1f}%" if n else f"{name}: 无有效样本")

print("\n=== 辅助角色 11 句：三层有没有一个猜对？ ===")
for d in aux_rows:
    print(f"  id{d['id']:>2} 真值={d['truth']:<4} 声纹={d['sv']:<4} 人脸={d['fc']:<6} 建议={d['sug']:<4} | {d['text'][:16]}")

print("\n=== 三层接力：建议 vs 人工 不一致的明细 ===")
diff = [d for d in labeled if d["sug"] != d["truth"]]
for d in diff:
    print(f"  id{d['id']:>2} 真值={d['truth']:<4} 建议={d['sug']:<4} 声纹={d['sv']:<4} 人脸={d['fc']:<6} 推理={d['llm']:<6} | {d['text'][:16]}")
print("不一致条数", len(diff), "/", len(labeled))

# 导出一份带真值的完整比对表
with open("三层比对_含人工真值.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["id","ep","text","sv","fc","llm","sug","truth","sv对","fc对","llm对","sug对"])
    w.writeheader()
    for d in data:
        t = d["truth"]
        w.writerow({**d,
                    "sv对": "是" if d["sv"]==t else "否",
                    "fc对": "是" if d["fc"]==t else "否",
                    "llm对": "是" if d["llm"]==t else "否",
                    "sug对": "是" if d["sug"]==t else "否"})
print("\n已导出 三层比对_含人工真值.csv")
