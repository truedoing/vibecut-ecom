# -*- coding: utf-8 -*-
"""
第一步：从原片切出"已知是谁说的"样本句，提声纹，存成 4 把尺子。
样本全部来自人工逐句听辨核准过的台词（不采信 sentences 里的 name 猜测）。
"""
import json
import numpy as np
import common as C

# (集, 起始秒, 结束秒, 文本, 说话人) —— 说话人人工核准
# 关键：切点在台词真实 shot 窗口内，绝不跨句（跨句会串到别人的声音，毁掉声纹尺子）
SAMPLES = [
    # 妈妈 林秀兰：老年女性，取连续的妈妈独白
    # 注意：ep1 13-17s 虽有"我是妈妈"字幕，但实为婚礼现场混杂声，声纹不像妈妈，已剔除
    (16, 42.9, 45.2, "我只是替你递材料", "林秀兰"),
    (1, 33.2, 35.8, "这17年妈一直在找你们", "林秀兰"),
    (16, 18.0, 19.7, "你这些年没少吃苦", "林秀兰"),
    # 女儿 许宁：年轻女性
    (9, 25.8, 28.8, "我想继续结婚 / 你们让我先不认他", "许宁"),
    (16, 12.0, 14.6, "少停工换件 / 方案我负责", "许宁"),
    (9, 37.5, 40.5, "我以为你愿意跟我过日子 / 现在才知道", "许宁"),
    (9, 1.0, 3.2, "成为你家的人 / 就得先不认自己的妈", "许宁"),
    # 周承：年轻男性
    (1, 46.0, 48.5, "来的都是亲戚领导 / 冒出你这么个亲家", "周承"),
    (9, 32.0, 35.6, "我对你还不够好 / 工作婚礼我哪一样没替你安排", "周承"),
    # 妹妹 许晴：年轻女性
    (1, 54.0, 58.2, "哪来的老婆子 / 跑到我姐婚礼上认女儿 / 你当我们好骗", "许晴"),
    (9, 44.5, 48.2, "姐你现在闹着走 / 过后可别后悔 / 不是谁都能嫁进", "许晴"),
]


def main():
    print("加载声纹模型（首次会走 hf-mirror 下载，约 80MB）...")
    clf = C.load_classifier()
    print("模型就绪。")

    embs = {}
    for i, (ep, a, b, text, who) in enumerate(SAMPLES):
        wav = C.SAMPLES_DIR / f"sample_{i:02d}_{who}.wav"
        C.slice_wav(ep, a, b, wav)
        e = C.emb_of_wav(clf, wav)
        embs.setdefault(who, []).append(e)
        print(f"  切样本 {i+1:>2}/{len(SAMPLES)}  ep{ep:02d} {a:>5.1f}-{b:>5.1f}  {who:<5} | {text}")

    # 每人取均值当尺子
    centers = {who: np.mean(np.asarray(v), axis=0).tolist() for who, v in embs.items()}
    np.save(C.DATA_DIR / "sample_centers.npy",
            np.asarray([centers[w] for w in sorted(centers)]), allow_pickle=False)
    (C.DATA_DIR / "speaker_names.json").write_text(
        json.dumps(sorted(centers), ensure_ascii=False, indent=1), encoding="utf-8")
    (C.DATA_DIR / "sample_meta.json").write_text(
        json.dumps([{"ep": e, "a": a, "b": b, "text": t, "who": w} for e, a, b, t, w in SAMPLES],
                   ensure_ascii=False, indent=1), encoding="utf-8")

    # 区分度自检：样本离自己人近、离别人远才算合格
    print("\n===== 区分度自检（余弦相似度，越大越像）=====")
    names = sorted(centers)
    ok = True
    for who in names:
        mine = embs[who]
        in_s = [C.cosine(mine[i], mine[j]) for i in range(len(mine)) for j in range(i + 1, len(mine))]
        out_s = [C.cosine(mine[i], centers[o]) for o in names if o != who for i in range(len(mine))]
        mi = np.mean(in_s) if in_s else float("nan")
        mo = np.mean(out_s) if out_s else float("nan")
        flag = "OK" if mi > mo + 0.10 else "⚠ 区分度不足"
        if flag.startswith("⚠"):
            ok = False
        print(f"  {who:<5} 自己人相似度 {mi:.3f}  别人 {mo:.3f}  差 {mi-mo:+.3f}  {flag}")
    print("\n尺子已存入 data/。" if ok else "\n⚠ 有说话人区分度不足，需换样本。")


if __name__ == "__main__":
    main()
