# -*- coding: utf-8 -*-
"""全片验收报告：把这次做了什么、为什么这么做、边界在哪，写成一页 HTML。"""
import json, os

ROOT = "/Users/zgl/vibecut-ecom"
RUN = f"{ROOT}/runs/以真心守护至亲/D3_线一认不认这个妈"
OUT = f"{RUN}/build/去重预览/全片版"

V = json.load(open(f"{OUT}/验收数据.json", encoding="utf-8"))
T = json.load(open(f"{OUT}/点睛句表.json", encoding="utf-8"))
S = json.load(open(f"{OUT}/dedup_spec.json", encoding="utf-8"))
P = json.load(open(f"{RUN}/plan.silence.json", encoding="utf-8"))
q_on = [x for x in T["点睛句"] if x["on_screen"]]

def rows(checks):
    return "".join(
        f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>" for a, b, c in checks)

def qrows():
    out = []
    for q in T["点睛句"]:
        mark = "上屏" if q["on_screen"] else "未上屏"
        cls = "yes" if q["on_screen"] else "no"
        out.append(
            f'<tr class="{cls}"><td>{q["id"]}</td><td>第 {q["ep"]} 集 {q["t_in_ep"]} 秒</td>'
            f'<td>{q["quote"]}</td><td>{q["seg"]}</td>'
            f'<td>{q["at_in_trailer"]:.2f}s</td><td>{mark}</td>'
            f'<td>{q["why"]}</td></tr>')
    for q in T["未命中"]:
        out.append(
            f'<tr class="no"><td>{q["id"]}</td><td>第 {q["ep"]} 集 {q["t"]} 秒</td>'
            f'<td>{q["quote"]}</td><td>—</td><td>—</td><td>未命中</td>'
            f'<td>{q["原因"]}</td></tr>')
    return "".join(out)

def a1rows():
    return "".join(f"<li>{x}</li>" for x in S["a1_segments"])

html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>全片一次编码验收报告 · 以真心守护至亲 D3</title>
<style>
:root{{--bg:#f6f4ef;--card:#fff;--ink:#2b2620;--dim:#6d6459;--line:#e2ddd3;
--gold:#b8863c;--ok:#3d7a55;--warn:#b06a2c}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1120px;margin:0 auto;padding:36px 24px 80px}}
h1{{font-size:27px;margin:0 0 6px;letter-spacing:.3px}}
.sub{{color:var(--dim);font-size:13px;margin-bottom:26px}}
h2{{font-size:19px;margin:34px 0 12px;padding-left:11px;border-left:4px solid var(--gold)}}
h3{{font-size:16px;margin:22px 0 8px}}
p{{margin:9px 0}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:18px 20px;margin:14px 0}}
.lead{{background:#fdf9ef;border-left:4px solid var(--gold)}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;background:var(--card)}}
th,td{{border:1px solid var(--line);padding:9px 11px;text-align:left;vertical-align:top}}
th{{background:#efebe2;font-weight:600;white-space:nowrap}}
tr.no{{color:#8a8175}} tr.yes td:nth-child(6){{color:var(--ok);font-weight:600}}
ul{{margin:8px 0 4px;padding-left:22px}} li{{margin:4px 0}}
img{{max-width:100%;border:1px solid var(--line);border-radius:8px;display:block;margin:10px 0}}
.tag{{display:inline-block;background:#efe7d6;color:#7a5b23;border-radius:4px;
padding:1px 8px;font-size:12px;margin-right:6px}}
.big{{font-size:21px;font-weight:700;color:var(--gold)}}
.note{{color:var(--dim);font-size:13px}}
.warn{{background:#fdf3e7;border-left:4px solid var(--warn)}}
.ok{{color:var(--ok);font-weight:600}}
code{{background:#f0ede6;padding:1px 5px;border-radius:3px;font-size:12.5px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">

<h1>全片一次编码验收报告</h1>
<div class="sub">剧目：以真心守护至亲 · 用例：D3_线一认不认这个妈（v3 清单） ·
2026-11-14 · 这次不再是 48 秒演示片，是整条 {V['full']['时长']:.2f} 秒成片</div>

<div class="card lead">
<p><span class="big">一句话</span>：之前几轮都是拿已经拼好的 <code>v3.mp4</code> 再压一遍做 48 秒演示。
这次按剪辑清单从<b>原剧素材重新装配整条片子</b>，把画中画包装、全片参数包、扫光、字幕式文字
全部挂进<b>同一张画面处理图里，只编码一次</b>。文字内容也不再按固定序号取金句，
改成从清单和剧集包里自动推出的一张「点睛句表」。</p>
<p class="note">红线照旧：不动段序、段长、台词、五幕、原剧字幕；音频不做任何去重处理；
不为一句漂亮字幕去多选一段戏。去重的权重小于剪辑质量。</p>
</div>

<h2>一、这次到底做了哪几件事</h2>
<div class="card">
<table>
<tr><th>做了什么</th><th>之前（48 秒演示）</th><th>这次（全片）</th></tr>
<tr><td>画面从哪来</td><td>拿 <code>v3.mp4</code> 二次压制</td><td class="ok">从 16 集原剧素材按清单装配</td></tr>
<tr><td>编码次数</td><td>2 次（成片一次 + 演示再一次）</td><td class="ok">1 次，画质不掉一档</td></tr>
<tr><td>覆盖长度</td><td>48 秒，抽 4 小段</td><td class="ok">{V['full']['时长']:.2f} 秒，{len(P['segs'])} 段全在</td></tr>
<tr><td>文字文案</td><td>固定取金句库第 2 条、钩子第 1 条</td><td class="ok">按「集号＋时间点」自动对齐到已选段，{len(q_on)} 条上屏</td></tr>
<tr><td>装配器</td><td>没动</td><td>加了一个可选开关；不给开关时行为跟以前完全一样</td></tr>
</table>
</div>

<h2>二、文字：点睛句是怎么「算」出来的</h2>
<div class="card">
<p>张老师上一轮问「画龙点睛的金句，是不是在做文案时就能分析出来」——这轮把它落成了
一份独立文件 <code>点睛句表.json</code>。做法很朴素：剧集包里每条金句都带着
「第几集、第几秒」，剪辑清单里每段都带着「第几集、从几秒到几秒」。
两边一比，金句时间点落在哪一段里，就说明这一拍用的是这句台词，
上屏时刻也就自然算出来了。</p>
<p>实测：金句库 {T['统计']['金句库总数']} 条，自动对上 <b>{T['统计']['自动对齐命中']} 条</b>，
其中 {T['统计']['命中但未上屏']} 条因为和前一句只隔 2.16 秒、叠字会糊成一片而没上屏；
另有 {len(T['未命中'])} 条是这条故事线根本没选到那场戏，不是对齐漏了。</p>
<p class="note">这张表是独立文件，<b>没有塞进 plan.json</b>——塞进去会牵动装配器和过目页、
触发整套重验。它只描述「哪句该上屏、在第几秒」，不参与剪辑决策。</p>
<table>
<tr><th>编号</th><th>原剧位置</th><th>金句</th><th>落在哪一段</th><th>成片第几秒</th><th>上屏</th><th>理由</th></tr>
{qrows()}
</table>
<p class="note">开场悬念句「{T['开场悬念句']['text']}」、三个人名、收尾句「{T['收尾句']['text']}」
仍从钩子库 / 人物表 / 悬念收尾库自动取，一句没手写。
四种动效（逐字弹跳、名字横滑、宋体整句滑入、打字机）和四种字体是 v4 定版，本轮未改样式。</p>
</div>

<img src="点睛句上屏抽帧.png" alt="六条点睛句上屏时刻抽帧">

<h3 style="color:#b06a2c">一个如实的观察：金句会和原剧字幕重复</h3>
<div class="card warn">
<p>抽帧复核时发现，上屏的花字和原剧自带的白色硬字幕<b>是同一句话</b>——
比如 9.9 秒画面里，上面一行宋体花字「可我妈妈17年前就没了」，下面一行原剧字幕还是这句。
这不是渲染出错：金句本来就是从台词里抽的，而这部剧自带硬字幕，两句必然同源。</p>
<p>位置上是错开的（花字在画面中部偏上，原剧字幕在下部），字重、字体、颜色也不同，
所以看成「强调」还是看成「重复」，得由人判断。这轮<b>没有擅自改</b>：
既没有为了避开重复去换文案，也没有删原剧字幕（那是红线）。</p>
<p class="note">可选的三条路，等张老师定：① 就这样，当强调；② 花字往后挪 1–2 秒，
让观众先读完原剧字幕、再看到花字回响；③ 上屏文案改成不重复的短评（但那就要手写了，
会破「文案自动从剧集包取」这条）。</p>
</div>
<img src="文字位置检查.png" alt="金句与原剧字幕的位置对照">

<h2>三、画面：哪几段包装、哪些不包装</h2>
<div class="grid">
<div class="card">
<h3>画中画包装只给 {len(S['a1_segments'])} 段</h3>
<p>不是全片套一个框。挑的是<b>静场、独白、特写</b>段——画面缩到 90%、
后面垫一层模糊底、加一圈很细的白边，视线收拢到人物脸上。
冲突密集、台词你来我往的段保持满屏，不遮信息、不切原剧字幕。</p>
<ul>{a1rows()}</ul>
<p class="note">底层先裁掉底部字幕带再模糊，模糊背景不会留下字影；
上层主画面完整保留原剧字幕。这一条是「不去字幕」的落地方式。</p>
</div>
<div class="card">
<h3>全片统一挂的三样</h3>
<ul>
<li><b>参数包</b>：亮度 +3%、对比 1.03、暗角、边缘裁 8 像素再放大。
本地实测这一套对画面指纹平均动 3.7 位，真正干活的是裁切(1.3)和暗角(1.7)，
整张图一起调亮是白调。</li>
<li><b>斜向扫光</b>：宽 5%、倾角 32°、峰值 <b>12%</b>、<b>5 秒</b>扫一个来回。
这是张老师 11-14 拍板的口径；150 帧循环图无缝滚动，肉眼很淡。</li>
<li><b>角标 + 五段进度刻度</b>：全片常驻，纯图形，不带任何结构词。</li>
</ul>
<p class="note">小辅助画中画（分屏里那个「另一个人 / 细节放大」的小窗）按上一轮反馈
已删除，这轮全片里也没有。</p>
</div>
</div>

<h2>四、验收：段数、时长、声音对不对</h2>
<div class="card">
<table><tr><th>检查项</th><th>结果</th><th>说明</th></tr>{rows(V['checks'])}</table>
<p class="note">v3 原成片：{V['v3']['时长']} 秒 / {V['v3']['画面']}；
全片新版：{V['full']['时长']} 秒 / {V['full']['画面']}。
清单是 {len(P['segs'])} 段（22 段实拍 + 1 张收尾卡），装配前 44 个刀口体检全部通过
（2 处贴边警告，不改清单）。</p>
</div>
<img src="全片抽帧对照.png" alt="v3 与全片新版同时间点抽帧对照">

<h2>五、边界（这几条别当成已经解决）</h2>
<div class="card warn">
<ul>
<li><b>没测平台真实查重读数。</b>本地那套「画面指纹」只是复现抽帧比对的常用一层，
不是平台模型，位数回答不了「会不会判重」。要真知道，得拿两版片子走素材投前分析。</li>
<li><b>语义层一点没变。</b>台词、剧情、人物还是那部剧，这层躲不掉，只求把读数往下压。</li>
<li><b>技能本体还没落文件。</b>这次落地的是「参数表 + 脚本 + 独立点睛句表」，
去重技能、修饰技能、文案技能三个包都还没正式建目录、没改 <code>SKILL.md</code>、
没动 <code>check_env.py</code>。要不要固化进技能、点睛句表要不要由文案技能产出，等张老师定。</li>
<li><b>文字样式是 v4 定版「合格」那档，不算优秀。</b>本轮只换了文案来源和时间轴，样式没动。</li>
<li>收尾卡也一并过了同一套全局参数包和扫光，所以片尾不会突然「跳」一下变风格。</li>
</ul>
</div>

<h2>六、这次动了哪些文件</h2>
<div class="card">
<table>
<tr><th>文件</th><th>动作</th></tr>
<tr><td><code>skills/short-drama-trailer/scripts/mix_trailer.py</code></td>
<td>加可选 <code>--dedup</code> / <code>--preset</code> 开关与画面链挂载；
<b>不给开关时行为完全不变</b>；音频链、时长账、刀口体检一行未改</td></tr>
<tr><td><code>去重预览/全片版/dedup_spec.json</code></td><td>新建：画面参数表（两个技能共同产物）</td></tr>
<tr><td><code>去重预览/全片版/点睛句表.json</code></td><td>新建：独立点睛句表，不进 plan.json</td></tr>
<tr><td><code>去重预览/全片版/全片文字.ass</code></td><td>新建：覆盖全片时间轴的字幕动效</td></tr>
<tr><td><code>去重预览/全片版/扫光帧/</code></td><td>新建：150 帧循环光带图</td></tr>
<tr><td><code>去重预览/全片版/全片_去重修饰_v1.mp4</code></td><td>新建：本轮成片</td></tr>
<tr><td>剪辑清单、方向卡、剧集包、v1/v2/v3 成片、三份去重方案文档</td><td class="ok">全部未动</td></tr>
</table>
</div>

<p class="note" style="margin-top:30px">报告生成：2026-11-14 · 所有数据来自本机实测与清单自动对齐，
无平台侧数据、无效果百分比承诺。</p>
</div></body></html>
"""
open(f"{OUT}/全片验收报告.html", "w", encoding="utf-8").write(html)
print("报告已生成：", os.path.getsize(f"{OUT}/全片验收报告.html"), "字节")
