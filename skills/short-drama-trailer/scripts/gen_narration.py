#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解说配音生成（MiMo 语音克隆）· 通用工具（2026-11-22 从 D7 的一次性脚本提取）

**为什么单独一个工具**：D7 的配音脚本是照着一条片子手写的（路径写死、段数写死、
文案写死在代码里）。新片子要重写一遍，而且每次都会把「情绪指令被念出来」
「语速估快了」这两个坑再踩一次。这个工具把那两件事固化下来。

**它做什么**：
  1. 读一份「配音表」JSON（段号 / 档位 / 情绪指令 / 文案 / 可用秒数上限）
  2. 调 MiMo 克隆接口生成每段的 mp3（用共用音色「说书人C」的声音样本）
  3. 变速 + 响度归一化，落到片子的 build 目录
  4. 写 manifest.json（每段的原始时长、成品时长、token 消耗）
  5. 顺带写一份 `解说配置.json`（给 duck_narration.py 叠轨用）

**关键规矩（都是踩坑换来的）**：

- **情绪指令走顶层 `instructions` 字段**，绝不拼进朗读文本——
  拼进 `content` 会被模型当台词念出来；`messages` 里用 `system` 角色接口 400；
  `【不朗读】`标记无效。
- **语速必须实测**：字��上限按 `实测语速` 算，别拍脑袋。
  说书人C 真实语速 **4.52 字/秒**（原始音频），按 5.75 估会偏快 27%。
  变速用 `atempo`，**只做一次**（叠两次会发飘）。
- **超上限就改文案，不许截断音频**。脚本会打印超限段并以退出码 1 结束。
- **`--only` 只重配改动的段**，其余沿用 manifest 里的旧 mp3——
  配音按 token 计费，改一句不用整条重配（D7 实测补配 4 段只花 909 tokens）。
- 配置一律走 `_env.read_env()` 读 `.env`，**不写死本机路径**。

**配音表长这样**（`--spec` 指它）：

```json
{
  "task": "D7_锁链",
  "voice_asset": "narration_说书人C",
  "tempo": 1.28,
  "segments": [
    {"idx": 1, "tier": "深", "instructions": "语气冷静克制，像一个看明白了的人慢慢讲",
     "text": "第一道绳，是她丈夫递的。",
     "limit": 13.85, "at": 5.35, "seg_label": "S02_如果我不送呢"},
    {"idx": 2, "tier": "留白", "instructions": null, "text": null,
     "limit": 0, "at": 0, "seg_label": "S01_你高兴就够了"}
  ]
}
```

- `text` 为 null ＝ **留白段**（不配解说，留原声），不占 token。
- `at` ＝ 这段解说的**成片起点秒数**，从清单段位推算。填错会导致复述率检测结果错误。
- `limit` ＝ 该段画面内可用的秒数上限，超了就必须改文案。

用法：
    python3 gen_narration.py --spec <配音表.json> [--out-dir <目录>] [--only 2,3,8,9]
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    from _env import read_env, data_root, tool  # type: ignore
except Exception:  # pragma: no cover
    sys.exit("找不到 _env.py——这个脚本必须放在 skills/<技能>/scripts/ 里跑")


DEFAULT_VOICE_DIR = "assets/voice_assets"


def dur(path: Path, ffprobe: str) -> float:
    return round(float(subprocess.check_output(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        text=True).strip()), 3)


def load_voice_asset(name: str) -> Path:
    """按音色资产名找声音样本（共用层 assets/voice_assets/<名字>/voice.mp3）。"""
    root = data_root()
    p = root / DEFAULT_VOICE_DIR / name / "voice.mp3"
    if p.exists():
        return p
    # 退一步：找名字里带关键字的
    base = root / DEFAULT_VOICE_DIR
    if base.exists():
        for d in sorted(base.iterdir()):
            if d.is_dir() and name and name in d.name:
                cand = d / "voice.mp3"
                if cand.exists():
                    return cand
    raise SystemExit(f"找不到音色资产的声音样本：{name}（在 {DEFAULT_VOICE_DIR}/<名字>/voice.mp3）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="配音表 JSON")
    ap.add_argument("--out-dir", default=None, help="输出目录，默认 <spec 同级>/配音输出")
    ap.add_argument("--only", default="", help="只重配这些段号，逗号分隔；其余沿用旧 mp3")
    ap.add_argument("--dry", action="store_true", help="只打印计划，不调接口")
    ap.add_argument("--jobs", type=int, default=2,
                    help="并发路数，默认 2。配音 90%% 的时间在等网络，串行 8 段要 147 秒。"
                         "**别调大**：11-23 实测 4 路并发直接吃 429 限流。"
                         "接口扛不住时脚本会退避重试，但重试也要花时间。")
    args = ap.parse_args()

    spec_path = Path(args.spec).expanduser().resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir).resolve() if args.out_dir else spec_path.parent / "配音输出"
    raw_dir = out_dir / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    cfg = read_env()
    base = (cfg.get("MIMO_BASE_URL") or "https://api.xiaomimimo.com/v1").rstrip("/")
    key = (cfg.get("MIMO_API_KEY") or "").strip()
    # 配音模型跟打标模型不是一回事：打标用 mimo-v2.6-pro，配音用语音克隆模型。
    # 这里不用 _env.mimo()，因为它给的是打标配置。
    model = cfg.get("MIMO_TTS_MODEL") or "mimo-v2.5-tts-voiceclone"
    tempo = float(spec.get("tempo", 1.0))
    voice_name = spec.get("voice_asset") or "narration_说书人C"

    only = set()
    if args.only:
        only = {int(x) for x in args.only.replace("，", ",").split(",") if x.strip()}

    man_path = out_dir / "manifest.json"
    old = {}
    if only and man_path.exists():
        old = {m["idx"]: m for m in json.loads(man_path.read_text(encoding="utf-8"))}

    man, over, tot = [], [], 0
    ffprobe = tool("ffprobe")
    ffmpeg = tool("ffmpeg")
    lock = threading.Lock()

    # 断点续跑：manifest 里已经有、且文案与这次 spec 完全一致的段，直接沿用现成的 mp3。
    # （2026-11-23 加：会话被掐断后重跑，manifest 还没写出来，已配好的段全白配一遍。
    #   现在每配完一段就增量落盘，下次跑认这个账。）
    if man_path.exists():
        for m in json.loads(man_path.read_text(encoding="utf-8")):
            old[int(m["idx"])] = m

    def flush_manifest() -> None:
        rows = sorted(man, key=lambda x: int(x.get("idx", 0)))
        man_path.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    def synth(seg: dict) -> dict:
        """配一段：调接口 → 落 raw → 变速响度 → 落成品 mp3。返回 manifest 那一行。

        单独抽出来是为了能并发跑——这一步 90% 的时间在等网络返回，
        8 段串行要 147 秒，4 路并发大约 40 秒（2026-11-23 实测）。
        """
        idx = int(seg.get("idx", 0))
        text = (seg.get("text") or "").strip()
        limit = float(seg.get("limit") or 0)
        tier = seg.get("tier") or ""

        if not key:
            raise SystemExit("缺 MIMO_API_KEY（在 .env 里配）")

        anchor = load_voice_asset(voice_name)
        b64 = base64.b64encode(anchor.read_bytes()).decode("ascii")
        body = {
            "model": model,
            # 只放要念的；情绪指令走顶层 instructions
            "messages": [{"role": "assistant", "content": text}],
            "instructions": seg.get("instructions") or "",
            "audio": {"format": "mp3", "voice": "data:audio/mpeg;base64," + b64},
        }
        req = urllib.request.Request(
            base + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "api-key": key},
            method="POST")
        # 接口有限流：并发一高就吃 429。退避重试，别让整批因为一次限流全废。
        # （2026-11-23：4 路并发第 7 段直接 429，整批中断，已配好的段白配。）
        data = None
        last_err = ""
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                body_txt = exc.read().decode("utf-8", errors="ignore")[:400]
                last_err = f"HTTP {exc.code}: {body_txt}"
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    wait = 2 ** attempt * 3          # 3s / 6s / 12s
                    print(f"  第{idx:02d}段 限流/服务忙（{exc.code}），{wait}s 后重试（第{attempt + 2}次）")
                    time.sleep(wait)
                    continue
                raise SystemExit(f"第{idx}段失败 {last_err}")
            except Exception as exc:
                last_err = f"{exc.__class__.__name__}: {exc}"
                if attempt < 3:
                    wait = 2 ** attempt * 3
                    print(f"  第{idx:02d}段 {last_err}，{wait}s 后重试（第{attempt + 2}次）")
                    time.sleep(wait)
                    continue
                raise SystemExit(f"第{idx}段失败 {last_err}")
        if data is None:
            raise SystemExit(f"第{idx}段失败 {last_err}")

        raw = raw_dir / f"第{idx:02d}段.mp3"
        raw.write_bytes(base64.b64decode(data["choices"][0]["message"]["audio"]["data"]))

        out = out_dir / f"第{idx:02d}段.mp3"
        # 变速只做一次；响度归一 + 限幅（分段 loudnorm 后必须加 alimiter）
        af = f"atempo={tempo},loudnorm=I=-16:TP=-1.5:LRA=7,alimiter=limit=0.95"
        subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(raw), "-af", af,
                        "-ar", "48000", str(out)], check=True)

        tk = (data.get("usage") or {}).get("total_tokens", 0)
        d = dur(out, ffprobe)
        row = {"idx": idx, "tier": tier, "instructions": seg.get("instructions") or "",
               "text": text, "file": out.name, "raw": dur(raw, ffprobe),
               "duration": d, "limit": limit, "tempo": tempo, "tokens": tk,
               "seg_label": seg.get("seg_label"), "at": seg.get("at")}
        # 落盘后**立刻**记账，不等整批跑完。
        # （2026-11-23：以前是全部跑完才写 manifest，一段失败整批丢，
        #   已经配好的段既没记账、下一轮又要重配。）
        with lock:
            man.append(row)
            flush_manifest()
        return row

    # 收集要配的段（留白段和沿用旧产物的段不走接口）
    todo: list[dict] = []
    for seg in spec.get("segments", []):
        idx = int(seg.get("idx", 0))
        text = (seg.get("text") or "").strip()
        limit = float(seg.get("limit") or 0)
        tier = seg.get("tier") or ""

        # 留白段：不配音、不占 token
        if not text:
            man.append({"idx": idx, "tier": tier, "text": "", "file": None,
                        "duration": 0.0, "limit": limit, "tokens": 0,
                        "seg_label": seg.get("seg_label"), "at": seg.get("at")})
            print(f"第{idx:02d}段 [{tier}] 留白，不配音")
            continue

        # 断点续跑：manifest 里有这段、**文案一字不差**、成品 mp3 还在 → 直接沿用，不花钱。
        # （2026-11-23 加：会话被掐断后重跑，已配好的段不用重配。
        #   文案比对是必须的——改了文案的段必须重配，否则成片和稿子对不上。）
        #   语义：--only 列出的是**强制重配**的段；其余段有现成的就沿用。
        prev = old.get(idx)
        mp3 = out_dir / f"第{idx:02d}段.mp3"
        reusable = (
            prev is not None
            and (prev.get("text") or "").strip() == text
            and mp3.is_file()
        )
        forced = bool(only) and idx in only
        if reusable and not forced:
            m = dict(prev)
            m["limit"] = limit
            m["at"] = seg.get("at", m.get("at"))
            man.append(m)
            print(f"第{idx:02d}段 [{tier}] 沿用现成配音 {m.get('duration', 0):6.2f}s（文案没变，不重配）")
            continue

        if only and idx not in only:
            if idx not in old:
                raise SystemExit(f"第{idx}段不在 --only 列表且没有旧产物，无法沿用")
            m = dict(old[idx])
            man.append(m)
            print(f"第{idx:02d}段 [{tier}] 沿用旧配音 {m.get('duration', 0):6.2f}s (上限 {limit:.2f})")
            continue

        if args.dry:
            print(f"第{idx:02d}段 [{tier}] 将配音 {len(text)} 字（上限 {limit:.2f}s）")
            continue

        todo.append(seg)

    if todo and not args.dry:
        workers = max(1, min(args.jobs, len(todo)))
        print(f"配 {len(todo)} 段，{workers} 路并发…")
        t0 = time.time()
        failed: list[str] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(synth, seg): seg for seg in todo}
            for fut in concurrent.futures.as_completed(futs):
                seg = futs[fut]
                try:
                    row = fut.result()
                except SystemExit as exc:
                    # 一段失败**不能拖垮整批**：其余段照跑，记下来最后统一报。
                    failed.append(str(exc))
                    print(f"  ✗ 第{seg.get('idx')}段失败：{exc}")
                    continue
                print(f"  ✓ 第{row['idx']:02d}段 {row['duration']:6.2f}s "
                      f"(上限 {row['limit']:.2f}) 已落盘")
        print(f"配音接口耗时 {time.time() - t0:.1f}s（{workers} 路并发）")
        if failed:
            print(f"\n✗ {len(failed)} 段没配上（其余段已落盘，重跑会跳过它们）：")
            for f in failed:
                print("  · " + f)
            flush_manifest()
            return 1

    for m in sorted(man, key=lambda x: int(x.get("idx", 0))):
        tot += int(m.get("tokens") or 0)
        d = float(m.get("duration") or 0)
        limit = float(m.get("limit") or 0)
        if not m.get("file"):
            continue
        flag = ""
        if limit and d > limit:
            flag = f"  ⚠ 超出上限 {d - limit:.2f}s"
            over.append((int(m["idx"]), d, limit))
        print(f"第{int(m['idx']):02d}段 [{m.get('tier', '')}] {float(m.get('raw') or 0):6.2f}s -> {d:6.2f}s "
              f"(上限 {limit:.2f}) tokens={m.get('tokens', 0)}{flag}")

    if args.dry:
        return 0

    man.sort(key=lambda m: m["idx"])
    man_path.write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # 顺带出叠轨用的解说配置（duck_narration.py 吃这个格式）
    cues, texts = [], []
    for m in man:
        if not m.get("file"):
            continue
        at = m.get("at")
        if at is None:
            continue
        # 路径相对**这份配置自己所在的目录**（叠音工具按这个解析）：
        # 配置写在 <out_dir>/解说配置.json，所以只写文件名，不带 配音输出/ 前缀。
        # （2026-11-23 修：原来写 "配音输出/xxx.mp3"，叠音工具按配置目录解析，
        #   拼出来是 <out_dir>/配音输出/配音输出/xxx.mp3，找不到文件。）
        cues.append({"at": float(at), "audio": m['file']})
        texts.append(m.get("text") or "")
    narration_cfg = {
        "voice": "zh-CN-YunjianNeural",
        "duck": 0,
        "gain": 1.32,
        "_note": "解说时原剧完全静音（duck=0），解说停后原声自动恢复。"
                 "起点 = 该段画面起点；每段都在本段画面内结束，不压留白段。",
        "cues": cues,
        "texts": texts,
        "_texts_note": "解说词原文，只为同步复述率检测用；配音用的是同名的 mp3。",
    }
    (out_dir / "解说配置.json").write_text(
        json.dumps(narration_cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"\n总时长 {round(sum(x['duration'] for x in man), 2)} 秒，本次 tokens {tot}")
    print(f"叠轨配置：{out_dir / '解说配置.json'}")
    if over:
        print("⚠ 超上限的段：", over, "——必须改文案，不能靠截断")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
