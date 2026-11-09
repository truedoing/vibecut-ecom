#!/usr/bin/env python3
"""给 97 个冲突段落打语义标签，产出 asset_index.json v2（跨集锚点池）。

分工：
- segment.py 负责机械切分（时间边界）
- 本文件负责语义判断（临界状态 / 主题 / 视角 / 强度 / 钩子适配 / 风险）

判断依据全部来自台词本身，不引入画面推断（画面核验留给锚点筛出来之后做）。
标签是人工判读结果，不是模型自动生成 —— 这是 v2 与 v1 最大的区别：
v1 只有「冲突强度」，v2 多了「临界/已兑现」，因为后者决定能不能做钩子。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# ── 跨集主题（栏目单元的最小单位）────────────────────────────────
# 判据：能用一个「看法」把 3-4 个不相邻的画面串起来，且不依赖剧情顺序。
THEMES = {
    "T1": {"name": "门槛", "claim": "这家人一开始不让她进门",
           "voice": "「那天她站在门口，我在院里晾着衣裳」"},
    "T2": {"name": "傻子的帽子", "claim": "所有人都叫她傻子，她一次次自己把名字说回来",
           "voice": "「傻？她比咱院里明白人都清楚」"},
    "T3": {"name": "借米不还", "claim": "一个邻居的小恶，从一斗米滚到一垄菜苗",
           "voice": "「咱村有些人，借东西不为穷，是习惯了占」"},
    "T4": {"name": "力气换来的位置", "claim": "她没别的本钱，只有一身力气，可这身力气救了人",
           "voice": "「那闺女一身力气，别人拿它当笑话，她拿它当本钱」"},
    "T5": {"name": "婆婆心软的三次", "claim": "最狠的那个婆婆，是怎么一步步改口的",
           "voice": "「当婆婆的也有当婆婆的难，可她的心是一点点化的」"},
    "T6": {"name": "一碗粥的往返", "claim": "一碗粥端过去、门口碗空了、灶上抹油，全是不说出口的",
           "voice": "「这院里最亲的话，都是隔着门说的」"},
    "T7": {"name": "旧账上门", "claim": "日子刚好起来，当年那些人就都回来了",
           "voice": "「人一好起来，旧账就自己找上门」"},
    "T8": {"name": "从陪嫁到陪嫁", "claim": "一份陪嫁从嫌弃到传给亲闺女，走完了全剧",
           "voice": "「一样东西，从给不出去，到给得心疼」"},
}

# ── 97 段边界帧判读（L3，11-08 补）────────────────────────────
# 来源：edge_frames.py 抽的 194 帧（每段头尾各一帧），拼图后逐张人工判读。
# 只判三件事：画面人数 / 场景 / 出口形态。**不猜，判不清就留空。**
# ex 五类（决定这段从哪儿切得出去 → 王婶能不能在段落后"场景二扩"）：
#   人出画 = 走远/出门/走过，有明确方向   → 最顺
#   边界物 = 门洞/路口/墙头/田埂在画面里  → 天然是相邻空间的入口
#   空镜   = 无人物                      → 最自由，但要自己造衔接
#   自顾   = 单人在画面内做自己的事       → 可切，且切过去最像同一片田野
#   锁空间 = 双人对话/正反打/面部特写     → 切出去就断，只能画外音插话
EDGE: dict[str, dict] = {
    # EP01
    "E101": dict(p="2", sp="院子", ex="锁空间"),
    "E102": dict(p="1", sp="院子", ex="边界物"),
    "E103": dict(p="1", sp="院子", ex="锁空间"),
    "E104": dict(p="1", sp="院子", ex="人出画"),
    "E105": dict(p="1", sp="路上", ex="人出画"),
    "E106": dict(p="1", sp="田里", ex="自顾"),
    "E107": dict(p="2", sp="门口", ex="锁空间"),
    "E108": dict(p="2", sp="门口", ex="锁空间"),
    "E109": dict(p="1", sp="门口", ex="自顾"),
    "E110": dict(p="2", sp="门口", ex="锁空间"),
    # EP02 夜戏，全在门口/屋内
    "E201": dict(p="3", sp="门口", ex="锁空间"),
    "E202": dict(p="2", sp="门口", ex="锁空间"),
    "E203": dict(p="2", sp="门口", ex="锁空间"),
    "E204": dict(p="2", sp="屋内", ex="锁空间"),
    # EP03
    "E301": dict(p="2", sp="路上", ex="人出画"),
    "E302": dict(p="1", sp="院子", ex="人出画"),
    "E303": dict(p="1", sp="院子", ex="锁空间"),
    # EP04
    "E401": dict(p="3", sp="院子", ex="锁空间"),
    "E402": dict(p="2", sp="院子", ex="锁空间"),
    "E403": dict(p="2", sp="院子", ex="锁空间"),
    "E404": dict(p="1", sp="院子", ex="锁空间"),
    "E405": dict(p="1", sp="院子", ex="锁空间"),
    "E406": dict(p="1", sp="屋内", ex="边界物"),
    # EP05
    "E501": dict(p="1", sp="院子", ex="自顾"),
    "E502": dict(p="1", sp="院子", ex="人出画"),
    "E503": dict(p="1", sp="堂屋", ex="锁空间"),
    "E504": dict(p="1", sp="门口", ex="边界物"),
    # EP06
    "E601": dict(p="3", sp="院子", ex="锁空间"),
    "E602": dict(p="3", sp="田里", ex="人出画"),
    # EP07 夜戏
    "E701": dict(p="2", sp="院子", ex="锁空间"),
    "E702": dict(p="1", sp="屋内", ex="人出画"),
    "E703": dict(p="1", sp="屋内", ex="锁空间"),
    "E704": dict(p="1", sp="屋内", ex="锁空间"),
    # EP08 巷子戏
    "E801": dict(p="2", sp="屋内", ex="锁空间"),
    "E802": dict(p="1", sp="巷子", ex="自顾"),
    "E803": dict(p="1", sp="巷子", ex="自顾"),
    "E804": dict(p="1", sp="巷子", ex="边界物"),
    # EP09 雨夜 + 医院
    "E901": dict(p="1", sp="屋内", ex="人出画"),
    "E902": dict(p="1", sp="路上", ex="人出画"),
    "E903": dict(p="2", sp="屋内", ex="锁空间"),
    "E904": dict(p="1", sp="屋内", ex="锁空间"),
    # EP10
    "E1001": dict(p="2", sp="屋内", ex="锁空间"),
    "E1002": dict(p="1", sp="院子", ex="锁空间"),
    "E1003": dict(p="1", sp="院子", ex="锁空间"),
    "E1004": dict(p="1", sp="屋内", ex="锁空间"),
    # EP11 菜地戏
    "E1101": dict(p="2", sp="院子", ex="边界物"),
    "E1102": dict(p="2", sp="屋内", ex="锁空间"),
    "E1103": dict(p="2", sp="屋内", ex="锁空间"),
    "E1104": dict(p="2", sp="田里", ex="自顾"),
    "E1105": dict(p="1", sp="田里", ex="自顾"),
    "E1106": dict(p="1", sp="田里", ex="锁空间"),
    "E1107": dict(p="2", sp="屋内", ex="锁空间"),
    # EP12 当众评理
    "E1201": dict(p="多", sp="院子", ex="锁空间"),
    "E1202": dict(p="1", sp="门口", ex="边界物"),
    "E1203": dict(p="3", sp="屋内", ex="锁空间"),
    # EP13 婚礼
    "E1301": dict(p="2", sp="院子", ex="锁空间"),
    "E1302": dict(p="1", sp="院子", ex="锁空间"),
    "E1303": dict(p="1", sp="院子", ex="人出画"),
    "E1304": dict(p="1", sp="院子", ex="空镜"),
    # EP14 开荒
    "E1401": dict(p="2", sp="田里", ex="锁空间"),
    "E1402": dict(p="1", sp="田里", ex="锁空间"),
    "E1403": dict(p="2", sp="路上", ex="人出画"),
    "E1404": dict(p="1", sp="院子", ex="锁空间"),
    "E1405": dict(p="多", sp="门口", ex="边界物"),
    "E1406": dict(p="1", sp="院子", ex="锁空间"),
    "E1407": dict(p="1", sp="院子", ex="边界物"),
    # EP15 与刘桂香争执（现代景）
    "E1501": dict(p="2", sp="院子", ex="锁空间"),
    "E1502": dict(p="3", sp="院子", ex="锁空间"),
    "E1503": dict(p="1", sp="院子", ex="锁空间"),
    "E1504": dict(p="多", sp="巷子", ex="锁空间"),
    "E1505": dict(p="1", sp="路上", ex="锁空间"),
    "E1506": dict(p="1", sp="路上", ex="锁空间"),
    "E1507": dict(p="1", sp="路上", ex="锁空间"),
    # EP16
    "E1601": dict(p="2", sp="田里", ex="自顾"),
    "E1602": dict(p="2", sp="田里", ex="锁空间"),
    "E1603": dict(p="1", sp="屋内", ex="锁空间"),
    "E1604": dict(p="1", sp="屋内", ex="人出画"),
    "E1605": dict(p="2", sp="院子", ex="锁空间"),
    "E1606": dict(p="1", sp="门口", ex="边界物"),
    "E1607": dict(p="多", sp="院子", ex="锁空间"),
    # EP17 喜宴
    "E1701": dict(p="多", sp="院子", ex="锁空间"),
    "E1702": dict(p="2", sp="门口", ex="边界物"),
    "E1703": dict(p="1", sp="门口", ex="人出画"),
    "E1704": dict(p="1", sp="院子", ex="锁空间"),
    "E1705": dict(p="1", sp="院子", ex="人出画"),
    # EP18
    "E1801": dict(p="2", sp="院子", ex="锁空间"),
    "E1802": dict(p="1", sp="门口", ex="锁空间"),
    "E1803": dict(p="1", sp="门口", ex="边界物"),
    "E1804": dict(p="1", sp="院子", ex="锁空间"),
    "E1805": dict(p="3", sp="院子", ex="锁空间"),
    "E1806": dict(p="1", sp="院子", ex="边界物"),
    "E1807": dict(p="1", sp="院子", ex="锁空间"),
    # EP19
    "E1901": dict(p="2", sp="屋内", ex="锁空间"),
    "E1902": dict(p="1", sp="屋内", ex="边界物"),
    "E1903": dict(p="3", sp="院子", ex="锁空间"),
    "E1904": dict(p="3", sp="院子", ex="锁空间"),
    "E1905": dict(p="1", sp="门口", ex="空镜"),
}

# 出口形态 → 王婶能怎么进（写执行单时直接用）
EXIT_ENTRY = {
    "人出画": "段落后可场景二扩：顺着人物走的方向扩一个相邻空间",
    "边界物": "段落后可场景二扩：从画面里的门洞/路口/墙头切到对面",
    "空镜": "段落后可场景二扩：空镜最自由，直接换到王婶所在的空间",
    "自顾": "段落后可场景二扩：切到王婶做同类的事，同一片田野里最自然",
    "锁空间": "只能画外音插话或段前立人，不要在段落中间切出去",
}

# ── 97 段逐段判读 ────────────────────────────────────────────
# 字段：intensity 1-5 / state 临界|已兑现|铺垫 / theme / view 谁在看 / chars
# anchor=True 表示进锚点池（state=临界 或 情绪最强的已兑现，可当结尾锚）
L: dict[str, dict] = {
    # ── EP01 门口退婚 ──
    "E101": dict(i=3, s="铺垫", t="退婚通知", th=["T1"], v="娘家", c=["苏小满"]),
    "E102": dict(i=3, s="铺垫", t="追问原因", th=["T1"], v="娘家", c=["苏小满"]),
    "E103": dict(i=3, s="铺垫", t="家人论理（救人才摔的）", th=["T2"], v="娘家", c=["苏小满"]),
    "E104": dict(i=2, s="铺垫", t="走几十里路", th=["T1"], v="小满", c=["苏小满"]),
    "E105": dict(i=3, s="铺垫", t="娘家放话", th=["T1"], v="娘家", c=["苏小满"]),
    "E106": dict(i=4, s="已兑现", t="门口求助", th=["T1"], v="小满", c=["苏小满", "ch_003"], a=True,
                  note="『你别不要我』是终点情绪，只能当结尾锚，不能当开头钩子"),
    "E107": dict(i=5, s="临界", t="当门被拒→她说不退", th=["T1"], v="桂芬", c=["苏小满", "ch_002"], a=True,
                  note="『这门亲事退了』落下后她只回两个字『不退』——结果未知，第一强锚"),
    "E108": dict(i=5, s="临界", t="当众贬低→自报姓名", th=["T2"], v="刘婶", c=["苏小满", "ch_002", "ch_004"], a=True,
                  note="『我不是傻姑娘，我叫苏小满』说完之后满院没人接话"),
    "E109": dict(i=4, s="已兑现", t="拉扯入屋", th=["T1"], v="桂芬", c=["苏小满", "ch_002", "ch_003"]),
    "E110": dict(i=5, s="临界", t="婆婆狠话封门", th=["T1", "T5"], v="桂芬", c=["苏小满", "ch_002"], a=True,
                  note="『只要我活着你就别想进这个门』——门到底进没进，全剧最大悬念"),
    # ── EP02 赖着不走 ──
    "E201": dict(i=3, s="已兑现", t="拒走", th=["T1"], v="小满", c=["苏小满", "ch_002"]),
    "E202": dict(i=4, s="已兑现", t="揭穿承诺", th=["T1"], v="小满", c=["苏小满", "ch_003"],
                  note="『你上次也说来看我，我等了三天』——傻人有账本"),
    "E203": dict(i=4, s="已兑现", t="我听得懂→讨米", th=["T2", "T3"], v="小满", c=["苏小满", "ch_004"], a=True,
                  note="全剧第一次反杀，用一斗米证明她记得住事"),
    "E204": dict(i=3, s="已兑现", t="坐门口不进屋", th=["T1", "T5"], v="桂芬", c=["苏小满", "ch_002", "ch_003"]),
    # ── EP03 干活换饭吃 ──
    "E301": dict(i=3, s="已兑现", t="抢着干活", th=["T4"], v="桂芬", c=["苏小满", "ch_002"]),
    "E302": dict(i=4, s="已兑现", t="当面讨米（刘婶）", th=["T3"], v="刘婶", c=["苏小满", "ch_004"], a=True,
                  note="当着婆婆的面把刘婶的账摊开，位置第一次反转"),
    "E303": dict(i=2, s="铺垫", t="让饭", th=["T5"], v="桂芬", c=["苏小满", "ch_002", "ch_003"]),
    # ── EP04 留下就得包重活 ──
    "E401": dict(i=3, s="已兑现", t="我不走", th=["T1"], v="小满", c=["苏小满"]),
    "E402": dict(i=3, s="铺垫", t="娘家哥来领人", th=["T1"], v="苏强", c=["苏小满", "ch_002"]),
    "E403": dict(i=3, s="临界", t="走还是留", th=["T1"], v="嫂子", c=["苏小满"], a=True,
                  note="『我走了建军哥怎么办』——她还没答走不走"),
    "E404": dict(i=4, s="临界", t="留下条件：重活全包", th=["T1", "T4"], v="桂芬", c=["苏小满", "ch_002"], a=True),
    "E405": dict(i=5, s="临界", t="建军第一次顶娘", th=["T5"], v="建军", c=["ch_003", "ch_002"], a=True,
                  note="『小满的活我帮她干』——婆婆当场炸，但拿他没辙"),
    "E406": dict(i=3, s="已兑现", t="哥嫂离开", th=["T1"], v="苏强", c=["苏小满", "ch_003"]),
    # ── EP05 长工争议 ──
    "E501": dict(i=2, s="铺垫", t="互相体贴", th=["T4"], v="小满", c=["苏小满", "ch_003"]),
    "E502": dict(i=2, s="铺垫", t="我就想留在这儿", th=["T1"], v="小满", c=["苏小满", "ch_003"]),
    "E503": dict(i=4, s="已兑现", t="我不是长工", th=["T2"], v="刘婶", c=["苏小满", "ch_004"], a=True,
                  note="『我是建军哥的媳妇』——把身份说成事实，不是请求"),
    "E504": dict(i=4, s="已兑现", t="反将一军（半袋米）", th=["T3"], v="桂芬", c=["苏小满", "ch_004"], a=True),
    # ── EP06 拉犁 ──
    "E601": dict(i=4, s="临界", t="干完才留下", th=["T4"], v="桂芬", c=["苏小满", "ch_002", "ch_003"], a=True,
                  note="把「留不留下」变成一个可验收的条件，最硬的钩子结构"),
    "E602": dict(i=5, s="临界", t="建军爆发护妻→婆婆让步", th=["T5"], v="建军", c=["ch_003", "ch_002", "苏小满"], a=True,
                  note="『她是我媳妇！我不许你这么对她』——婆婆随后说『回家，地不翻了』"),
    # ── EP07 一碗粥 ──
    "E701": dict(i=2, s="铺垫", t="给她泡手", th=["T6"], v="建军", c=["苏小满", "ch_003"]),
    "E702": dict(i=3, s="临界", t="你就不怨她？", th=["T6"], v="建军", c=["苏小满", "ch_003"], a=True,
                  note="问完她没答怨不怨，只说给婶子端一碗"),
    "E703": dict(i=4, s="临界", t="婆婆嘴硬心软（问手）", th=["T5", "T6"], v="桂芬", c=["ch_002", "ch_003"], a=True,
                  note="『她手怎么样了』『灶上有灌油，抹上』——全剧婆婆第一道裂缝"),
    "E704": dict(i=2, s="已兑现", t="这傻丫头", th=["T5"], v="桂芬", c=["ch_002"]),
    # ── EP08 鸡蛋 ──
    "E801": dict(i=2, s="铺垫", t="换鸡蛋熬粥", th=["T6"], v="小满", c=["苏小满", "ch_002"]),
    "E802": dict(i=3, s="已兑现", t="看什么看，你也吃", th=["T5"], v="桂芬", c=["ch_002", "苏小满"]),
    "E803": dict(i=4, s="已兑现", t="我不是劳力", th=["T2"], v="刘婶", c=["苏小满", "ch_004"]),
    "E804": dict(i=4, s="已兑现", t="建军当众站队", th=["T3", "T5"], v="建军", c=["ch_003", "ch_004"], a=True,
                  note="『她是我周家的人，怎么不能管』——婆婆第一次没反驳"),
    # ── EP09 背婆婆看病 ──
    "E901": dict(i=4, s="临界", t="背得动吗", th=["T4", "T5"], v="小满", c=["苏小满", "ch_002"], a=True),
    "E902": dict(i=5, s="临界", t="不放，要去看大夫", th=["T4"], v="小满", c=["苏小满", "ch_002"], a=True,
                  note="背着一个一直赶她走的人走夜路，结果在路上"),
    "E903": dict(i=4, s="已兑现", t="婆婆醒来", th=["T5"], v="桂芬", c=["ch_002", "苏小满"]),
    "E904": dict(i=4, s="已兑现", t="我护定她了", th=["T5"], v="建军", c=["ch_003"], a=True,
                  note="可做全片收束句，短、狠、不留解释"),
    # ── EP10 拒媒 ──
    "E1001": dict(i=2, s="铺垫", t="被逼坐下吃饭", th=["T5"], v="桂芬", c=["ch_002", "苏小满"]),
    "E1002": dict(i=4, s="临界", t="刘婶来劝换儿媳", th=["T2"], v="刘婶", c=["ch_004", "ch_002"], a=True,
                   note="外人当着婆婆说「留个傻子建军以后咋办」，婆婆怎么答是钩子"),
    "E1003": dict(i=3, s="铺垫", t="小满说刘婶坏话", th=["T3"], v="小满", c=["苏小满", "ch_004"]),
    "E1004": dict(i=5, s="临界", t="不管娘同不同意我都要娶", th=["T1", "T5"], v="建军", c=["ch_003", "ch_002"], a=True,
                   note="儿子摊牌，婆婆只回「我再想想」，第二天却去扯布"),
    # ── EP11 扯布 + 界石 ──
    "E1101": dict(i=2, s="铺垫", t="去镇上扯布", th=["T5"], v="桂芬", c=["ch_002"]),
    "E1102": dict(i=4, s="已兑现", t="傻子穿新衣裳也是傻子", th=["T2"], v="刘婶", c=["苏小满", "ch_004"], a=True,
                   note="『我不傻。我会干活，会背婶子看病』——把傻的定义改掉"),
    "E1103": dict(i=5, s="已兑现", t="婆婆当众护（包起来）", th=["T5"], v="桂芬", c=["ch_002", "ch_004"], a=True),
    "E1104": dict(i=4, s="临界", t="界石之争", th=["T3"], v="小满", c=["苏小满", "ch_004"], a=True),
    "E1105": dict(i=5, s="临界", t="刘桂香你动我儿媳试试", th=["T3", "T5"], v="桂芬", c=["ch_002", "ch_004"], a=True),
    "E1106": dict(i=2, s="已兑现", t="萝卜没坏", th=["T3"], v="小满", c=["苏小满"]),
    "E1107": dict(i=3, s="铺垫", t="改口叫娘", th=["T5"], v="小满", c=["苏小满", "ch_002"]),
    # ── EP12 当众认儿媳 ──
    "E1201": dict(i=5, s="已兑现", t="当着大伙认儿媳", th=["T1", "T2"], v="桂芬", c=["ch_002", "苏小满"], a=True,
                   note="『谁再敢叫她一声傻子，就是跟我张桂芬过不去』——帽子被摘掉"),
    "E1202": dict(i=5, s="已兑现", t="我娶你 / 娶是啥", th=["T1", "T8"], v="建军", c=["ch_003", "苏小满"], a=True,
                   note="『娶是啥？』这三个字是全剧最好的钩子素材之一"),
    "E1203": dict(i=4, s="已兑现", t="认亲，我有家了", th=["T1"], v="村长", c=["苏小满", "ch_002"], a=True),
    # ── EP13 婚礼 + 石碾 ──
    "E1301": dict(i=4, s="已兑现", t="掀盖头", th=["T1"], v="建军", c=["ch_003", "苏小满"]),
    "E1302": dict(i=4, s="临界", t="三百斤石碾谁来搬", th=["T4"], v="看客", c=["苏小满"], a=True,
                   note="『得四个人抬』说完，还没人动"),
    "E1303": dict(i=5, s="已兑现", t="单手搬石碾", th=["T4"], v="看客", c=["苏小满"], a=True),
    "E1304": dict(i=2, s="铺垫", t="全家福", th=["T5"], v="桂芬", c=["苏小满", "ch_002"]),
    # ── EP14 菜苗被拔 ──
    "E1401": dict(i=2, s="铺垫", t="开荒种菜", th=["T6", "T4"], v="小满", c=["苏小满", "ch_003"]),
    "E1402": dict(i=3, s="铺垫", t="给刘婶也送点", th=["T3"], v="刘婶", c=["苏小满", "ch_004"]),
    "E1403": dict(i=3, s="铺垫", t="种了就不许再拔了", th=["T3"], v="小满", c=["苏小满"], a=True,
                   note="伏笔句，短到只能做插入，但语气是全剧最像「立规矩」的一次"),
    "E1404": dict(i=4, s="临界", t="菜苗是不是你拔的", th=["T3"], v="建军", c=["ch_003", "ch_004"], a=True,
                   note="『一个傻子说的话也能信？』——对方先攻她的证词资格"),
    "E1405": dict(i=5, s="已兑现", t="她不是傻子，她是我媳妇", th=["T2"], v="建军", c=["ch_003", "苏小满"], a=True),
    "E1406": dict(i=4, s="已兑现", t="村长评理，赔钱道歉", th=["T3"], v="村长", c=["ch_004", "苏小满"]),
    "E1407": dict(i=4, s="已兑现", t="人家比谁都明白", th=["T2"], v="村民", c=["苏小满"], a=True),
    # ── EP15 造谣反证 ──
    "E1501": dict(i=4, s="铺垫", t="刘婶满村造谣", th=["T3"], v="刘婶", c=["ch_004", "苏小满"]),
    "E1502": dict(i=4, s="临界", t="哥问：你打人了？", th=["T3"], v="苏强", c=["苏小满", "ch_002"], a=True,
                   note="最亲的人先怀疑，她只说『我没打人。我种菜』"),
    "E1503": dict(i=5, s="临界", t="当众对质，她答不上来", th=["T3"], v="建军", c=["ch_003", "ch_004"], a=True,
                   note="『就……就……』卡在这里，是全剧最好的截断点之一"),
    "E1504": dict(i=5, s="已兑现", t="婆婆爆发护儿媳", th=["T5"], v="桂芬", c=["ch_002", "ch_004"]),
    "E1505": dict(i=4, s="已兑现", t="我错了 / 谁再欺负她", th=["T3"], v="桂芬", c=["ch_002", "ch_004"]),
    "E1506": dict(i=4, s="已兑现", t="给菜种，以德报怨", th=["T3"], v="小满", c=["苏小满", "ch_004"], a=True),
    "E1507": dict(i=3, s="已兑现", t="旁评以德报怨", th=["T2"], v="村民", c=["苏小满"]),
    # ── EP16 补办婚宴筹备 ──
    "E1601": dict(i=1, s="铺垫", t="菜种得好", th=["T3"], v="刘婶", c=["ch_004", "ch_002"]),
    "E1602": dict(i=2, s="铺垫", t="她再拔我就再给菜种", th=["T3"], v="小满", c=["苏小满", "ch_003"]),
    "E1603": dict(i=3, s="铺垫", t="决定补办婚宴", th=["T1", "T5"], v="桂芬", c=["ch_002", "ch_003"]),
    "E1604": dict(i=2, s="铺垫", t="娘，谢谢您", th=["T5"], v="小满", c=["苏小满", "ch_002"]),
    "E1605": dict(i=2, s="铺垫", t="哥嫂备礼", th=["T1"], v="苏强", c=["苏小满"]),
    "E1606": dict(i=2, s="铺垫", t="我妹交给你我放心了", th=["T1"], v="苏强", c=["苏小满", "ch_003"]),
    "E1607": dict(i=3, s="已兑现", t="村长通知全村", th=["T1"], v="村长", c=["苏小满"]),
    # ── EP17 补办婚宴 + 撑棚 ──
    "E1701": dict(i=5, s="已兑现", t="传陪嫁：你就是我亲闺女", th=["T5", "T8"], v="桂芬", c=["ch_002", "苏小满"], a=True),
    "E1702": dict(i=3, s="已兑现", t="村长致辞：真心换来尊重", th=["T2"], v="村长", c=["苏小满"]),
    "E1703": dict(i=5, s="临界", t="棚子要塌了→单手撑住", th=["T4"], v="看客", c=["苏小满"], a=True,
                   note="『棚子要塌了』喊完之后的三秒，是全剧视觉最强的一下"),
    "E1704": dict(i=3, s="已兑现", t="最大的福气", th=["T5"], v="建军", c=["ch_003", "苏小满"]),
    "E1705": dict(i=2, s="已兑现", t="刘婶也来合影", th=["T3"], v="桂芬", c=["ch_004"]),
    # ── EP18 旧账上门 ──
    "E1801": dict(i=1, s="铺垫", t="留红纸给娘看", th=["T5"], v="小满", c=["苏小满", "ch_002"]),
    "E1802": dict(i=4, s="临界", t="赵四上门提旧事", th=["T7"], v="赵四", c=["苏小满", "ch_002"], a=True,
                   note="当年差点被说给别人，如今来看热闹"),
    "E1803": dict(i=4, s="临界", t="王婆子也来了", th=["T7"], v="桂芬", c=["ch_002"], a=True),
    "E1804": dict(i=5, s="已兑现", t="谁再敢打她主意", th=["T7"], v="建军", c=["ch_003"], a=True),
    "E1805": dict(i=5, s="已兑现", t="给她糖：我嫁人了，你别来了", th=["T7", "T2"], v="小满", c=["苏小满"], a=True,
                   note="全剧最狠的一句，狠在她还在笑"),
    "E1806": dict(i=5, s="已兑现", t="她是我张桂芬的闺女", th=["T5"], v="桂芬", c=["ch_002"], a=True),
    "E1807": dict(i=3, s="铺垫", t="我有家，两个家", th=["T7"], v="小满", c=["苏小满"]),
    # ── EP19 传镯子 ──
    "E1901": dict(i=2, s="铺垫", t="开春拍结婚照", th=["T5"], v="小满", c=["苏小满", "ch_003"]),
    "E1902": dict(i=5, s="已兑现", t="镯子跟了我三十年，今天传给你", th=["T5", "T8"], v="桂芬", c=["ch_002", "苏小满"], a=True),
    "E1903": dict(i=3, s="已兑现", t="这是娘的陪嫁", th=["T8"], v="嫂子", c=["苏小满"]),
    "E1904": dict(i=2, s="铺垫", t="该有的都得有 / 咱村的福气", th=["T1"], v="苏强", c=["苏小满"]),
    "E1905": dict(i=2, s="铺垫", t="娘做，我等", th=["T5"], v="小满", c=["苏小满", "ch_002"]),
}


def main() -> int:
    root = Path(__file__).resolve().parents[3]
    base = root / "assets/short-drama/八零憨妻的甜蜜日常"
    skel = json.loads((base / "segments_skeleton.json").read_text(encoding="utf-8"))

    eps_out = []
    missing, anchors = [], []
    for ep_key, v in skel["episodes"].items():
        ep = int(ep_key)
        segs_out = []
        for s in v["segments"]:
            sid = f"E{ep}{s['id'][1:]}"
            if sid not in L:
                missing.append(sid)
                continue
            lab = L[sid]
            seg = {
                "id": sid,
                "ep": ep,
                "start": s["start"],
                "end": s["end"],
                "duration": s["duration"],
                "lines": s["lines"],
                "speakers": None,
                "speakers_pending": True,
                "conflict": {"type": lab["t"], "intensity": lab["i"]},
                "critical_state": lab["s"],
                "themes": lab["th"],
                "viewpoint": lab["v"],
                "characters": lab["c"],
                "space": EDGE.get(sid, {}).get("sp"),
                "exit_type": EDGE.get(sid, {}).get("ex"),
                "exit_hint": EXIT_ENTRY.get(EDGE.get(sid, {}).get("ex", "")),
                "edge_verified": sid in EDGE,
                "anchor_candidate": bool(lab.get("a", False)),
                "visual": None,
                "visual_pending": True,
                "ending": False,
            }
            if lab.get("note"):
                seg["anchor_note"] = lab["note"]
            for k in ("head_from_zero", "tail_to_end"):
                if k in s:
                    seg[k] = s[k]
            segs_out.append(seg)
            if seg["anchor_candidate"]:
                anchors.append({
                    "id": sid, "ep": ep, "start": s["start"], "end": s["end"],
                    "critical_state": lab["s"],
                    "anchor_role": {"临界": "hook", "已兑现": "payoff"}.get(lab["s"], "insert"),
                    "intensity": lab["i"],
                    "themes": lab["th"],
                    "viewpoint": lab["v"],
                    "why": lab.get("note", lab["t"]),
                    "space": EDGE.get(sid, {}).get("sp"),
                    "exit_type": EDGE.get(sid, {}).get("ex"),
                    "insert_options": (["场景二扩", "中间插话", "开头立人"]
                                       if EDGE.get(sid, {}).get("ex") in
                                       ("人出画", "边界物", "空镜", "自顾")
                                       else ["中间插话", "开头立人"]),
                })
        eps_out.append({"ep": ep, "duration": v["duration"], "segments": segs_out})

    # 主题 → 覆盖哪些锚点（栏目单元的原料清单）
    theme_index = {}
    for tid, meta in THEMES.items():
        hits = [a for a in anchors if tid in a["themes"]]
        theme_index[tid] = {
            **meta,
            "anchor_ids": [h["id"] for h in hits],
            "anchor_count": len(hits),
            "ep_spread": sorted({h["ep"] for h in hits}),
            "enough_for_one_episode": len(hits) >= 3 and len({h["ep"] for h in hits}) >= 2,
        }

    out = {
        "schema_version": "2.0",
        "line": "short-drama",
        "category": "八零憨妻的甜蜜日常",
        "source_root_env": "SHORT_DRAMA_ASSET_ROOT",
        "unit": "conflict_segment",
        "scope": {"covered": list(range(1, 20)), "excluded": [20],
                  "excluded_reason": "大结局，任务要求第 3 条禁止让用户看完全集"},
        "method": {
            "text": "内嵌硬字幕 OCR → lines/lines_epNN.json（508 句，带秒）",
            "segment": "按台词间隔 >4.5s 或段长 >26s 切，段长落 12–26s（segment.py）",
            "label": "人工逐段判读（label_anchor_pool.py），非模型自动生成",
            "not_done": ["画面描述 visual（待锚点筛完再做局部核验，不做全量 VLM）",
                         "说话人 speakers（需音频分离，字幕给不了语气）"],
        },
        "stats": {
            "episodes": 19, "segments": sum(len(e["segments"]) for e in eps_out),
            "anchors": len(anchors),
            "by_state": {st: sum(1 for e in eps_out for s in e["segments"] if s["critical_state"] == st)
                         for st in ("临界", "已兑现", "铺垫")},
            "total_seconds": round(sum(e["duration"] for e in eps_out), 1),
            "by_exit": {k: sum(1 for e in eps_out for s in e["segments"] if s.get("exit_type") == k)
                        for k in ("人出画", "边界物", "空镜", "自顾", "锁空间")},
            "expandable": sum(1 for e in eps_out for s in e["segments"]
                              if s.get("exit_type") in ("人出画", "边界物", "空镜", "自顾")),
        },
        "themes": theme_index,
        "episodes": eps_out,
        "anchor_pool": anchors,
        "pending": [
            "苏强 / 赵小慧 / 村长 / 赵四 / 王婆子 未进 drama-pack.characters，锚点里只能写名字",
            "段边界帧已核验（space / exit_type），但段中画面 visual 仍为空 —— 只对入选锚点逐段看几帧（L4），不做全量",
            "speakers 全空，孪生解说只说自己词，但要知道这段画面里站的是谁才能判断能不能插",
            "部分段内跨两个小事件（如 E1703 段内先『亲家托付』再『棚子要塌』），执行单取锚点须按 start 精确到秒，不能整段照搬",
        ],
        "status": "anchor_pool 待人工验收",
    }

    if missing:
        print("缺标签的段：", missing, file=sys.stderr)
        return 1

    p = base / "asset_index.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"段 {out['stats']['segments']} / 锚点 {len(anchors)} / 状态分布 {out['stats']['by_state']}")
    print("\n主题 → 锚点覆盖：")
    for tid, m in theme_index.items():
        flag = "可单出一条" if m["enough_for_one_episode"] else "锚点不足"
        print(f"  {tid} {m['name']:<10} {m['anchor_count']:>2} 个锚点  跨 {m['ep_spread']}  {flag}")
    print(f"\n已写 {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
