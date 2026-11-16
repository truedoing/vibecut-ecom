#!/usr/bin/env python3
"""短剧镜头打标：场景检测切镜头 → 每镜头抽 1 帧 → VLM 批量描述 → shots_epNN.json

**只描述，不认人。** 短剧演员无名无参考照，让模型认人只会更不准（11-08 定的）。
模型输出「老年女性 深色旧棉袄 坐轮椅」这种特征串，人名映射由 rules 层反推、人审一次。

为啥不共用 ecommerce-asset-analysis：一套行业一套技能，各演进各的（张老师两次纠正）。
本脚本字段全是短剧要的（谁能说话、能不能切出去、有没有水印），与商品字段没有交集。

用法：
  python3 tag_shots.py --drama 以真心守护至亲 --ep 5
  python3 tag_shots.py --drama 以真心守护至亲 --ep 5 6 7 --force
  python3 tag_shots.py --drama 以真心守护至亲 --ep 5 --no-vlm    # 只切镜头不打标，先看切成几段
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

try:  # 数据目录不靠数层级猜：见同目录 _env.py（换机器也不用改代码）
    from _env import data_root as _data_root
    from _env import MIMO_BASE_URL as _MIMO_URL, MIMO_MODEL as _MIMO_MODEL
except Exception:  # 极端情况下退回老办法，至少不崩
    _MIMO_URL = "https://api.xiaomimimo.com/v1"
    _MIMO_MODEL = "mimo-v2.6-pro"

    def _data_root():
        return Path(__file__).resolve().parents[3]

ROOT = _data_root()
ENV_FILE = ROOT / ".env"

# 进度上报是给主管的网页看的。主管技能的 scripts 住在插件包的 skills/ 下，
# 不在数据目录里——以前按 ROOT 往上找，装成插件后必然落空，降级成 None，
# 网页上就看不到打标的细进度（11-16 修）。改成问 _env 要宿主位置。
try:
    from _env import HOST as _HOST
except Exception:
    _HOST = Path(__file__).resolve().parents[3]
_SUP = _HOST / "skills" / "short-drama-supervisor" / "scripts"
try:
    if _SUP.is_dir() and str(_SUP) not in sys.path:
        sys.path.insert(0, str(_SUP))
    import progress
    import analysis_log
except Exception:  # 没装这个模块、或换了目录结构，照样能跑
    progress = None
    analysis_log = None

SCENE_THRESHOLD = 0.25   # 场景切换阈值：低于它会把人物小动作当成切镜头
FRAME_SCALE = 640        # 缩图边长：只付 640px 的钱，原片不出本机
FRAMES_PER_SHOT = 3      # 每镜头抽几帧：单帧会正好撞上闭嘴的那一下（11-12 实测）
BATCH = 3                # 每次调用带几帧（3 帧 = 1 个镜头一组）
                         # 11-12 实测：一组带 2 个镜头时，后一个镜头的描述会抄前一个
                         # （ep15 9.8-14.1 画面只有手在装包，却报成“年轻女性正脸说话”）
PROMPT_VERSION = "sd-shot-v5"


def load_env() -> dict:
    env = dict(os.environ)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def tool(name: str) -> str:
    """找可执行文件：包内 bin/ → PATH → anaconda / homebrew 常见位。"""
    try:  # 优先用包里自带的 bin（分发到别的机器时 ffmpeg 就在包里）
        from _env import tool as _env_tool
        cand = _env_tool(name)
        if Path(cand).is_absolute() and Path(cand).exists():
            return cand
    except Exception:
        pass
    from shutil import which
    hit = which(name)
    if hit:
        return hit
    sys.exit(f"找不到 {name}。跑一次开工自检会自动下；"
             "下不动就手动放进 <数据目录>/bin")


def probe_duration(video: Path) -> float:
    r = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def detect_shots(video: Path, duration: float, threshold: float) -> list[dict]:
    """用场景检测拿镜头边界，返回 [{start, end}]。"""
    r = subprocess.run(
        [tool("ffmpeg"), "-v", "error", "-i", str(video), "-vf",
         f"select='gt(scene,{threshold})',metadata=print:file=-", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    cuts = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", r.stdout)]
    cuts = sorted({round(c, 2) for c in cuts if 0.3 < c < duration - 0.3})
    bounds = [0.0] + cuts + [round(duration, 2)]
    return [{"start": bounds[i], "end": bounds[i + 1]} for i in range(len(bounds) - 1)]


def extract_frame(video: Path, t: float, out: Path) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [tool("ffmpeg"), "-v", "error", "-ss", f"{max(t, 0.05):.2f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale={FRAME_SCALE}:-2", "-q:v", "4", "-y", str(out)],
        capture_output=True,
    )
    return out.exists()


TAG_PROMPT = """你是短剧素材的镜头标注员。素材是竖屏 AI 短剧，正反打为主。
下面按顺序给你 {n} 张图。**这 {n} 张全部属于同一个镜头**（该镜头的首、中、尾几个瞬间），
逐张输出一个 JSON 对象，按顺序组成数组返回，不要输出其他文字。
注意：**同一个镜头可以有好几句台词**，不要假设一张图只有一句话。
**只描述你在这 {n} 张里真正看见的内容，不要参考任何其他镜头。**

字段：
- "i": 图片序号（与传入一致）
- "shot_size": 从 特写|近景|中景|全景 四选一
- "people": 画面里能看清的人数（整数，没人写 0）
- "who": 数组，每人一条特征串，格式「年龄感+性别+主要服装+显著特征」，
  例如 "老年女性 深色旧棉袄 坐轮椅"、"年轻女性 白色婚纱 盘发"、"中年男性 深色西装"
- "mouth_visible": 这张图里**能不能看清至少一个人的嘴部**。填 true 或 false，必须先答这一条。
  只有下面这些情况才填 true：正脸或侧脸清楚、嘴没有被手/道具/头发挡住。
  凡是背影、后脑勺、低头看不见嘴、画面只有手或腿、画面里没有人，**一律 false**。
- "speaking": 这张图里**有没有人的嘴正在张开说话**。填 true 或 false。
  **mouth_visible 为 false 时，这一条必须填 false**，不许凭“气氛像在说话”去猜。
  判据只看嘴：牙齿或口腔可见、口型处于发音状态才 true；嘴闭着、抿着、微笑但不动，一律 false。
- "speaker": speaking 为 true 时，填「是谁在说」——用 "who" 数组里第几个（从 1 开始）；
  没人在说写 "none"；有多个人嘴都在动、分不清写 "several"
- "mouth_note": 一句话说明你看到了什么，例如 "第1位嘴巴张开"、"所有人都背对镜头，看不见嘴"、"只有一只手"
- "emotion": 画面里**主要人物**的情绪，从 愤怒|悲伤|震惊|恐惧|喜悦|冷淡|激动 七选一；
  看不出情绪但人物平静正常，填 "平静"；画面里没有人，填 "无"。
  **这一条必填，不许留空，也不许写"无表情"这种含糊说法**——平静也是一种情绪，写"平静"。
  判断依据是脸：眼眶红不红、有没有泪、眉头皱不皱、嘴角方向、眼神凶不凶；脸看不清就靠姿态
  （肩膀绷紧、身体前倾、后仰躲闪、手指指向对方）。
- "intensity": 情绪有多强，从 强|中|弱 三选一。哭喊、吼、崩溃、指人、落泪算"强"；
  只是语气不好、眉头皱着算"中"；轻声、垂眼、忍着、勉强笑得体算"弱"。
  画面里没有人写空字符串。
- "action": 一句话，画面里的人正在做什么，20 字内
- "place": 场景，如 酒店宴会厅|室内客厅|街边|办公室|走廊，看不出写 "未识别"
- "props": 数组，画面里显眼的具体物件（如 轮椅、麦克风、奖状、行李箱），没有就空数组
- "scene_text": 画面里**除对话字幕以外**的文字（奖状、门牌、横幅、招牌上的字），没有写空字符串
- "watermark": 画面底部或角落的水印/免责小字，有就照抄原文，没有写空字符串

重要约束：
1. **绝对不要猜人名、不要写人物关系**（不写"妈妈""女儿""新郎"），只写看得见的外观特征。
2. **画面下方那行对话字幕不算 scene_text**，那是台词，不要抄进 scene_text。
3. 只描述确实看得见的内容，吃不准写 "未识别"，禁止编造。
4. speaking 只认嘴部形态，**不要因为画面里有人、或在做动作就填 true**。
5. **看不见嘴就必须承认看不见**（mouth_visible=false）。宁可漏掉一句真在说的话，
   也不许把背影、后脑勺、只露一只手的情况报成“正在说话”。"""


def b64_url(p: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()


def salvage_objects(txt: str) -> list[dict]:
    """模型吐的 JSON 数组坏掉时，逐个花括号切对象单独解析，能救几个救几个。

    实测踩过：ep03 第三批返回少了个逗号，json.loads 整批报废、脚本直接崩。
    一个坏字符不该让整集白跑——救不出来的那几张后面单独重试。
    """
    out: list[dict] = []
    depth, start, in_str, esc = 0, None, False, False
    for i, ch in enumerate(txt):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(txt[start:i + 1])
                    if isinstance(obj, dict):
                        out.append(obj)
                except json.JSONDecodeError:
                    try:  # 常见毛病：对象末尾多逗号、或少了逗号
                        fixed = re.sub(r",\s*}", "}", txt[start:i + 1])
                        fixed = re.sub(r'"\s+"', '","', fixed)
                        out.append(json.loads(fixed))
                    except json.JSONDecodeError:
                        pass
                start = None
    return out


def call_vlm(env: dict, frames: list[Path]) -> tuple[list[dict], dict]:
    base = (env.get("MIMO_BASE_URL") or _MIMO_URL).rstrip("/")
    model = env.get("MIMO_MODEL") or _MIMO_MODEL
    url = base + "/chat/completions"
    headers = {"api-key": env["MIMO_API_KEY"], "Content-Type": "application/json"}
    content = [{"type": "image_url", "image_url": {"url": b64_url(p)}} for p in frames]
    content.append({"type": "text", "text": TAG_PROMPT.format(n=len(frames), k=FRAMES_PER_SHOT)})
    payload = {"model": model, "messages": [{"role": "user", "content": content}],
               "temperature": 0.2, "max_tokens": 3000}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    txt = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    txt = re.sub(r"```(?:json)?|```", "", txt).strip()
    m = re.search(r"\[.*\]", txt, re.S)
    if not m:
        raise ValueError(f"模型没返回 JSON 数组，原文前 200 字：{txt[:200]}")
    body = m.group(0)
    try:
        got = json.loads(body)
        if not isinstance(got, list):
            raise ValueError("顶层不是数组")
    except (json.JSONDecodeError, ValueError) as e:
        got = salvage_objects(body)
        print(f"    [容错] JSON 不完整（{e.__class__.__name__}），逐个对象救回 {len(got)}/{len(frames)}", flush=True)
        if not got:
            raise
    return got, data.get("usage", {})


def process_ep(env: dict, video: Path, ep: int, shots: list[dict],
               workdir: Path, use_vlm: bool, batch_jobs: int = 1) -> tuple[list[dict], dict]:
    """每镜头抽 FRAMES_PER_SHOT 帧 → 送 VLM → 按镜头归并成一条。

    v2 起：一镜头三帧（首/中/尾）。单帧会正好撞上闭嘴的那一下，
    同一句话在两个相邻瞬间一个是“张着嘴”一个是“闭着”，只看一帧必错。
    """
    frames: list[Path | None] = []
    for idx, s in enumerate(shots):
        span = max(s["end"] - s["start"], 0.12)
        ts = [s["start"] + span * 0.15,
              (s["start"] + s["end"]) / 2,
              s["end"] - span * 0.15][:FRAMES_PER_SHOT]
        while len(ts) < FRAMES_PER_SHOT:
            ts.append((s["start"] + s["end"]) / 2)
        for k, t in enumerate(ts):
            f = workdir / f"ep{ep:02d}_s{idx:03d}_{k}.jpg"
            frames.append(f if extract_frame(video, t, f) else None)

    def frame_at(sidx: int, k: int) -> Path | None:
        i = sidx * FRAMES_PER_SHOT + k
        return frames[i] if i < len(frames) else None

    def first_frame(sidx: int) -> Path | None:
        for k in range(FRAMES_PER_SHOT):
            f = frame_at(sidx, k)
            if f:
                return f
        return None

    if not use_vlm:
        for idx, s in enumerate(shots):
            f = first_frame(idx)
            s["frame"] = f.name if f else None
        return shots, {}

    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    per_shot: dict[int, list[dict]] = {}

    def apply_tags(chunk: list[tuple[int, Path]], got: list[dict]) -> None:
        """把模型结果贴回镜头。帧下标 // FRAMES_PER_SHOT = 镜头号。"""
        by_i = {int(g.get("i", -1)): g for g in got if isinstance(g, dict)}
        for local_i, (fi, _f) in enumerate(chunk):
            # 模型可能从 1 开始也可能从 0 开始，两种都试
            g = by_i.get(local_i) or by_i.get(local_i + 1) or (got[local_i] if local_i < len(got) else {})
            if not isinstance(g, dict) or not g:
                continue
            per_shot.setdefault(fi // FRAMES_PER_SHOT, []).append(g)

    def ask(chunk: list[tuple[int, Path]]) -> tuple[list[tuple[int, Path]], list[dict], dict]:
        """发一批。返回 (这批帧, 模型结果, 用量)，由调用方合并——并行时不在线程里改共享状态。"""
        try:
            got, usage = call_vlm(env, [f for _, f in chunk])
        except Exception as e:  # 网络抖动、模型抽风都不该让整集崩掉
            print(f"    [ep{ep:02d}][重试] 本批失败：{e.__class__.__name__} {str(e)[:80]}", flush=True)
            return chunk, [], {}
        return chunk, got, usage or {}

    def absorb(usage: dict) -> None:
        for k in usage_total:
            usage_total[k] += int(usage.get(k, 0) or 0)

    chunks: list[list[tuple[int, Path]]] = []
    for base in range(0, len(frames), BATCH):
        c = [(i, frames[i]) for i in range(base, min(base + BATCH, len(frames))) if frames[i]]  # type: ignore[misc]
        if c:
            chunks.append(c)

    marked = 0
    if batch_jobs <= 1 or len(chunks) <= 1:
        for c in chunks:
            ch, got, us = ask(c)
            absorb(us)
            apply_tags(ch, got)
            marked += len(ch)
            print(f"  ep{ep:02d} 已打标 {marked}/{len(frames)} 帧", flush=True)
            if progress:
                progress.update("tag_shots", video.parent.name, ep,
                                done=marked, total=len(frames))
    else:
        # 11-12 加：一批请求要 25 秒上下，全花在等模型上。同一集内部的批次互不依赖，
        # 开几路就能把一集从 6 分钟压到 2 分钟。合并仍放主线程做，不碰共享状态。
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=batch_jobs) as ex:
            futs = [ex.submit(ask, c) for c in chunks]
            for fut in as_completed(futs):
                ch, got, us = fut.result()
                absorb(us)
                apply_tags(ch, got)
                marked += len(ch)
                print(f"  ep{ep:02d} 已打标 {marked}/{len(frames)} 帧", flush=True)
            if progress:
                progress.update("tag_shots", video.parent.name, ep,
                                done=marked, total=len(frames))

    # 兜底：整镜头没贴上标的，把该镜头的帧再单独试一次
    for idx in range(len(shots)):
        if idx in per_shot:
            continue
        chunk = [(idx * FRAMES_PER_SHOT + k, f) for k in range(FRAMES_PER_SHOT)
                 if (f := frame_at(idx, k))]
        if chunk:
            print(f"  ep{ep:02d} 兜底重试镜头 {idx}（{len(chunk)} 帧）", flush=True)
            ch, got, us = ask(chunk)
            absorb(us)
            apply_tags(ch, got)
    still = [i for i in range(len(shots)) if i not in per_shot]
    if still:
        print(f"  [ep{ep:02d}][警告] 仍有 {len(still)} 个镜头没打上标：{still}", flush=True)

    def merge(tags: list[dict]) -> dict:
        """同一镜头的多帧结果归并成一条：描述取人物最全的那帧，说话看有没有任一帧张嘴。"""
        def pick(key: str, default: str = "") -> str:
            for t in tags:
                v = t.get(key)
                if v not in (None, "", "未识别"):
                    return v
            return default

        main = max(tags, key=lambda t: (int(t.get("people", 0) or 0),
                                        len(t.get("who", []) or [])))
        # v3：看不见嘴就不许判说话。三帧里没有一帧能看清嘴，一律按无声处理。
        visible = any(t.get("mouth_visible") is True for t in tags)
        said = next((t for t in tags if t.get("speaking") is True), None) if visible else None
        # note 必须和 speaking 同源：判说话就用那一帧的理由，否则会出现
        # “理由是嘴唇闭合、结论是在说话”这种自相矛盾的记录（11-12 修）
        if said:
            note = said.get("mouth_note", "") or ""
        else:
            note = next((t.get("mouth_note") for t in tags if t.get("mouth_note")), "")
        # 情绪：优先取"正在说话那一帧"的——那句话的情绪才是我们要的。
        # 三帧都没有说话帧时退回人物最全那帧。v5 起是模型直接给的结构化字段，
        # 不再从 action 描述里正则猜（猜不到就空，导致大半句子没情绪）。
        for src in ([said] if said else []) + list(tags):
            if src and (src.get("emotion") or "").strip():
                emotion = src["emotion"].strip()
                intensity = (src.get("intensity") or "").strip()
                break
        else:
            emotion, intensity = "", ""
        return {
            "shot_size": main.get("shot_size", "未识别"),
            "people": int(main.get("people", 0) or 0),
            "who": main.get("who", []) or [],
            "mouth_visible": visible,
            "speaking": bool(said),
            "speaker": ((said or {}).get("speaker") or "none") if said else "none",
            "mouth_note": note,
            "emotion": emotion,
            "intensity": intensity,
            "action": main.get("action", ""),
            "place": pick("place", "未识别"),
            "props": main.get("props", []) or [],
            "scene_text": pick("scene_text", ""),
            "watermark": pick("watermark", ""),
            "frames_used": len(tags),
        }

    for idx, s in enumerate(shots):
        tags = per_shot.get(idx)
        if tags:
            s["tag"] = merge(tags)
        f = first_frame(idx)
        s["frame"] = f.name if f else None
    return shots, usage_total


def run_one_ep(env: dict, video: Path, ep: int, threshold: float, use_vlm: bool,
               batch_jobs: int = 1) -> dict:
    """跑一整集：切镜头 → 抽帧 → 打标。并行时每集一个线程跑这个函数。"""
    drama = video.parent.name
    t0 = time.time()
    if progress:
        progress.start("tag_shots", drama, ep, total=0, unit="帧")
    try:
        dur = probe_duration(video)
        shots = detect_shots(video, dur, threshold)
        print(f"ep{ep:02d}  {dur:.1f}s  切成 {len(shots)} 个镜头", flush=True)

        with tempfile.TemporaryDirectory(prefix=f"sd_shots_ep{ep:02d}_") as td:
            shots, usage = process_ep(env, video, ep, shots, Path(td), use_vlm, batch_jobs)

        payload = {
            "ep": ep,
            "file": video.name,
            "duration": round(dur, 2),
            "shot_count": len(shots),
            "scene_threshold": threshold,
            "prompt_version": PROMPT_VERSION if use_vlm else None,
            "usage": usage,
            # 单集耗时：切镜头 + 抽帧 + 等模型全算在内，主要花在等模型上
            "elapsed_sec": round(time.time() - t0, 1),
            "shots": shots,
        }
        if progress:
            progress.finish("tag_shots", drama, ep, note=f"{len(shots)} 个镜头")
        return payload
    except Exception as e:
        if progress:
            progress.fail("tag_shots", drama, ep,
                          note=f"{e.__class__.__name__}: {str(e)[:100]}")
        raise


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drama", required=True)
    ap.add_argument("--ep", type=int, nargs="+", required=True)
    ap.add_argument("--force", action="store_true", help="已有结果也重跑")
    ap.add_argument("--no-vlm", action="store_true", help="只切镜头不打标")
    ap.add_argument("--threshold", type=float, default=SCENE_THRESHOLD)
    ap.add_argument("--jobs", type=int, default=4,
                    help="并行集数（默认 4）。集与集之间互不依赖，产物按集落盘，结果与串行一致")
    ap.add_argument("--batch-jobs", type=int, default=1,
                    help="同一集内并行几个批次（默认 1=串行）。一批约 25 秒全在等模型，"
                         "调到 2–4 可把一集从 6 分钟压到 2 分钟；总并发 = jobs × batch-jobs，"
                         "开太大可能被服务商限流")
    args = ap.parse_args()

    env = load_env()
    src_root = env.get("SHORT_DRAMA_ASSET_ROOT", "")
    if not src_root:
        sys.exit("未配置 SHORT_DRAMA_ASSET_ROOT（检查 .env）")
    drama_dir = Path(src_root) / args.drama
    if not drama_dir.exists():
        sys.exit(f"素材目录不存在：{drama_dir}")

    out_dir = ROOT / "assets" / "short-drama" / args.drama / "shots"
    out_dir.mkdir(parents=True, exist_ok=True)

    use_vlm = not args.no_vlm
    if use_vlm and not env.get("MIMO_API_KEY"):
        sys.exit("未找到 MIMO_API_KEY（检查 .env）")

    videos = {}
    for p in sorted(drama_dir.glob("*.mp4")):
        m = re.search(r"(?:第)?(\d+)(?:集)?\.mp4$", p.name)
        if m:
            videos[int(m.group(1))] = p

    todo = []
    for ep in args.ep:
        if ep not in videos:
            print(f"ep{ep:02d} 跳过：找不到视频")
            continue
        dst = out_dir / f"shots_ep{ep:02d}.json"
        if dst.exists() and not args.force:
            print(f"ep{ep:02d} 跳过：已存在 {dst.name}")
            continue
        todo.append(ep)
    if not todo:
        print("没有要跑的集")
        return

    jobs = max(1, min(args.jobs, len(todo)))
    t_all = time.time()
    tally = {"eps": 0, "shots": 0, "tokens": 0}   # 本轮总账，记耗时台账用

    def save(payload: dict) -> None:
        dst = out_dir / f"shots_ep{payload['ep']:02d}.json"
        dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tally["eps"] += 1
        tally["shots"] += int(payload.get("shot_count") or 0)
        tally["tokens"] += int((payload.get("usage") or {}).get("total_tokens") or 0)
        print(f"ep{payload['ep']:02d} -> {dst.name}   "
              f"tokens={payload['usage'].get('total_tokens', 0)}  镜头={payload['shot_count']}",
              flush=True)

    if jobs == 1:
        for ep in todo:
            save(run_one_ep(env, videos[ep], ep, args.threshold, use_vlm, args.batch_jobs))
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        print(f"并行 {jobs} 路（集间），每集内 {args.batch_jobs} 路（批间），"
              f"总并发 {jobs * args.batch_jobs}，共 {len(todo)} 集", flush=True)
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(run_one_ep, env, videos[ep], ep, args.threshold, use_vlm,
                              args.batch_jobs): ep
                    for ep in todo}
            for fut in as_completed(futs):
                ep = futs[fut]
                try:
                    save(fut.result())
                except Exception as e:
                    print(f"ep{ep:02d} 失败：{e.__class__.__name__} {str(e)[:120]}", flush=True)

    # 耗时台账：页面上的「画面打标跑了多久、花了多少 token」来自这一条
    if tally["eps"] and analysis_log:
        analysis_log.record(args.drama, "tag_shots", started=t_all,
                            eps=tally["eps"], items=tally["shots"],
                            tokens=tally["tokens"] or None,
                            note=f"集间 {jobs} 路 × 集内 {args.batch_jobs} 路，{PROMPT_VERSION}")

    _rebuild_sqlite(args.drama)


def _rebuild_sqlite(drama: str) -> None:
    """镜头索引变更后重建派生缓存 index.sqlite（11-11 定：有变更即更新，缓存可随时全量重建）。"""
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_index.py")
    try:
        r = subprocess.run([sys.executable, script, "--drama", drama],
                           capture_output=True, text=True, timeout=180)
        print("SQLite 查询缓存已重建") if r.returncode == 0 else \
            print(f"SQLite 重建失败（JSON 不受影响）：{r.stderr.strip()[-200:]}")
    except Exception as e:
        print(f"SQLite 重建跳过（JSON 不受影响）：{e}")


if __name__ == "__main__":
    main()
