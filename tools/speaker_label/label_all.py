# -*- coding: utf-8 -*-
"""
第二步：全剧逐句提声纹，与 4 把尺子比对，输出说话人表。
输出 output/说话人表.csv 与 .json：集/时间/说话人/台词/把握/画面标注/是否存疑。
"""
import csv
import json
import sys
import numpy as np
import common as C

# 阈值用已知答案标定过：判对的句子 conf 中位 0.54、gap 中位 0.12+
# 存疑规则只看明显异常的，避免把大批正常句误标存疑
THRESHOLD = 0.25   # 相似度低于此值 = 实在不像任何一个人
MARGIN = 0.08      # 第一第二名差小于此值 = 两人太接近，分不清


def calc_doubt(conf, gap):
    d = []
    if conf < THRESHOLD:
        d.append("声音把握低")
    if gap < MARGIN:
        d.append("两人接近")
    return "、".join(d)


def write_results(results):
    results.sort(key=lambda r: (r["ep"], r["t"]))
    (C.OUT_DIR / "说话人表.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(C.OUT_DIR / "说话人表.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["ep", "t", "who", "conf", "runner_up", "gap",
                                          "screen_guess", "doubt", "text"])
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
    dubious = [r for r in results if r["doubt"]]
    print(f"\n完成：{len(results)} 句，存疑 {len(dubious)} 句（{len(dubious)/len(results)*100:.1f}%）。")


def relabel():
    """只重算存疑标记，不重跑声纹（调阈值时用）。"""
    p = C.OUT_DIR / "说话人表.json"
    rows = json.loads(p.read_text(encoding="utf-8"))
    for r in rows:
        r["doubt"] = calc_doubt(r["conf"], r["gap"])
    write_results(rows)
    print("已按新阈值重算存疑标记。")


def main():
    if "--relabel" in sys.argv:
        relabel()
        return

    names = json.loads((C.DATA_DIR / "speaker_names.json").read_text(encoding="utf-8"))
    centers = np.load(C.DATA_DIR / "sample_centers.npy")  # 顺序与 names 一致
    print(f"加载 {len(names)} 把声纹尺子：{names}")

    print("加载声纹模型...")
    clf = C.load_classifier()

    # 断点续跑：已有结果先读入
    out_json = C.OUT_DIR / "说话人表.json"
    done = {}
    if out_json.exists():
        for r in json.loads(out_json.read_text(encoding="utf-8")):
            done[(r["ep"], r["t"])] = r
        print(f"已有 {len(done)} 句结果，续跑。")

    results = []
    for ep in range(1, C.CFG.EPISODE_COUNT + 1):
        # lines 文件覆盖全部60集；sentences 只到17集，仅用来补画面标注对照
        ld = C.lines_json(ep)
        sd = C.sentences_json(ep)
        screen_by_t = {}
        if sd:
            for r in sd.get("rows", []):
                if (r.get("name") or "").strip():
                    screen_by_t[round(float(r["t"]), 1)] = r["name"].strip()
        rows = ld.get("lines", [])
        for idx, row in enumerate(rows):
            t = float(row["t"])
            key = (ep, t)
            if key in done:
                results.append(done[key])
                continue
            next_t = float(rows[idx + 1]["t"]) if idx + 1 < len(rows) else None
            s, e = C.line_window(row, next_t=next_t)
            # 临时 wav 放系统临时目录，让系统回收；不碰项目目录（避免删除权限问题）
            wav = C.TMP_DIR / f"sl_ep{ep:02d}_{idx:03d}.wav"
            try:
                C.slice_wav(ep, s, e, wav)
                emb = np.asarray(C.emb_of_wav(clf, wav))
                sims = [C.cosine(emb, c) for c in centers]
                order = np.argsort(sims)[::-1]
                top, second = order[0], order[1]
                who = names[top]
                conf = float(sims[top])
                gap = float(sims[top] - sims[second])
            except Exception as ex:
                who, conf, gap = "切句失败", 0.0, 0.0

            guess = screen_by_t.get(round(t, 1), "")  # 画面标注仅作对照，不作存疑依据
            rec = {
                "ep": ep, "t": t, "text": row.get("text", ""),
                "who": who, "conf": round(conf, 3),
                "runner_up": names[second] if len(names) > 1 else "",
                "gap": round(gap, 3),
                "screen_guess": guess,
                "doubt": calc_doubt(conf, gap),
            }
            results.append(rec)
        print(f"  ep{ep:02d} 完成 {len(rows)} 句，累计 {len(results)}")

    write_results(results)
    print("表已写入 output/说话人表.csv 和 .json")


if __name__ == "__main__":
    main()
