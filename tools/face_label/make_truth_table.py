# -*- coding: utf-8 -*-
"""生成真值表：把三层判断并排列出，给建议真值，人工确认列留空。
真值不由机器定——'建议真值'只是综合三层给人工看的参考，'人工真值'列空着由张老师拍板。
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
rows = list(csv.DictReader(open(ROOT / "真值样本.csv", encoding="utf-8-sig")))


def suggest(r):
    """三层接力定稿规则（96句真值验证过）：
    - 三层里有两层以上同意 → 听多数
    - 打平时 → 按 人脸 > 大模型 > 声纹 的顺序定
    理由：人脸在四个主角上最准（86.9%），声纹最弱（72%）。
    旧规则打平站声纹，把人脸对的压掉了，主角准确率只有 79.3%；
    改成平票站人脸后，主角准确率 86.6%。
    辅助角色（周叔/苏秘书等）不在候选名单里，判错不影响主角口径。
    """
    sv = r["who_声纹"]
    face = r.get("who_人脸", "")
    llm = r.get("who_大模型", "")
    votes = [v for v in (sv, face, llm) if v and v not in ("解析失败", "不确定")]
    if not votes:
        return "", "三层都没结果"
    from collections import Counter
    c = Counter(votes)
    top = c.most_common()
    if len(top) == 1 or top[0][1] > top[1][1]:
        who = top[0][0]
    else:
        # 定稿：打平按 人脸 > 大模型 > 声纹
        tied = [x[0] for x in top if x[1] == top[0][1]]
        who = next((p for p in (face, llm, sv) if p in tied), tied[0])
    agree = sum(1 for v in votes if v == who)
    return who, f"{agree}/{len(votes)}层同意"


out_rows = []
for r in rows:
    sug, basis = suggest(r)
    # 三层是否一致
    sv, fa, lm = r["who_声纹"], r.get("who_人脸", ""), r.get("who_大模型", "")
    decides = [v for v in (sv, fa, lm) if v and v not in ("解析失败", "不确定")]
    consistent = "一致" if decides and len(set(decides)) == 1 else "打架/不全"
    out_rows.append({
        "id": r["id"],
        "集": r["ep"],
        "时间s": r["t"],
        "台词": r["text"],
        "存疑": r["doubt"] or "无",
        "声纹判": sv,
        "人脸判": fa or "(无脸)",
        "人脸距离": r.get("face_dist", ""),
        "大模型判": lm or "",
        "大模型理由": r.get("llm_reason", ""),
        "三层一致?": consistent,
        "建议真值": sug,
        "建议依据": basis,
        "人工真值": "",   # 留空，张老师填
        "人工确认": "",   # 留空，√ 或 改
        "音频": f"truth_audio/{int(r['id']):03d}_ep{int(r['ep']):02d}_{float(r['t']):.1f}s.wav",
        "画面": f"truth_frames/{int(r['id']):03d}_ep{int(r['ep']):02d}_{float(r['t']):.1f}s.jpg",
        "上下文": r["context"],
    })

out = ROOT / "真值表_待人工确认.csv"
with open(out, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
    w.writeheader()
    w.writerows(out_rows)

from collections import Counter
print("生成", len(out_rows), "行")
print("三层一致", Counter(r["三层一致?"] for r in out_rows))
print("建议真值分布", Counter(r["建议真值"] for r in out_rows))
print("文件", out)
