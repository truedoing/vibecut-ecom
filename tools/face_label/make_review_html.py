# -*- coding: utf-8 -*-
"""生成逐句核对页：台词+三层判断+建议+音频+画面+人工确认下拉。
老师在页面里听/看/选，点"导出确认结果"下载 CSV，再回填给机器算准确率。

用法：python3 make_review_html.py <真值表_证据包目录>
"""
import csv
import html
import json
import sys
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "真值表_证据包"
rows = list(csv.DictReader(open(SRC / "真值表_待人工确认.csv", encoding="utf-8-sig")))
NAMES = ["林秀兰", "许宁", "周承", "许晴",
         "周叔", "苏秘书", "陈助理", "方主管", "房东", "保安", "其他"]
# 每个角色用一个字符编码，方便把进度存进链接：0未选，其余见 CHARS
CHARS = "123456789ab"
CODE = {n: CHARS[i] for i, n in enumerate(NAMES)}

def badge(v):
    if not v or v in ("(无脸)", "解析失败"):
        return f'<span class="b m">{html.escape(v or "—")}</span>'
    return f'<span class="b">{html.escape(v)}</span>'

trs = []
for r in rows:
    i = r["id"]
    aud = f"truth_audio/{int(i):03d}_ep{int(r['集']):02d}_{float(r['时间s']):.1f}s.wav"
    frm = f"truth_frames/{int(i):03d}_ep{int(r['集']):02d}_{float(r['时间s']):.1f}s.jpg"
    opts = ''.join(f'<option value="{n}">{n}</option>' for n in NAMES)
    agree = r["三层一致?"]
    cls = "ok" if agree == "一致" else "warn"
    trs.append(f'''<tr data-id="{i}" class="row">
 <td class="id">{i}</td>
 <td class="meta">ep{r['集']} · {r['时间s']}s<br><span class="doubt">{html.escape(r['存疑'])}</span><br><span class="{cls}">{agree}</span></td>
 <td class="text">{html.escape(r['台词'])}<div class="ctx">{html.escape(r['上下文'][:160])}</div></td>
 <td class="layers">声纹 {badge(r['声纹判'])}<br>人脸 {badge(r['人脸判'])}<br>推理 {badge(r['大模型判'])}<div class="sug">建议:{html.escape(r['建议真值'])} ({html.escape(r['建议依据'])})</div></td>
 <td class="media"><audio controls preload="none" src="{aud}"></audio><br><img src="{frm}" loading="lazy" onclick="window.open(this.src)"></td>
 <td class="pick"><select class="truth"><option value="">—选谁说的—</option>{opts}</select></td>
</tr>''')

page = f'''<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>说话人真值表 · 逐句确认</title>
<style>
*{{box-sizing:border-box}}body{{font-family:-apple-system,"PingFang SC",sans-serif;margin:0;background:#f5f6f8;color:#222}}
header{{position:sticky;top:0;background:#fff;padding:14px 20px;border-bottom:2px solid #e3e5e8;z-index:9;box-shadow:0 2px 8px #0001}}
h1{{font-size:19px;margin:0 0 6px}}header p{{margin:2px 0;font-size:13px;color:#666}}
button{{background:#2b6cb0;color:#fff;border:0;padding:9px 18px;border-radius:6px;font-size:14px;cursor:pointer}}
table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px}}
th{{background:#edf0f3;padding:8px;position:sticky;top:96px;text-align:left;font-size:12px;color:#444}}
td{{border-bottom:1px solid #eee;padding:8px;vertical-align:top}}
.id{{width:38px;color:#999;font-weight:bold}}.meta{{width:110px;color:#555}}.text{{width:34%}}.layers{{width:20%}}.media{{width:210px}}.pick{{width:120px}}
.ctx{{font-size:11px;color:#999;margin-top:4px;line-height:1.4}}
.b{{display:inline-block;background:#e8f2fb;color:#1a5b96;padding:1px 7px;border-radius:4px;margin:1px 0}}
.b.m{{background:#f0f0f0;color:#999}}
.doubt{{font-size:11px;color:#c05621}}.ok{{color:#2f855a;font-size:11px}}.warn{{color:#c53030;font-size:11px}}
.sug{{margin-top:4px;font-size:11px;color:#555;background:#fafafa;padding:3px 5px;border-radius:3px}}
audio{{width:200px;height:30px}}img{{width:200px;cursor:zoom-in;border:1px solid #ddd;border-radius:3px}}
select{{width:100%;padding:5px;font-size:13px}}
</style></head><body>
<header>
<h1>说话人真值表 · 逐句确认</h1>
<p>共 {len(rows)} 句。每句点播放器听一句、看画面、读三层判断，在右侧下拉选真实说话人。</p>
<p>大模型这层只覆盖了 19/96 句（API 不稳定），标"—"的即没跑出来；不影响你听辨定真值。</p>
<p id="progress" style="font-weight:bold;color:#2b6cb0"></p>
<p style="font-size:12px;color:#666">每选一句都会自动存进下面这个链接。想换窗口继续，复制这个链接保存；下次打开链接，进度自动回来。</p>
<input id="linkbox" readonly style="width:100%;padding:5px;font-size:11px;color:#555" onclick="this.select()">
<button onclick="recoverOld()" style="background:#805ad5">找回旧进度（如果你刚才确认过）</button>
<button onclick="exportCSV()">导出 CSV 文件</button>
<button onclick="showText()" style="background:#2f855a;margin-left:8px">显示结果文字（推荐·可直接复制）</button>
<textarea id="outbox" style="display:none;width:100%;height:200px;font-family:monospace;font-size:12px;margin-top:8px"></textarea>
<p id="hint" style="font-size:12px;color:#666"></p>
</header>
<table><thead><tr><th>#</th><th>集/时间</th><th>台词 + 上下文</th><th>三层判断 + 建议</th><th>音频/画面</th><th>真实说话人</th></tr></thead>
<tbody>{''.join(trs)}</tbody></table>
<script>
const CODE = {{code_js}};
let BOOT = false;
function buildCSV(){{
 let out=[['id','ep','t','text','who_sv','who_face','who_llm','suggest','人工真值']];
 document.querySelectorAll('tr.row').forEach(tr=>{{
   const c=tr.querySelectorAll('td');
   out.push([tr.dataset.id,c[1].innerText.split('\\n')[0],'',c[2].innerText.split('\\n')[0],
             c[3].innerText,'','','',c[5].querySelector('select').value]);
 }});
 return '\\ufeff'+out.map(r=>r.map(x=>'"'+String(x).replace(/"/g,'""')+'"').join(',')).join('\\n');
}}
function exportCSV(){{
 let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([buildCSV()],{{type:'text/csv'}}));
 a.download='真值表_人工确认结果.csv';document.body.appendChild(a);a.click();
 setTimeout(()=>{{URL.revokeObjectURL(a.href);a.remove();}},1000);
 document.getElementById('hint').innerText='已触发下载。如果预览窗口拦了下载，请用绿色按钮复制文字发我。';
}}
function showText(){{
 let box=document.getElementById('outbox');
 box.value=buildCSV();box.style.display='block';box.focus();box.select();
 document.getElementById('hint').innerText='已全选，按 Cmd/ctrl+C 复制，粘贴发给我即可。保存进度请复制上方链接。';
}}
function saveCode(){{
 // 把每句的选择编成一个字符，存进网址，换台机器/重开页面也能恢复
 let s=[...document.querySelectorAll('select.truth')].map(x=>x.value?CODE[x.value]:'0').join('');
 try{{localStorage.setItem('truth_code',s);}}catch(e){{}}
 history.replaceState(null,'','#'+s);
 document.getElementById('linkbox').value=location.href;
}}
function updProgress(){{
 let sels=[...document.querySelectorAll('select.truth')];
 let done=sels.filter(s=>s.value).length;
 document.getElementById('progress').innerText='已确认 '+done+' / '+sels.length+' 句';
 saveCode();
}}
function recoverOld(){{
 // 旧版把每句存在 truth_0..95，试着读回来
 const sels=[...document.querySelectorAll('select.truth')];
 let n=0;
 sels.forEach((s,i)=>{{
   let v='';try{{v=localStorage.getItem('truth_'+i)||'';}}catch(e){{}}
   if(v && [...s.options].some(o=>o.value===v)){{ s.value=v; n++; }}
 }});
 if(n){{updProgress();document.getElementById('hint').innerText='找回了 '+n+' 句旧进度。';}}
 else{{document.getElementById('hint').innerText='没找到旧进度——可能换了预览窗口，浏览器本地数据已失效，需要重新确认。';}}
}}
function restore(){{
 const sels=[...document.querySelectorAll('select.truth')];
 let code='';
 if(location.hash && location.hash.length>1) code=location.hash.slice(1);
 if(!code) {{try{{code=localStorage.getItem('truth_code')||'';}}catch(e){{}}}}
 if(code && code.length===sels.length){{
   const REV={{}};for(const k in CODE) REV[CODE[k]]=k;
   sels.forEach((s,i)=>{{ s.value=REV[code[i]]||''; }});
 }}
 document.getElementById('linkbox').value=location.href;
 BOOT=true;updProgress();
}}
document.querySelectorAll('select.truth').forEach(s=>{{
 s.onchange=()=>{{ if(BOOT) updProgress(); }};
}});
restore();
</script></body></html>'''.replace("{code_js}", json.dumps(CODE, ensure_ascii=False))

out = SRC / "逐句核对.html"
out.write_text(page, encoding="utf-8")
print("生成", out, len(rows), "句")
