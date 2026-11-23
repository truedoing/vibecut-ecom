# -*- coding: utf-8 -*-
"""
第三步：抽检。对存疑句子重新切音频、高帧率抽帧辅助人工听辨，
并把听辨结论回填到 output/说话人表_人工核对.csv（人工改 who 列即可）。
不自动改表——说话人是内容判断，机器不替人拍板。
"""
import csv
import json
import subprocess
import common as C

REVIEW_DIR = C.OUT_DIR / "待听辨音频"
REVIEW_DIR.mkdir(parents=True, exist_ok=True)


def main():
    rows = json.loads((C.OUT_DIR / "说话人表.json").read_text(encoding="utf-8"))
    dubious = [r for r in rows if r.get("doubt")]

    # 每个说话人取几句"有把握"的作为对照样本，混着存疑句一起导出，方便对比着听
    picked = list(dubious[:60])
    for who in C.CFG.SPEAKERS:
        sure = [r for r in rows if r["who"] == who and not r["doubt"]][:3]
        picked.extend(sure)

    picked.sort(key=lambda r: (r["ep"], r["t"]))

    # 按集缓存 lines，好算 next_t（窗口切到下一句起点，避免串音）
    lines_cache = {}
    def get_next_t(ep, t):
        if ep not in lines_cache:
            lines_cache[ep] = C.lines_json(ep).get("lines", [])
        ls = lines_cache[ep]
        for i, l in enumerate(ls):
            if abs(float(l["t"]) - float(t)) < 0.01:
                if i + 1 < len(ls):
                    return float(ls[i + 1]["t"])
                break
        return None

    manifest = []
    for i, r in enumerate(picked):
        next_t = get_next_t(r["ep"], r["t"])
        s, e = C.line_window(r, next_t=next_t)
        wav = REVIEW_DIR / f"{i:03d}_ep{r['ep']:02d}_{r['t']:.0f}s_{r['who']}_{r['doubt'] or 'OK'}.wav"
        try:
            C.slice_wav(r["ep"], s, e, wav)
            manifest.append({
                "file": wav.name, "ep": r["ep"], "t": r["t"],
                "text": r["text"], "who": r["who"], "doubt": r["doubt"],
            })
        except Exception as ex:
            print(f"  切失败 ep{r['ep']} {r['t']}: {ex}")

    (REVIEW_DIR / "清单.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    # 出一份只含存疑句的 CSV，人工在此表改 who 列
    with open(C.OUT_DIR / "说话人表_人工核对.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["ep", "t", "who", "conf", "runner_up", "gap",
                                          "screen_guess", "doubt", "text"])
        w.writeheader()
        for r in dubious:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})

    print(f"存疑 {len(dubious)} 句，已切 {len(manifest)} 段音频到 {REVIEW_DIR}")
    print("听辨后改 output/说话人表_人工核对.csv 的 who 列。")
    print("对照样本（标注 OK）混在里面，按文件名区分。")


if __name__ == "__main__":
    main()
