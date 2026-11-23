#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令出片：装配 → 配音 → 叠音（2026-11-23 建）

**为什么要有这个**
11-23 那次出片，从「装配」到「成片」花了 1 小时 24 分（10:02 → 11:26）。
真正花在片子上的时间不到 20 分钟，剩下的全是**顺序错了导致的返工**：

| 时间 | 干了什么 | 本来该怎么做 |
|---|---|---|
| 10:02–10:20 | 写 6 段解说（130 字，每段一句） | — |
| 10:20–10:40 | 改成 7 段 340 字 → 第 2 段塞不进 6 秒窗口 → 拆成 8 段 | **先拿「字数上限表」再写** |
| 10:40–11:00 | 写跨段撞词检测器、改词、改技能口径 | 检测器该是现成的（**已被取代**：11-23 张老师定，撞词检测整道去掉，解说不原样引用就不会撞） |
| 11:00–11:08 | 装配底片 | — |
| 11:08–11:20 | 发现时间轴差 0.45s（转场黑场漏算）→ 重排 8 段落点 | 时间轴该是唯一真源 |
| 11:20–11:25 | 配音 8 段 | — |
| 11:25–11:26 | 第 8 段超窗 → 压缩 → 重配 | 配音前就该算准 |
| 11:26–11:28 | 修两个路径 bug、叠音、出片 | — |

**这个脚本定的规矩：顺序不能错。**

```
 ① 装配底片（先拿真实时间轴，后面的落点才有准）
      ↓
 ② 出「字数上限表」——每段能写多少字，先看表再写文案
      ↓
 ③ 写文案（人工/AI），落点避开关键原声
      ↓
 ④ 一键校验：时间轴对不对、字超没超、撞没撞原话
      ↓
 ⑤ 配音（超上限直接拒，不出片）
      ↓
 ⑥ 叠音出片
```

**关键：②的表在写文案之前就有，⑤在超限时直接失败。**
这两条一卡，11-23 的三轮返工（拆块 → 压字 → 重配）都不会发生。

用法：
    # 只做到第 ② 步：拿字数上限表，用来写文案
    python3 build_trailer.py --plan runs/x/plan.json --step caps

    # 全流程（文案先写好，放在 --narration 里）
    python3 build_trailer.py --plan runs/x/plan.json --narration runs/x/build/解说配置.json

    # 试跑不花钱（只做校验，不调配音接口）
    python3 build_trailer.py --plan runs/x/plan.json --narration runs/x/build/解说配置.json --dry
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from timeline import build_timeline, chars, find_host, load_rate  # noqa: E402

MIX = HERE / "mix_trailer.py"
GEN = HERE / "gen_narration.py"
DUCK = HERE / "duck_narration.py"
TIMELINE = HERE / "plan_timeline.py"
# 11-23 张老师定：撞词检测、关键台词保护两道从出片流程摘掉。
# 理由：解说只要不原样引用原剧台词就不会撞；解说已限定只落静音垫画面段，
# 结构上盖不到主线原声。留着这两道等于自己挖坑自己查，白耗 20 分钟。


def hr(title: str) -> None:
    print(f"\n{'=' * 64}\n  {title}\n{'=' * 64}")


def run(cmd: list[str], desc: str) -> int:
    print(f"\n$ {desc}")
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    for line in out.rstrip().splitlines():
        print("  " + line)
    print(f"  ⏱  {time.time() - t0:.1f}s")
    return r.returncode


def step_caps(plan_path: Path, rate: float) -> int:
    """出字数上限表——写文案之前必须先看这个。

    第三轮起有两类段，含义不同：
      · 主线段（ep）：原声一句不丢，**不放解说**
      · 垫画面段（broll）：静音画面，解说配在这里 → 字数上限看它
    """
    hr("② 每段解说能写多长（照这张表写，超了就拆块）")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    tl = build_timeline(plan)
    total = tl[-1]["end"] if tl else 0.0
    print(f"成片总长 {total:.2f}s　语速 {rate} 字/秒（纯字）\n")
    print(f"{'段':<26}{'起':>7}{'止':>8}{'可用':>7}{'字数上限':>9}  说明")
    print("-" * 78)
    for it in tl:
        if it["kind"] != "seg":
            continue
        typ = it["seg"].get("type")
        if typ == "ep":
            print(f"{it['label'][:24]:<26}{it['start']:>7.2f}{it['end']:>8.2f}"
                  f"{it['dur']:>7.2f}{'-':>9}  主线原声，不放解说")
        elif typ == "broll":
            print(f"{it['label'][:24]:<26}{it['start']:>7.2f}{it['end']:>8.2f}"
                  f"{it['dur']:>7.2f}{int(it['dur'] * rate):>9d}  ← 解说配这里")
    print("-" * 78)
    print("用法：解说要写在「垫画面段」那一行给的字数上限之内，超了就拆成两块。")
    print("⚠️ 垫画面段太短装不下解说时：加长它（多剪一个镜头），别把解说压到主线段上。")
    return 0


def step_check(plan_path: Path, narr_path: Path, rate: float) -> tuple[int, list[str]]:
    """④ 写完文案、配音前的全部校验。任何一项不过都不许进配音。"""
    problems: list[str] = []
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    cfg = json.loads(narr_path.read_text(encoding="utf-8"))
    tl = build_timeline(plan)
    cues, texts = cfg.get("cues") or [], cfg.get("texts") or []
    if not cues:
        return 1, ["解说配置里没有 cues"]

    # 已有配音的用实测时长，没有的按语速估。
    # ⚠️ 只有**文案没变**的那段才能用实测时长——改了字却沿用旧的实测值，
    #    就会把超窗的段落判成合格（11-23 自测时发现的洞）。
    #    判据：manifest 里记的字数跟当前文案字数一致，才算同一段。
    man: dict[int, float] = {}
    mp = narr_path.parent / "配音输出" / "manifest.json"
    if mp.is_file():
        for row in json.loads(mp.read_text(encoding="utf-8")):
            idx = int(row.get("idx", 0))
            old_text = row.get("text") or ""
            cur_text = texts[idx - 1] if 0 < idx <= len(texts) else ""
            if old_text.strip() != cur_text.strip():
                continue  # 文案改过了，旧实测值作废
            man[idx] = float(row.get("duration") or 0)

    print(f"{'段':>3} {'落点':>8} {'所在画面':<22}{'窗口':>7}{'上限':>6}{'实字':>6}  判读")
    print("-" * 68)
    prev_end = None
    for i, cue in enumerate(cues):
        at = float(cue["at"])
        text = texts[i] if i < len(texts) else ""
        n = chars(text)
        host = find_host(tl, at)
        if not host:
            problems.append(f"第{i + 1}段 at={at:.2f}s 不在任何画面段里（多半落在转场黑场）")
            print(f"{i + 1:>3} {at:>8.2f} {'—':<22}{'—':>7}{'—':>6}{n:>6}  ✗ 落点无效")
            continue
        # 第三轮起：解说只能落在垫画面段（主线段有原声，盖了等于删）
        if host["seg"].get("type") != "broll":
            problems.append(
                f"第{i + 1}段落在主线段「{host['label']}」上——"
                f"那里的原声会被解说盖掉（等于删掉）。解说该配在垫画面段上。")
        win = host["end"] - at
        cap = int(win * rate)
        real = man.get(i + 1)
        need = real if real else n / rate
        ok = need <= win
        if not ok:
            problems.append(
                f"第{i + 1}段装不下：{n}字需要 {need:.2f}s，窗口只有 {win:.2f}s（上限 {cap}字）"
                + (f"，实测配音 {real:.2f}s" if real else ""))
        if prev_end is not None and at < prev_end:
            problems.append(f"第{i + 1}段与上一段解说重叠 {prev_end - at:.2f}s")
            ok = False
        prev_end = at + need
        how = f"实测{real:.2f}s" if real else f"估{n / rate:.2f}s"
        print(f"{i + 1:>3} {at:>8.2f} {host['label'][:20]:<22}{win:>7.2f}{cap:>6d}{n:>6}  "
              f"{'✓' if ok else '✗'} {how}")
    return (1 if problems else 0), problems


def build_narration_spec(plan_path: Path, narr_path: Path, rate: float) -> Path:
    """从解说配置生成配音表（自动算好 at/limit，不让人手抄）。"""
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    cfg = json.loads(narr_path.read_text(encoding="utf-8"))
    tl = build_timeline(plan)
    cues, texts = cfg.get("cues") or [], cfg.get("texts") or []
    ins = cfg.get("instructions") or []
    segs = []
    for i, cue in enumerate(cues):
        at = float(cue["at"])
        text = texts[i] if i < len(texts) else ""
        host = find_host(tl, at)
        raw = ins[i] if i < len(ins) else ""
        tier, rest = "中", raw
        if raw.startswith("（"):
            tier = raw[1] if len(raw) > 1 and raw[1] != "）" else "中"
            rest = raw.split("）", 1)[1] if "）" in raw else ""
        segs.append({
            "idx": i + 1, "tier": tier, "instructions": rest.strip(),
            "text": text, "limit": round(host["end"] - at, 2) if host else 0,
            "at": at, "seg_label": host["label"] if host else "",
        })
    spec = {
        "task": plan.get("title") or plan_path.parent.name,
        "voice_asset": "narration_说书人C",
        "tempo": 1.28,
        "_note": f"由 build_trailer.py 自动生成，at/limit 取自 timeline.py（唯一真源）。语速 {rate} 字/秒。",
        "segments": segs,
    }
    out = narr_path.parent / "配音表.json"
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="一条命令出片：装配 → 配音 → 叠音")
    ap.add_argument("--plan", required=True, help="剪辑清单 plan.json")
    ap.add_argument("--narration", default="", help="解说配置 json（文案+落点）")
    ap.add_argument("--out-dir", default="", help="产物目录，默认 <plan 同级>/build")
    ap.add_argument("--step", default="all",
                    choices=["caps", "check", "all"], help="caps=只出字数上限表")
    ap.add_argument("--preset", default="veryfast", help="装配画质档位")
    ap.add_argument("--allow-mid-speech", action="store_true", help="允许刀口切在说话中间")
    ap.add_argument("--only", default="", help="只重配这些段号（改一段用）")
    ap.add_argument("--force-rebuild", action="store_true", help="底片已存在也重新装")
    ap.add_argument("--dry", action="store_true", help="只校验不配音，不花钱")
    args = ap.parse_args()

    plan_path = Path(args.plan).expanduser().resolve()
    if not plan_path.is_file():
        sys.exit(f"找不到剪辑清单：{plan_path}")
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else plan_path.parent / "build"
    out_dir.mkdir(parents=True, exist_ok=True)

    rate, rate_src = load_rate()
    print(f"成品语速基准：{rate} 字/秒（纯字）　来源：{rate_src}")

    # ② 只需要清单：出字数上限表
    if args.step == "caps":
        return step_caps(plan_path, rate)

    if not args.narration:
        sys.exit("要出片得给 --narration（解说配置 json）。只想要字数上限表就加 --step caps。")
    narr_path = Path(args.narration).expanduser().resolve()
    if not narr_path.is_file():
        sys.exit(f"找不到解说配置：{narr_path}")

    # ① 装配底片（已有且不比清单旧就复用，省 2 分钟）
    hr("① 装配底片")
    base_mp4 = out_dir / "底片.mp4"
    reuse = base_mp4.is_file() and base_mp4.stat().st_mtime >= plan_path.stat().st_mtime
    if reuse and not args.force_rebuild:
        print(f"底片已存在且不比清单旧（{base_mp4.stat().st_mtime:.0f}），直接复用。"
              f"要重装加 --force-rebuild")
    else:
        cmd = [sys.executable, str(MIX), "--plan", str(plan_path), "--out", str(base_mp4),
               "--preset", args.preset]
        if args.allow_mid_speech:
            cmd.append("--allow-mid-speech")
        if run(cmd, "mix_trailer.py 装配底片") != 0:
            print("\n✗ 装配没过（刀口或时长不合格）。别硬闯——改上游清单再装配。")
            return 1

    # ④ 校验（在配音之前）
    hr("④ 配音前校验")
    rc, problems = step_check(plan_path, narr_path, rate)
    if problems:
        print("\n✗ 有问题，先改，别进配音（改了重配一次要花钱）：")
        for p in problems:
            print("  · " + p)
        print("\n提示：解说要拆块的话，回 ② 拿字数上限表重新分配。")
        return 1
    print("\n✓ 字数与落点全部通过。")

    if args.dry:
        print("\n--dry：到此为止，不配音不叠音。")
        return 0

    # ⑤ 配音
    hr("⑤ 配音")
    spec = build_narration_spec(plan_path, narr_path, rate)
    cmd = [sys.executable, str(GEN), "--spec", str(spec)]
    if args.only:
        cmd += ["--only", args.only]
    if run(cmd, "gen_narration.py 生成配音") != 0:
        print("\n✗ 配音没过（超上限或接口报错）。")
        return 1

    # ⑥ 叠音
    hr("⑥ 叠音出片")
    spec_cfg = spec.parent / "配音输出" / "解说配置.json"
    out_mp4 = out_dir / "成片_带解说.mp4"
    cmd = [sys.executable, str(DUCK), "--video", str(base_mp4), "--out", str(out_mp4),
           "--spec", str(spec_cfg)]
    if run(cmd, "duck_narration.py 叠解说轨") != 0:
        print("\n✗ 叠音失败。")
        return 1

    hr("出片完成")
    print(f"底片：{base_mp4}")
    print(f"成片：{out_mp4}")
    print("\n还没做的验证（别当成过了）：")
    print("  · 留白区电平比对（证明原声没被盖）")
    print("  · 解说逐字核对（只查念错字，别查他/她这种同音字）")
    print("  · 看片：五幕、前 45 秒冲突密度、砍场前因")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
