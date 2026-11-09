#!/usr/bin/env python3
"""拍摄清单生成器 —— 无厂商素材品类（书/课程/知识品）的素材方案落地件。

核心判断：这类品类的素材主体不是「某厂家的产品」，而是「书这种物体 + 阅读这个动作」。
所以素材拆成两层：通用层（一次拍，跨书永久复用）+ 单书层（每书一次，机位固定）。

用法:
  python3 shotlist.py packs/被讨厌的勇气/category-pack.json --slug bydy
  python3 shotlist.py packs/xxx/category-pack.json --slug xxx --out packs/xxx/拍摄清单.md
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# ── 通用层：不出现可读书名/出版社，只有纸页质感与生活场景 ────────────────────
# (编号, 镜头名, 动作与机位, 时长, 要点)
GENERIC = [
    ("GEN_desk_lamp",   "台灯开灯，桌面书本剪影", "关顶灯，只留一盏台灯侧逆光，手按开关入画", "3s", "书感定调镜头，每条片子都能用"),
    ("GEN_page_turn",   "手指翻页特写",           "俯拍纸页边缘，手指从右下角捻起翻过", "3s", "只拍纸与手指，字必须虚焦"),
    ("GEN_flip_fast",   "快速翻页扇动",           "侧面平视，拇指压书页快速放开", "3s", "表现「翻得动」，转场用"),
    ("GEN_book_close",  "书合上推远",             "正俯拍，手合书后向镜头推 20cm", "3s", "段落收尾用"),
    ("GEN_stack_side",  "三本书摞起侧面",         "侧平视，拍纸页堆叠纹理", "3s", "厚度/分量感，不拍书名"),
    ("GEN_shelf_pull",  "从书架抽出一本",         "手伸入书架抽书，只听纸页摩擦", "3s", "书架背景必须虚焦"),
    ("GEN_shelf_put",   "书插回书架",             "反向动作，书脊朝外推进去", "3s", "与上一条配对做前后呼应"),
    ("GEN_sticky",      "撕便签贴页",             "特写，撕下便签贴到页边，背面朝镜头", "3s", "不要出现便签上的字"),
    ("GEN_pen_line",    "笔尖划线特写",           "极近特写，笔尖在纸上划一道线", "4s", "只拍笔尖与纸，字虚焦"),
    ("GEN_note_write",  "笔记本抄句子",           "俯拍，手写字，纸上只露笔迹不露内容", "3s", "「读进去了」的最强自证动作"),
    ("GEN_nightstand",  "床头柜台灯+书+水杯",     "中景固定，暖光，画面里有人生活痕迹", "3s", "睡前场景，钩子与 CTA 都用"),
    ("GEN_sofa",        "沙发扶手上的书",         "低角度，书摊开搭在扶手，手入画", "3s", "客厅场景"),
    ("GEN_dining",      "餐桌上摊开的书",         "俯拍，旁边一副碗筷/一杯水", "3s", "「吃完翻两页」"),
    ("GEN_cup",         "茶/咖啡杯与摊开的书",     "俯拍 45°，热气可见", "3s", "通用空镜，转场补空"),
    ("GEN_window",      "窗边光斑落在纸上",       "逆光，让树影/窗框光斑扫过页面", "3s", "氛围镜头，AI 也能出，可二选一"),
    ("GEN_bag",         "包里的书",               "俯拍，打开通勤包，书露出一角", "3s", "「带着走」，不拍书名"),
    ("GEN_parcel",      "撕塑封/拆快递",           "手撕塑封口，纸箱打开", "3s", "开箱感，任何书通用"),
    ("GEN_box_old",     "纸箱里的旧书堆",         "俯拍纸箱，旧书乱摞，角落有捆绳", "3s", "废品站/处理旧物类钩子专用"),
    ("GEN_clock_night", "台灯下的钟/窗外夜色",     "中景，画面里有时间参照物", "3s", "「一晚上」类时间表达"),
]

# ── 单书层：每本书一次，机位固定、动作固定，只有书本身在换 ────────────────────
# (编号, 镜头名, 动作与机位, 时长, 要点)；要点里的 {qN} 由 golden_quotes 填充
PER_BOOK = [
    ("cover_front",   "封面正面平铺",        "正俯拍，书放桌面正中，无手入画", "3s", "商品主图口径，四边留白"),
    ("cover_angle",   "封面 45° 斜面",       "侧 45°，要拍出书的厚度与书口", "3s", "立体感，别拍成平面图"),
    ("cover_hand",    "手持封面朝镜头",      "手托书脊，封面正对镜头缓慢推近", "3s", "1.5s 处定格即可当钩子首帧"),
    ("spine",         "书脊立起",            "书立起，侧平视，书脊占满画面高度", "3s", "书名+出版社可读，唯一允许字清晰的镜头之一"),
    ("back",          "封底（定价/条码）",   "平铺俯拍封底", "3s", "定价可读，用于价格段；条码不要特写做防伪暗示"),
    ("flap",          "腰封/勒口推荐语",     "撕下腰封平放，或翻开勒口", "3s", "名人推荐若未经核实，此镜头不上片"),
    ("flyleaf",       "翻开扉页",            "手掀封面到扉页停住", "3s", "开篇感"),
    ("toc_slide",     "目录页缓慢上移",      "俯拍目录，镜头缓速上移一拍到底", "4s", "图书的「配料表」，L1 举证主力"),
    ("toc_point",     "手指点住某一章",      "食指按在某一章名上停 1s", "3s", "把结构讲成「五夜」"),
    ("copyright",     "版权页/印次页",       "平铺特写，拍印次与出版年份", "3s", "「重印 115 次」的证据底片"),
    ("quote_page_1",  "金句页 ① {q1}",      "手指按住该句所在段落，压住不划", "3s", "书翻到真实页码，不要摆拍空白页"),
    ("quote_page_2",  "金句页 ② {q2}",      "同上", "3s", ""),
    ("quote_page_3",  "金句页 ③ {q3}",      "同上", "3s", ""),
    ("underline",     "划线过金句",          "笔在句子下划一道横线，划完停 1s", "4s", "划线镜头比翻页更有说服力"),
    ("sticky_mark",   "便签贴金句页",        "撕便签贴上页边，让书角鼓起来", "3s", "「读过多遍」的物证"),
    ("thickness",     "书页侧切厚度",        "侧平视极近拍书口纸页纹理", "2s", "194 页的真实厚度"),
    ("close_desk",    "合书放桌",            "合书，手掌压一下封面", "3s", "段落收尾"),
    ("handover",      "递书向镜头",          "手持书递向镜头，封面朝镜头", "3s", "CTA「给自己添一本」"),
    ("with_others",   "与别的书并置",        "本书与 2–3 本其它书平放对比", "3s", "厚度/开本对比，别让其它书封面抢镜"),
    ("bedside",       "放床头柜",            "放好，手撤出画面", "3s", "睡前场景"),
    ("dining",        "放餐桌",              "放好，旁边有碗筷", "3s", "「吃饭时翻两页」"),
    ("into_bag",      "塞进包里",            "书滑入包内，包扣上", "3s", "「带着走」"),
    ("random_open",   "随手翻到中间",        "书脊一松，自然翻开停在中间某页", "3s", "最像真读的镜头"),
    ("read_hands",    "双手捧书阅读",        "只拍手与书下半部，不拍脸", "3s", "代替 AI 人物，真人感更稳"),
]

# ── 网络层：CC0 素材站兜底空镜（检索词 + 禁用清单）────────────────────────────
NET = [
    ("图书馆书架长廊", "library bookshelf aisle", "平视推进，无人"),
    ("雨窗/雪窗",     "rain on window cozy",      "窗玻璃雨痕，室内暖光"),
    ("通勤阅读",      "reading on train commute", "只取手与书，人脸虚焦可裁"),
    ("城市夜景",      "city night window",        "灯串/窗格，情绪空镜"),
    ("书页特写微距",  "book pages macro",         "纸纹/墨迹微距"),
    ("书房全景",      "cozy home library",        "书桌+台灯+椅，无人"),
]
NET_BAN = [
    "他人可辨识面孔（肖像权，且与「自己读」叙事冲突）",
    "别人的书封面清晰可读（等于给别人做广告）",
    "教材/教辅/成功学封面（品类串味）",
    "医院、诊室、心理咨询室（心理疗效暗示，红线）",
    "带平台水印或需署名（CC0 之外一律不用）",
]

# ── AI 层：人物与情绪，走 ecommerce-video-gen ───────────────────────────────
AI = [
    ("钩子段主角（1 镜）", "30–45 岁父母或职场人，室内暖光，非明星脸，写实生活感", "封面文字锁死虚焦，禁止生成可读汉字；人群按包内 audiences 选"),
    ("情绪特写（1 镜）",   "低头看书的侧脸、手背擦眼角（可裁掉）", "不做疗效暗示的表情（如顿悟/释然大笑）"),
    ("场景补空（1–2 镜）", "台灯下的书桌远景、窗外夜色", "可用通用层实拍替代，能实拍就别 AI"),
]

RULES = [
    ("画幅", "竖屏 9:16、1080×1920、30fps（手机原生即可，不要后期裁）"),
    ("单条时长", "3–5 秒；开头留 0.5 秒静止，剪辑好切入"),
    ("机位", "固定不动，三脚架或靠墙靠书堆；**不允许变焦/推拉**（后期用关键帧做）"),
    ("光线", "关顶灯 + 一盏台灯侧逆光；白平衡锁定，关自动曝光（否则翻页时忽明忽暗）"),
    ("手", "素色袖口，指甲干净；同一批尽量同一双手（跨书复用才连得上）"),
    ("书", "**先撕塑封再拍**（反光毁素材）；书脊压平但不折断"),
    ("命名", "`GEN_<镜头>_01.mp4` / `BK_<书slug>_<镜头>_01.mp4`，一条一文件，不合并"),
    ("存放", "`assets/books/gen/`（通用层）与 `assets/books/<slug>/`（单书层）"),
    ("入库", "`vibecut assets ingest --project <项目名> --file <文件> --tags <层>,<镜头>`"),
    ("可读字", "全片只有 spine / toc / copyright / back 四个镜头允许文字清晰，其余一律虚焦"),
    ("人", "**不用露脸**：只拍手、侧身、背影。图书带货不需要真人背书，露脸反而多一道审核；"
     "一个人一天拍十几本书，靠的就是不用化妆不用出镜"),
]


def build(pack: dict, slug: str) -> str:
    meta = pack.get("meta") or {}
    name = (pack.get("product_facts") or [{}])[0].get("value") or meta.get("category", "")
    bk = re.search(r"《[^》]+》", str(name))
    name = bk.group(0) if bk else name
    quotes = [q.get("text", "") for q in (pack.get("elements") or {}).get("golden_quotes") or []]
    q = [(quotes + ["（待补金句页）"] * 3)[i] for i in range(3)]

    L: list[str] = []
    L.append(f"# 拍摄清单 · {name}")
    L.append("")
    L.append(f"> 由 `shotlist.py` 从 `category-pack.json` 自动生成。slug = `{slug}`。"
             "打印出来拍一条勾一条。")
    L.append("")
    L.append("## 这套清单为什么是两层")
    L.append("")
    L.append("书不是厂家特有的产品——素材主体是「书这种物体 + 阅读这个动作」，与具体哪本书无关。")
    L.append("所以 **19 条通用层拍一次、以后每本书都能用**，每本新书只补 **24 条单书层**（约 25 分钟）。")
    L.append("第十本书的边际拍摄成本趋近于 0。")
    L.append("")

    L.append("## 一、通用层 GEN（一次拍，跨书永久复用）")
    L.append("")
    L.append("| # | 编号 | 镜头 | 动作与机位 | 时长 | 要点 |")
    L.append("|---|---|---|---|---|---|")
    for i, (sid, shot, act, dur, note) in enumerate(GENERIC, 1):
        L.append(f"| {i} | `{sid}` | {shot} | {act} | {dur} | {note} |")
    L.append("")
    L.append("**拍这一层的纪律**：画面里不要出现能读出书名的书。翻页只拍纸边、不拍字。"
             "拍完统一入库，之后所有书共用。")
    L.append("")

    L.append(f"## 二、单书层 BK_{slug}（每书一次，约 25 分钟）")
    L.append("")
    L.append("| # | 编号 | 镜头 | 动作与机位 | 时长 | 要点 |")
    L.append("|---|---|---|---|---|---|")
    for i, (sid, shot, act, dur, note) in enumerate(PER_BOOK, 1):
        shot = shot.replace("{q1}", q[0]).replace("{q2}", q[1]).replace("{q3}", q[2])
        L.append(f"| {i} | `BK_{slug}_{sid}` | {shot} | {act} | {dur} | {note} |")
    L.append("")
    L.append("**金句页怎么找**：按包内 `golden_quotes` 找到真实页码，翻到那一页再拍；"
             "不要摆拍空白页，页码与上下文必须对得上（观众会暂停看）。")
    L.append("")

    L.append("## 三、网络层 NET（CC0 素材站，兜底空镜）")
    L.append("")
    L.append("| 用途 | 英文检索词（Pexels / Pixabay / Coverr / Mixkit） | 备注 |")
    L.append("|---|---|---|")
    for use, kw, note in NET:
        L.append(f"| {use} | `{kw}` | {note} |")
    L.append("")
    L.append("**禁用清单**（下载前先过这一遍）：")
    for b in NET_BAN:
        L.append(f"- {b}")
    L.append("")
    L.append("网络素材只占 ~10%，作用是补「图书馆/城市/通勤」这类自家拍不到的空镜；"
             "**主叙事镜头必须实拍**——观众认得出「这本书真的在他手上」。")
    L.append("")

    L.append("## 四、AI 层（人物与情绪，走 ecommerce-video-gen）")
    L.append("")
    L.append("| 用途 | 提示词方向 | 硬约束 |")
    L.append("|---|---|---|")
    for use, kw, note in AI:
        L.append(f"| {use} | {kw} | {note} |")
    L.append("")
    L.append("AI 在图书品类里的性价比低于食品：**书可以实拍，人不好实拍**。"
             "所以 AI 只补人物，不补书；能让书实拍的一律实拍（真人手比 AI 手可信）。")
    L.append("")

    L.append("## 五、拍摄技术规范")
    L.append("")
    L.append("| 项 | 规范 |")
    L.append("|---|---|")
    for k, v in RULES:
        L.append(f"| {k} | {v} |")
    L.append("")

    L.append("## 六、拍摄进度（打印勾选）")
    L.append("")
    L.append("### 通用层")
    for sid, shot, *_ in GENERIC:
        L.append(f"- [ ] `{sid}` {shot}")
    L.append("")
    L.append(f"### 单书层 {slug}")
    for sid, shot, *_ in PER_BOOK:
        L.append(f"- [ ] `BK_{slug}_{sid}` {shot}")
    L.append("")
    L.append("---")
    L.append("")
    L.append(f"合计：通用层 {len(GENERIC)} 条 + 单书层 {len(PER_BOOK)} 条 + "
             f"网络层 {len(NET)} 类 + AI {len(AI)} 镜。")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pack")
    ap.add_argument("--slug", default=None, help="单书层编号用的 ASCII 短名，如 bydy")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    p = Path(a.pack)
    pack = json.loads(p.read_text(encoding="utf-8"))
    slug = a.slug or p.parent.name
    out = Path(a.out) if a.out else p.parent / "拍摄清单.md"
    out.write_text(build(pack, slug), encoding="utf-8")
    print(f"已生成 {out}（{len(build(pack, slug).splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
