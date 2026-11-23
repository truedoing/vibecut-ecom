#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解说轨叠加器（音频闪避）—— short-drama-trailer 技能脚本。

做什么：拿一条已经装配好的成片（画面+原声），把若干段解说配音叠上去；
解说响的时候把原声压低（默认压到 15%），解说结束原声自动回到原样。
画面一个像素都不动，只重编码音频。

为什么不改进 mix_trailer.py：装配器只管把原片声音拼起来，它的音频链
（loudnorm/alimiter）不能乱碰。解说是在成片出来之后加的一层薄壳，
独立一步，出问题只重做这一步，不重拼画面、不动清单。

用法（三选一，可混用）：
  # 1) 直接给文案，脚本调 edge-tts 生成配音（免费，不花钱）
  python3 duck_narration.py --video 成片.mp4 --out 带解说.mp4 \
      --cue '13.9:满屋子人都笑她。没人知道，这位老太太找了两个女儿，整整十七年。' \
      --cue '140.9:@结尾点题.mp3'

  # 2) 全部写进配置文件（多段、要换音色/压低倍数时推荐）
  python3 duck_narration.py --video 成片.mp4 --out 带解说.mp4 --spec 解说配置.json

  # 3) 只看计划不出片（先核对每段起点和时长）
  python3 duck_narration.py --video 成片.mp4 --out 带解说.mp4 --spec 解说配置.json --dry

--cue 写法：起点秒数:解说文本；前面加 @ 表示这是现成音频文件路径。
配置文件格式（JSON）：
{
  "voice": "zh-CN-YunjianNeural",   # 可选，默认 5 号云健（男声，激情）
  "duck": 0.15,                     # 可选，解说时原声压到几成，默认 0.15
  "gain": 1.25,                     # 可选，解说音量放大倍数，默认 1.25
  "cues": [
    {"at": 13.9, "text": "满屋子人都笑她。没人知道，这位老太太找了两个女儿，整整十七年。"},
    {"at": 140.9, "audio": "结尾点题.mp3"}
  ]
}
相对路径一律相对配置文件所在目录解析；--cue 里的相对路径相对当前目录。

起点怎么定：从清单段位推算 + 抽帧看画面确认（钩子说完那一帧之后）。
解说长度由脚本自动探测，不用手算——这正是以前手搓 ffmpeg 命令最容易错的地方。

音色从哪来：edge-tts（微软 Edge 公开语音接口，本机已装，不花钱、不用密钥）。
普通话常用：zh-CN-XiaoxiaoNeural（晓晓，女·温润）、zh-CN-XiaoyiNeural（晓伊，女·活泼）、
zh-CN-YunyangNeural（云扬，男·专业）、zh-CN-YunxiNeural（云希，男·阳光）、
zh-CN-YunjianNeural（云健，男·激情）。全部音色：edge-tts --list-voices | grep zh-CN

已知边界：
  · 不做语音转文字逐字验证（太慢）；落点对不对用抽帧看画面，声音进没进去
    用解说段与相邻段的音量包络对比，或者人耳听。
  · 解说段之间如果重叠，会报警——两段解说叠一起没人听得清。
  · 解说时长超过视频尾部会报警——那种情况应该改起点，不是截断解说。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
try:
    from _env import HOST as _HOST, data_root as _data_root
except Exception:  # 兜底：别悄悄指到当前目录
    _HOST = Path(__file__).resolve().parents[3]

    def _data_root() -> Path:
        docs = Path.home() / "Documents"
        return (docs if docs.is_dir() else Path.home()) / "短剧工作区"


DEFAULT_VOICE = "zh-CN-YunjianNeural"
DEFAULT_DUCK = 0.15
DEFAULT_GAIN = 1.25


def find_tool(name: str, override: str | None = None) -> str:
    """数据目录 bin/ 优先（ffmpeg 8.0.1 在那里），再走 PATH。"""
    if override:
        return override
    local = _data_root() / "bin" / name
    if local.is_file():
        return str(local)
    found = shutil.which(name)
    if not found:
        raise RuntimeError(
            f"找不到 {name}。请确认已安装，或放一份到数据目录 bin/：{_data_root() / 'bin'}"
        )
    return found


def probe_duration(path: Path, ffprobe: str) -> float:
    """读音频/视频时长。失败直接报，不拿 0 当默认（会让闪避区间算错）。"""
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"读时长失败：{path}\n{r.stderr.strip()}")
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise RuntimeError(f"时长不是数字：{path} -> {r.stdout.strip()!r}")


def tts(text: str, out: Path, voice: str, edge_tts: str) -> Path:
    """文案 -> mp3。edge-tts 免费，不需要密钥。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [edge_tts, "--voice", voice, "--text", text, "--write-media", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"配音失败（音色 {voice}）：{r.stderr.strip()[:300]}")
    return out


def load_cues(args, base: Path) -> tuple[list[dict], dict, Path]:
    """把 --cue 和 --spec 合成统一的 cues 列表。

    返回的 base 是**解引用相对路径用的基准目录**：
      - 用 --spec 时 = 配置文件所在目录（文档就是这么写的）
      - 只用 --cue 时 = 当前目录
    （2026-11-23 修：以前这里把 base 改了却不往外传，导致 --spec 里的相对路径
      变成相对当前目录，叠音脚本找不到音频文件。）
    """
    cfg: dict = {}
    if args.spec:
        spec = Path(args.spec).resolve()
        if not spec.is_file():
            raise RuntimeError(f"配置文件不存在：{spec}")
        cfg = json.loads(spec.read_text(encoding="utf-8"))
        base = spec.parent
        for c in cfg.get("cues", []):
            if "audio" not in c and "text" not in c:
                raise RuntimeError("配置里有一段既没 text 也没 audio：" + json.dumps(c, ensure_ascii=False))
            if "at" not in c:
                raise RuntimeError("配置里有一段没写 at（起点秒数）：" + json.dumps(c, ensure_ascii=False))

    cues: list[dict] = []
    for raw in args.cue:
        head, sep, tail = raw.partition(":")
        if not sep:
            raise RuntimeError(f"--cue 写法不对（要 起点:文本）：{raw}")
        try:
            at = float(head)
        except ValueError:
            raise RuntimeError(f"--cue 起点不是数字：{head}")
        if tail.startswith("@"):
            cues.append({"at": at, "audio": tail[1:]})
        else:
            cues.append({"at": at, "text": tail})

    cues = cues + [dict(c) for c in cfg.get("cues", [])]
    if not cues:
        raise RuntimeError("没有解说段。用 --cue 或 --spec 给至少一段。")
    return cues, cfg, base


def resolve_audio(cue: dict, base: Path, idx: int, out: Path, voice: str, edge_tts: str) -> Path:
    if "audio" in cue:
        p = Path(cue["audio"]).expanduser()
        if not p.is_absolute():
            p = base / p
        p = p.resolve()
        if not p.is_file():
            raise RuntimeError(f"第 {idx} 段解说音频不存在：{p}")
        return p
    # 文案 -> 生成音频，落在成片旁边的 解说音频/ 目录，方便下次复用
    text = cue["text"]
    if not text.strip():
        raise RuntimeError(f"第 {idx} 段解说是空文本")
    audio_dir = out.parent / "解说音频"
    audio_dir.mkdir(parents=True, exist_ok=True)
    # 文件名带起点，避免同名覆盖；冒号等字符换成下划线
    safe = str(cue["at"]).replace(".", "_") + "_" + f"第{idx}段.mp3"
    return tts(text, audio_dir / safe, voice, edge_tts)


def build_filter(video_dur: float, plans: list[dict], duck: float, gain: float) -> str:
    """生成 ffmpeg 滤镜图：原声压低 + 各段解说对齐 + 混合。"""
    checks = " + ".join(f"between(t,{p['a']:.3f},{p['b']:.3f})" for p in plans)
    parts = [
        f"[0:a]volume='if({checks},{duck},1)':eval=frame[orig]"
    ]
    for i, p in enumerate(plans, 1):
        delay = int(round(p["at"] * 1000))
        parts.append(
            f"[{i}:a]aresample=48000,adelay={delay}|{delay},volume={gain}[v{i}]"
        )
    labels = "[orig]" + "".join(f"[v{i}]" for i in range(1, len(plans) + 1))
    parts.append(
        f"{labels}amix=inputs={len(plans) + 1}:duration=first:normalize=0[mixed]"
    )
    return ";\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="把解说叠进成片，解说时原声自动压低（音频闪避）。画面不动。"
    )
    ap.add_argument("--video", required=True, help="已装配好的成片 mp4")
    ap.add_argument("--out", required=True, help="输出 mp4（带解说）")
    ap.add_argument("--cue", action="append", default=[], metavar="起点:文本或@音频文件",
                    help="一段解说，可重复给多段")
    ap.add_argument("--spec", help="解说配置 JSON（多段/换音色时用）")
    ap.add_argument("--voice", default=None, help=f"edge-tts 音色，默认 {DEFAULT_VOICE}")
    ap.add_argument("--duck", type=float, default=None,
                    help=f"解说时原声压到几成，0=完全静音（解说停了自动恢复），默认 {DEFAULT_DUCK}")
    ap.add_argument("--gain", type=float, default=None,
                    help=f"解说音量倍数，默认 {DEFAULT_GAIN}")
    ap.add_argument("--ff", default=None, help="ffmpeg 路径（默认数据目录 bin/ 优先）")
    ap.add_argument("--ffprobe", default=None, help="ffprobe 路径（默认跟着 ffmpeg 走）")
    ap.add_argument("--edge-tts", dest="edge_tts", default=None, help="edge-tts 路径")
    ap.add_argument("--dry", action="store_true", help="只打印计划，不出片")
    args = ap.parse_args()

    try:
        ffmpeg = find_tool("ffmpeg", args.ff)
        # ffprobe 跟 ffmpeg 同目录优先；不然走 PATH
        ffprobe = args.ffprobe
        if not ffprobe:
            sibling = Path(ffmpeg).parent / "ffprobe"
            ffprobe = str(sibling) if sibling.is_file() else find_tool("ffprobe")
        edge_tts = args.edge_tts or shutil.which("edge-tts") or ""

        base = Path.cwd()
        cues, cfg, base = load_cues(args, base)
        voice = args.voice or cfg.get("voice") or DEFAULT_VOICE
        duck = args.duck if args.duck is not None else cfg.get("duck", DEFAULT_DUCK)
        gain = args.gain if args.gain is not None else cfg.get("gain", DEFAULT_GAIN)

        video = Path(args.video).expanduser().resolve()
        out = Path(args.out).expanduser().resolve()
        if not video.is_file():
            raise RuntimeError(f"成片不存在：{video}")
        if video == out:
            raise RuntimeError("输出不能覆盖原片，换个文件名")
        if not (0 <= duck <= 1):
            raise RuntimeError(f"压低倍数要在 0 到 1 之间（0=完全静音），现在是 {duck}")
        if gain <= 0:
            raise RuntimeError(f"解说音量倍数要大于 0，现在是 {gain}")

        video_dur = probe_duration(video, ffprobe)
        print(f"成片：{video.name}（{video_dur:.2f}s）")
        print(f"音色：{voice}　原声压到：{duck} 倍　解说音量：{gain} 倍")

        plans: list[dict] = []
        for i, cue in enumerate(cues, 1):
            at = float(cue["at"])
            if at < 0:
                raise RuntimeError(f"第 {i} 段起点是负数：{at}")

            # --dry 是只读预览：现成音频可以探时长，文案绝不能在此时生成配音
            if args.dry and "text" in cue:
                preview = cue["text"][:34] + ("…" if len(cue["text"]) > 34 else "")
                print(f"  第{i}段  {at:7.2f}s 起  文案（时长正式出片时探测）  ← {preview}")
                plans.append({"at": at, "a": at, "b": at, "audio": "", "dur": 0.0})
                continue

            if "audio" in cue:
                audio = resolve_audio(cue, base, i, out, voice, edge_tts)
            else:
                if not edge_tts:
                    raise RuntimeError("这段是文案，需要 edge-tts 生成配音；本机找不到 edge-tts")
                audio = resolve_audio(cue, base, i, out, voice, edge_tts)
            dur = probe_duration(audio, ffprobe)
            a, b = at, at + dur
            if at < 0:
                raise RuntimeError(f"第 {i} 段起点是负数：{at}")
            if b > video_dur + 0.05:
                raise RuntimeError(
                    f"第 {i} 段解说到 {b:.2f}s，超过成片 {video_dur:.2f}s。"
                    "改起点或缩短文案，不要截断解说。"
                )
            plans.append({"at": at, "dur": dur, "a": a, "b": b, "audio": str(audio)})
            print(f"  第{i}段  {at:7.2f}s 起  长{dur:5.2f}s  压原声 {a:.2f}–{b:.2f}s  "
                  f"← {audio.name}")

        # 重叠检查：两段解说叠一起没人听得清
        for i in range(len(plans) - 1):
            if plans[i]["b"] > plans[i + 1]["a"] + 0.01:
                raise RuntimeError(
                    f"第{i+1}段解说到 {plans[i]['b']:.2f}s，第{i+2}段从 "
                    f"{plans[i+1]['a']:.2f}s 开始，两段重叠了"
                )

        fc = build_filter(video_dur, plans, duck, gain)
        if args.dry:
            print("\n--- 滤镜图（--dry，未出片）---\n" + fc)
            return 0

        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [ffmpeg, "-y", "-v", "error", "-i", str(video)]
        for p in plans:
            cmd += ["-i", p["audio"]]
        cmd += [
            "-filter_complex", fc,
            "-map", "0:v", "-map", "[mixed]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            str(out),
        ]
        print("\n开始混音……")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("ffmpeg 混音失败：\n" + r.stderr.strip()[-800:])
        print(f"完成：{out}（{out.stat().st_size} 字节）")
        print("提示：落点对不对抽帧看画面；别动不动做语音转文字，太慢。")
        return 0

    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as e:
        print("失败：" + str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
