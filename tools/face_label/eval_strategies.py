# -*- coding: utf-8 -*-
"""用95句人工真值，对比几种接力规则的实际准确率，找出该站哪一层。
注意：规则是在同一批样本上调的，有过拟合风险，样本只有95句，结论要留余地。"""
import csv
from collections import Counter

F = "人工真值_张老师确认.csv"
rows = list(csv.DictReader(open(F, encoding="utf-8-sig")))
MAIN = ["林秀兰","许宁","周承","许晴"]
INVALID = {"(无脸)","不确定","—","","解析失败"}

import re
def parse(blob):
    g=lambda p:(re.search(p,blob).group(1).strip() if re.search(p,blob) else "")
    return g(r"声纹\s*([^\n]+)"), g(r"人脸\s*([^\n]+)"), g(r"推理\s*([^\n]+)")

D=[]
for r in rows:
    sv,fc,lm=parse(r["who_sv"])
    D.append(dict(id=int(r["id"]),sv=sv,fc=fc,lm=lm,truth=r["人工真值"].strip()))
L=[d for d in D if d["truth"]]
MAINR=[d for d in L if d["truth"] in MAIN]

def rate(subset,fn,label):
    ok=sum(1 for d in subset if fn(d)==d["truth"])
    n=len(subset)
    print(f"  {label:<34} {ok}/{n} = {ok/n*100:.1f}%")

# 先看：声纹和人脸打架时，到底谁对
both=[d for d in L if d["sv"] not in INVALID and d["fc"] not in INVALID and d["sv"]!=d["fc"]]
sv_ok=sum(1 for d in both if d["sv"]==d["truth"])
fc_ok=sum(1 for d in both if d["fc"]==d["truth"])
both_main=[d for d in both if d["truth"] in MAIN]
sv_ok_m=sum(1 for d in both_main if d["sv"]==d["truth"])
fc_ok_m=sum(1 for d in both_main if d["fc"]==d["truth"])
print(f"声纹人脸打架 {len(both)} 句：声纹对 {sv_ok}，人脸对 {fc_ok}")
print(f"其中真值在四主角 {len(both_main)} 句：声纹对 {sv_ok_m}，人脸对 {fc_ok_m}")
print()

def r_sv(d): return d["sv"]
def r_face_first(d): return d["fc"] if d["fc"] not in INVALID else d["sv"]
def r_face_only(d): return d["fc"] if d["fc"] not in INVALID else ""  # 无脸不算
def r_vote_sv(d):  # 现规则：多数；平票大模型>声纹
    votes=[v for v in (d["sv"],d["fc"],d["lm"]) if v not in INVALID]
    if not votes: return ""
    c=Counter(votes); top=c.most_common()
    if len(top)==1 or top[0][1]>top[1][1]: return top[0][0]
    tied=[x[0] for x in top if x[1]==top[0][1]]
    return d["lm"] if d["lm"] in tied else d["sv"]
def r_vote_face(d):  # 改规则：多数；平票人脸>大模型>声纹
    votes=[v for v in (d["sv"],d["fc"],d["lm"]) if v not in INVALID]
    if not votes: return ""
    c=Counter(votes); top=c.most_common()
    if len(top)==1 or top[0][1]>top[1][1]: return top[0][0]
    tied=[x[0] for x in top if x[1]==top[0][1]]
    for p in (d["fc"],d["lm"],d["sv"]):
        if p in tied: return p
    return tied[0]
def r_face_then_llm(d):  # 人脸有结果就信人脸；无脸时信大模型；再无信声纹
    for p in (d["fc"],d["lm"],d["sv"]):
        if p not in INVALID: return p
    return ""

for subset,name in [(L,"全95句(含辅助角色)"),(MAINR,"只四主角82句")]:
    print(f"=== {name} ===")
    rate(subset,r_sv,"①只用声纹")
    rate(subset,r_face_first,"②人脸优先(无脸用声纹兜底)")
    rate(subset,r_vote_sv,"③现规则投票(平票站声纹)")
    rate(subset,r_vote_face,"④改规则投票(平票站人脸)")
    rate(subset,r_face_then_llm,"⑤人脸>大模型>声纹 顺序")
    print()

# 人脸优先规则里，错的都是什么
print("=== ②人脸优先 在四主角里判错的 ===")
for d in MAINR:
    p=r_face_first(d)
    if p!=d["truth"]:
        print(f"  id{d['id']:>2} 真值={d['truth']:<4} 人脸={d['fc']:<6} 声纹={d['sv']:<4}")
