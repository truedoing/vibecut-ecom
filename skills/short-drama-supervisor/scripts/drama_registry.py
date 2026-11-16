#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剧的编号与工作状态（后台用，不管网页）。

做什么
    - 给每一部剧一个短号：J1、J2……，沟通时说「J2」不用打剧名
    - 记住这部剧是在做（active）还是交付后归档（archived）
    - 归档后号让出，后面的剧往前补（11-13 张老师拍板"回收、连续"）

为什么单独一个文件、不塞进 runs/工作台.json
    工作台.json 是下游好几个脚本在读的核心文件，往里加字段要逐个确认没人踩。
    编号和状态属于「管理视角」的信息，单独一份，坏了也不影响既有链路。

编号规则
    - 只给**在做**的剧编号，按 drama_order 里的名字顺序排 J1..Jn
    - 归档时把当时是几号记进 archived_code，翻旧记录还能对上
    - 新加的剧默认排最后，也可以用 --at 指定插在第几位

为什么剧名顺序要人工维护、不靠程序排
    这台机器上 zh_CN 的 locale 排序不是真拼音序：实测把「以真心守护至亲」
    排到了「离婚后我靠鸡排翻身」前面（yi 应该在 li 后面）。
    宁可用一份固定名单，也不要每次算出一个不一样的号。

用法
    python3 drama_registry.py                      # 看现在的编号表
    python3 drama_registry.py --json               # 给别的脚本读（含简称表）
    python3 drama_registry.py --resolve 父爱剧      # 简称/编号对成正式剧名
    python3 drama_registry.py --alias 父爱跨越山海 父爱,山海   # 加简称
    python3 drama_registry.py --archive 父爱剧      # 归档，号让出、后面往前补
    python3 drama_registry.py --activate 父爱剧     # 恢复在做，排到末尾
    python3 drama_registry.py --add 剧名 [--at 2]   # 登记一部新剧
    python3 drama_registry.py --check              # 只查不一致，不改
    任何写盘动作加 --dry-run 就只打印不落盘。

简称怎么认（11-13 张老师加的一条）
    他的原话："沟通的时候通过简称也要支持，比如父爱剧，意思就是父爱跨越山海，
    这比编号好记。这个能力就是专家或者主管技能要具备。"
    所以 --archive / --activate / --resolve 都先过一道简称识别：
    编号（J1）→ 剧名原样 → 登记的简称 → 去掉「剧／片子」尾巴再认 → 两字以上的包含匹配。
    **对出好几部就停下来把候选列出来**，绝不挑一个猜——改错一部剧比多问一句贵得多。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REG_NAME = "剧注册表.json"
STATUS_LABEL = {"active": "在做", "archived": "已归档"}

# 口语里挂在剧名后面的尾巴，匹配前先去掉：「父爱剧」「父爱那部剧」「鸡排片子」
SUFFIXES = ("那部剧", "这部剧", "这一部", "那部", "这部", "的剧", "片子", "剧", "片")


def find_root() -> Path:
    """数据目录只认一个出口：_env.data_root()（11-16 修）。

    以前这里沿 __file__ 往上找 runs/工作台.json——技能装进 ~/.workbuddy/skills
    或专家包里之后，那条链一路翻到 C:\\ 也碰不到工作台，模块一导入就抛异常。
    而进度网页的 server.py 会 import 本模块，于是 A1 修完还会崩在这一处，
    报错一字不差，极容易误判成「没修好」。
    """
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import _env

    return _env.data_root()


ROOT = find_root()
RUNS = ROOT / "runs"
REG_PATH = RUNS / REG_NAME
ASSETS = ROOT / "assets" / "short-drama"


def jload(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save(reg: dict, write: bool) -> None:
    """原子写：先写临时文件再替换，中途断电不会留下半个 JSON。"""
    if not write:
        return
    tmp = REG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(REG_PATH)


def known_dramas() -> list[str]:
    """这部剧库里的剧从哪来：工作台登记的 ∪ 素材盘有原视频的。

    素材目录可能用全名（八零憨妻 → 八零憨妻的甜蜜日常），要按工作台里的
    asset_dir 对回短名，否则同一部剧会算成两部、占两个号。
    """
    ws = jload(RUNS / "工作台.json", {}) or {}
    dramas = ws.get("dramas") or {}
    names = list(dramas.keys())
    alias = {}
    for short, info in dramas.items():
        ad = (info or {}).get("asset_dir") or ""
        if ad:
            alias[Path(ad).name] = short
    if ASSETS.exists():
        for p in ASSETS.iterdir():
            if not p.is_dir() or p.name.startswith(".") or p.name == "ai_assets":
                continue
            name = alias.get(p.name, p.name)
            if name not in names:
                names.append(name)
    return names


def strip_suffix(text: str) -> str:
    """去掉口语尾巴：「父爱剧」→「父爱」。至少留两个字才敢去，免得把两字剧名削光。"""
    t = (text or "").strip()
    for suf in SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= 2:
            return t[: len(t) - len(suf)]
    return t


def match_drama(text: str, reg: dict) -> dict:
    """把口语里的叫法对到正式剧名。沟通时说「父爱剧」「鸡排」「J3」都认。

    对上的顺序（从确定到宽松，碰到就停）：
      1. 编号 J1 / j1
      2. 剧名一字不差
      3. 登记过的简称（注册表 aliases）
      4. 去掉「剧／片子」这类尾巴后，再查剧名和简称
      5. 双向包含：输入是剧名的一段，或剧名是输入的一段（两字以上才这么找）

    **多个都能对上时不猜**，把候选原样交回去让人挑——猜错比问一句贵得多。

    返回 {"ok": bool, "name": 剧名或 None, "how": 怎么对上的, "candidates": [...], "status": ...}
    """
    t = (text or "").strip()
    if not t:
        return {"ok": False, "name": None, "how": "没给叫法", "candidates": []}

    dramas = reg.get("dramas") or {}
    order = reg.get("drama_order") or []
    names = [n for n in order if n in dramas] + [n for n in dramas if n not in order]

    def hit(name: str, how: str) -> dict:
        info = dramas.get(name) or {}
        return {"ok": True, "name": name, "how": how, "candidates": [],
                "status": info.get("status"),
                "code": info.get("code"),
                "archived_code": info.get("archived_code")}

    # 1 编号
    m = re.fullmatch(r"[Jj]\s*(\d+)", t)
    if m:
        code = f"J{int(m.group(1))}"
        for n in names:
            if dramas[n].get("code") == code:
                return hit(n, f"按编号 {code}")
        return {"ok": False, "name": None, "candidates": [],
                "how": f"编号 {code} 现在没分给任何一部在做的剧"}

    # 2 剧名一字不差
    if t in dramas:
        return hit(t, "剧名原样")

    # 3 登记过的简称
    for n in names:
        if t in (dramas[n].get("aliases") or []):
            return hit(n, f"登记过的简称「{t}」")

    # 4 去掉尾巴再来一遍
    base = strip_suffix(t)
    if base != t:
        if base in dramas:
            return hit(base, f"去掉「{t[len(base):]}」后就是剧名")
        for n in names:
            if base in (dramas[n].get("aliases") or []):
                return hit(n, f"去掉「{t[len(base):]}」后对上了简称「{base}」")

    # 5 双向包含，两字以上才这么找（一个字太容易误中）
    probe = base
    if len(probe) >= 2:
        found = [n for n in names if probe in n or n in probe]
        if len(found) == 1:
            return hit(found[0], f"「{probe}」只有这一部对得上")
        if len(found) > 1:
            return {"ok": False, "name": None, "candidates": found,
                    "how": f"「{probe}」能对上 {len(found)} 部，不知道说的是哪部"}

    return {"ok": False, "name": None, "candidates": [],
            "how": f"没有哪部剧对得上「{t}」"}


def resolve(reg: dict) -> dict[str, str]:
    """按名单顺序给在做（active）的剧连续编号。"""
    order = reg.get("drama_order") or []
    out, n = {}, 0
    for name in order:
        if (reg["dramas"].get(name) or {}).get("status") == "active":
            n += 1
            out[name] = f"J{n}"
    return out


def sync(reg: dict) -> list[tuple[str, str | None, str | None]]:
    """重算每部剧的号，返回变化清单 [(剧名, 旧号, 新号)]。"""
    new = resolve(reg)
    changed = []
    for name, info in (reg.get("dramas") or {}).items():
        old = info.get("code")
        info["code"] = new.get(name)
        if old != info["code"]:
            changed.append((name, old, info["code"]))
    return changed


def cmd_init(order: str | None, write: bool) -> dict:
    """第一次生成注册表：把认识的剧都写进去，默认都算在做。"""
    names = known_dramas()
    if order:
        seq = [x.strip() for x in order.split(",") if x.strip()]
        missing = [n for n in names if n not in seq]
        seq += missing           # 名单里漏掉的补在后面，不丢剧
    else:
        seq = sorted(names)      # 没给顺序就按字面排，之后可以改
    reg = {
        "_readme": "剧的编号与工作状态。只给在做（active）的剧编 J1..Jn，按 "
                   "drama_order 的顺序；归档后号让出、后面的往前补，归档那一刻的号记在 "
                   "archived_code 里。drama_order 是人工维护的名字顺序，理由见脚本 docstring。",
        "drama_order": seq,
        "dramas": {n: {"status": "active", "code": None} for n in seq},
    }
    sync(reg)
    save(reg, write)
    return reg


def load(write_if_missing: bool) -> dict:
    reg = jload(REG_PATH, None)
    if reg is None:
        raise SystemExit(f"还没有 {REG_NAME}，先跑一次 --init（可带 --order 指定名字顺序）")
    reg.setdefault("drama_order", [])
    reg.setdefault("dramas", {})
    return reg


def pct_line(tag: str, ok: bool) -> str:
    return f"[{tag}] {'通过' if ok else '有问题'}"


def cmd_check(reg: dict) -> int:
    """查三种不一致：名单里有但没记录、有记录但不在名单、号重复。"""
    order = reg.get("drama_order") or []
    dramas = reg.get("dramas") or {}
    bad = 0
    for n in order:
        if n not in dramas:
            print(f"  [缺] 名单里有「{n}」，但没有它的状态记录"); bad += 1
    for n in dramas:
        if n not in order:
            print(f"  [多] 有「{n}」的记录，但不在名字名单里，永远排不上号"); bad += 1
    codes = [i.get("code") for i in dramas.values() if i.get("code")]
    if len(codes) != len(set(codes)):
        print(f"  [重] 编号重复：{codes}"); bad += 1
    print(pct_line("校验", bad == 0))
    return 1 if bad else 0


def show(reg: dict) -> None:
    dramas = reg.get("dramas") or {}
    order = reg.get("drama_order") or []
    act = [n for n in order if (dramas.get(n) or {}).get("status") == "active"]
    arc = [n for n in order if (dramas.get(n) or {}).get("status") == "archived"]
    print(f"在做 {len(act)} 部 · 已归档 {len(arc)} 部\n")
    print(f"{'编号':<5}{'状态':<7}剧名")
    for n in act:
        print(f"{dramas[n]['code']:<6}{'在做':<7}{n}")
    for n in arc:
        info = dramas[n]
        was = info.get("archived_code") or "—"
        when = info.get("archived_at") or ""
        print(f"{'—':<6}{'已归档':<6}{n}（归档时是 {was} {when}，号已让出）")


def need_drama(text: str, reg: dict) -> str:
    """把用户嘴里的叫法换成正式剧名；对不上或对出好几个就停下来说清楚。"""
    m = match_drama(text, reg)
    if m["ok"]:
        return m["name"]
    lines = [m["how"]]
    for n in m.get("candidates") or []:
        info = (reg.get("dramas") or {}).get(n) or {}
        tag = info.get("code") or "已归档"
        lines.append(f"  - {n}（{tag}）")
    lines.append("  说全名或编号，我再对一次。")
    raise SystemExit("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(description="剧的编号、工作状态与简称")
    ap.add_argument("--init", action="store_true", help="第一次生成注册表")
    ap.add_argument("--order", default=None, help="初始名字顺序，逗号分隔，按拼音先后")
    ap.add_argument("--archive", metavar="剧", help="标记为已归档，号让出（可用简称）")
    ap.add_argument("--activate", metavar="剧", help="恢复为在做，排到末尾（可用简称）")
    ap.add_argument("--add", metavar="剧名", help="登记一部新剧")
    ap.add_argument("--at", type=int, default=None, help="配合 --add，插在第几位（从 1 数）")
    ap.add_argument("--resolve", metavar="叫法", help="把简称/编号对成正式剧名，对不上或对出多部会停")
    ap.add_argument("--alias", nargs=2, metavar=("剧", "简称"),
                    help="给某部剧加简称，多个用逗号隔开，例：--alias 父爱跨越山海 父爱,山海")
    ap.add_argument("--unalias", nargs=2, metavar=("剧", "简称"), help="删掉一个简称")
    ap.add_argument("--check", action="store_true", help="只查不一致，不改")
    ap.add_argument("--json", action="store_true", help="输出机器可读的编号表")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写盘")
    a = ap.parse_args()
    write = not a.dry_run

    if a.init:
        if REG_PATH.exists() and not a.dry_run:
            raise SystemExit(f"{REG_NAME} 已存在。要重排请用 --activate / --archive / --add，别重生成。")
        reg = cmd_init(a.order, write)
        print(f"已生成 {REG_PATH.relative_to(ROOT)}" if write else "（试运行，未写盘）")
        show(reg)
        return 0

    reg = load(write)
    dramas = reg["dramas"]
    changed: list = []

    if a.add:
        name = a.add
        if name in dramas:
            print(f"「{name}」已经登记过了，号是 {dramas[name].get('code') or '—（归档）'}")
        else:
            dramas[name] = {"status": "active", "code": None}
            if a.at and 1 <= a.at <= len(reg["drama_order"]) + 1:
                reg["drama_order"].insert(a.at - 1, name)
            else:
                reg["drama_order"].append(name)
            changed = sync(reg)
            save(reg, write)
            print(f"已登记「{name}」")
            for n, o, nn in changed:
                if o != nn:
                    print(f"  号变动：{n} {o or '—'} → {nn or '—'}")

    if a.alias:
        target, shorts = a.alias
        name = need_drama(target, reg)
        info = dramas[name]
        al = info.setdefault("aliases", [])
        added, clash = [], []
        for s in re.split(r"[,，、\s]+", shorts):
            s = s.strip()
            if not s or s == name:
                continue
            # 新简称如果已经指向别的剧，收下来会让人越对越乱，先拦
            probe = match_drama(s, reg)
            if probe["ok"] and probe["name"] != name:
                clash.append((s, probe["name"]))
                continue
            if s not in al:
                al.append(s)
                added.append(s)
        save(reg, write)
        print(f"「{name}」的简称：{'、'.join(al) if al else '（还没有）'}")
        if added:
            print(f"  新加：{'、'.join(added)}" + ("" if write else "（试运行，未写盘）"))
        for s, other in clash:
            print(f"  [没加] 「{s}」已经指向「{other}」，要换就先 --unalias")
        return 0

    if a.unalias:
        target, short = a.unalias
        name = need_drama(target, reg)
        al = dramas[name].get("aliases") or []
        if short in al:
            al.remove(short)
            save(reg, write)
            print(f"已从「{name}」删掉简称「{short}」")
        else:
            print(f"「{name}」没有简称「{short}」")
        return 0

    if a.resolve:
        m = match_drama(a.resolve, reg)
        if m["ok"]:
            info = dramas[m["name"]]
            if info.get("status") == "active":
                tag = info.get("code") or "在做"
            else:
                tag = f"已归档，当时是 {info.get('archived_code') or '—'}"
            print(f"{a.resolve} → {m['name']}（{tag}）")
            print(f"  怎么对上的：{m['how']}")
            return 0
        print(m["how"])
        cand = m.get("candidates") or []
        for n in cand:
            info = dramas.get(n) or {}
            print(f"  - {n}（{info.get('code') or '已归档'}）")
        if cand:
            print("\n  说全名或编号，我再对一次。")
        return 2

    if a.archive:
        name = need_drama(a.archive, reg)
        if name not in dramas:
            raise SystemExit(f"没这部：「{name}」。先 --add 登记")
        if dramas[name].get("status") == "archived":
            print(f"「{name}」已经是归档状态")
        else:
            dramas[name]["archived_code"] = dramas[name].get("code")
            dramas[name]["status"] = "archived"
            dramas[name]["archived_at"] = datetime.now().strftime("%Y-%m-%d")
            dramas[name]["code"] = None
            changed = sync(reg)
            save(reg, write)
            print(f"已归档「{name}」（归档时是 {dramas[name]['archived_code']}，号让出）")
            for n, o, nn in changed:
                if o != nn:
                    print(f"  号变动：{n} {o or '—'} → {nn or '—'}")

    if a.activate:
        name = need_drama(a.activate, reg)
        if name not in dramas:
            raise SystemExit(f"没这部：「{name}」。先 --add 登记")
        dramas[name]["status"] = "active"
        dramas[name].pop("archived_at", None)
        dramas[name].pop("archived_code", None)
        if name not in reg["drama_order"]:
            reg["drama_order"].append(name)
        changed = sync(reg)
        save(reg, write)
        print(f"已恢复「{name}」为在做，按名字顺序重新排号")
        for n, o, nn in changed:
            if o != nn:
                print(f"  号变动：{n} {o or '—'} → {nn or '—'}")

    if a.check:
        return cmd_check(reg)

    if a.json:
        print(json.dumps({
            "active": {n: i["code"] for n, i in dramas.items()
                       if i.get("status") == "active" and i.get("code")},
            # 归档的剧没有当前号，"was" 是归档那一刻的号，只为翻旧记录对得上
            "archived": {n: {"was": i.get("archived_code"), "since": i.get("archived_at")}
                         for n, i in dramas.items() if i.get("status") == "archived"},
            # 简称给两处用：沟通时人对剧的称呼、页面搜索框
            "aliases": {n: (i.get("aliases") or []) for n, i in dramas.items()},
            "order": reg["drama_order"],
        }, ensure_ascii=False, indent=1))
        return 0

    show(reg)

    # 当前在做的那部被归档了，得提醒换一个，不然工作台指着一部归档剧
    ws = jload(RUNS / "工作台.json", {}) or {}
    cur = (ws.get("active") or {}).get("drama")
    if cur and (dramas.get(cur) or {}).get("status") == "archived":
        print(f"\n注意：工作台 active 还指着「{cur}」，它已经归档了，该换个在做的。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
