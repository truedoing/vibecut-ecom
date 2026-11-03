#!/usr/bin/env python3
"""文案脚本自查：只查 4 条硬红线，其余靠人。

用法: python3 validate.py <script.json>
"""
import io
import json
import sys

BANNED = [
    "降三高", "清肠", "排毒", "治便秘", "增强免疫", "防癌", "延年益寿",
    "更年期", "骨质疏松", "补钙", "改善睡眠", "医生推荐", "营养师认证",
    "央视上榜", "保健品", "有机", "蓝帽子", "不含坚果",
    "减肥", "代餐", "掉秤", "抗衰", "美白", "淡斑", "补气血",
]


def check(path):
    d = json.load(io.open(path, encoding="utf-8"))
    segs = d.get("segments") or []
    shots = d.get("ai_shots") or []
    problems = []

    if len(segs) > 15:
        problems.append(f"段数 {len(segs)} > 15")
    hooks = [s for s in segs if s.get("section_role") == "hook"]
    if len(hooks) != 1 or (hooks and hooks[0].get("seq") != 1):
        problems.append(f"hook 应恰好 1 段且是段 1（现有 {len(hooks)} 段）")
    if len(shots) > 2:
        problems.append(f"AI 镜头 {len(shots)} 条 > 2")
    # 只查真正会念出口的文本；合规说明、待确认清单里的词不算违规
    spoken = " ".join(
        [str(s.get("content") or "") for s in segs]
        + [str(d.get("hook_line") or ""), str(d.get("selling_point") or "")]
    )
    hit = [w for w in BANNED if w in spoken]
    if hit:
        problems.append("口播禁词命中: " + "、".join(hit))

    if problems:
        print("不合规：")
        for p in problems:
            print("  -", p)
        return 1
    print(f"OK · {len(segs)} 段 / {len(shots)} 条 AI / 无禁词")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: python3 validate.py <script.json>")
        sys.exit(2)
    sys.exit(check(sys.argv[1]))
