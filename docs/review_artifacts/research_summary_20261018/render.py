"""Render this review's Markdown subset to an offline HTML document; stdlib only.

From the repository root:
  python3 docs/review_artifacts/research_summary_20261018/render.py

No simulation, network access, or dependency installation is performed.
"""
from hashlib import sha256
from html import escape
from pathlib import Path
import json
import os
import re

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1] / 'reviews/研究工作总报告_目标约束与ScaleOut网络路线_20261018.md'
OUTPUT = HERE / 'report.html'


def inline(value):
    parts = re.split(r'(`[^`]+`|\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*)', value)
    result = []
    for part in parts:
        if part.startswith('`') and part.endswith('`'):
            result.append('<code>' + escape(part[1:-1]) + '</code>')
        elif part.startswith('**') and part.endswith('**'):
            result.append('<strong>' + inline(part[2:-2]) + '</strong>')
        elif re.fullmatch(r'\[[^\]]+\]\([^)]+\)', part):
            label, url = re.fullmatch(r'\[([^\]]+)\]\(([^)]+)\)', part).groups()
            if not re.match(r'^[a-z]+://', url) and not url.startswith('#'):
                path, separator, fragment = url.partition('#')
                url = os.path.relpath(SOURCE.parent / path, HERE)
                if separator:
                    url += '#' + fragment
            result.append('<a href="' + escape(url, quote=True) + '">' + inline(label) + '</a>')
        else:
            safe = escape(part)
            safe = re.sub(r'\[S(\d+)\]', r'<a class="cite" href="#source-s\1">[S\1]</a>', safe)
            result.append(safe)
    return ''.join(result)


def render():
    lines = SOURCE.read_text().splitlines()
    body, toc = [], []
    i, heading = 0, 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith('```'):
            code = []
            i += 1
            while i < len(lines) and not lines[i].startswith('```'):
                code.append(lines[i])
                i += 1
            if i == len(lines):
                raise ValueError('Unclosed code fence')
            body.append('<pre><code>' + escape('\n'.join(code)) + '</code></pre>')
            i += 1
        elif re.match(r'^#{1,3} ', line):
            level = len(line.split(' ', 1)[0])
            title = line[level + 1:]
            heading += 1
            anchor = 'section-' + str(heading)
            body.append(f'<h{level} id="{anchor}">{inline(title)}</h{level}>')
            if level == 2:
                toc.append(f'<a href="#{anchor}">{inline(title)}</a>')
            i += 1
        elif line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].startswith('|'):
                rows.append([cell.strip() for cell in lines[i].strip('|').split('|')])
                i += 1
            if len(rows) < 2 or not all(re.fullmatch(r':?-+:?', cell) for cell in rows[1]):
                raise ValueError('Table separator missing')
            if any(len(row) != len(rows[0]) for row in rows):
                raise ValueError('Table cell count differs')
            table = '<thead><tr>' + ''.join('<th>' + inline(c) + '</th>' for c in rows[0]) + '</tr></thead><tbody>'
            table += ''.join('<tr>' + ''.join('<td>' + inline(c) + '</td>' for c in row) + '</tr>' for row in rows[2:])
            body.append('<div class="table-wrap"><table>' + table + '</tbody></table></div>')
        elif line.startswith('- '):
            items = []
            while i < len(lines) and lines[i].startswith('- '):
                source = re.match(r'- \*\*\[S(\d+)\]\*\*', lines[i])
                anchor = f' id="source-s{source[1]}"' if source else ''
                items.append('<li' + anchor + '>' + inline(lines[i][2:]) + '</li>')
                i += 1
            body.append('<ul>' + ''.join(items) + '</ul>')
        else:
            paragraph = []
            while i < len(lines) and lines[i].strip() and not re.match(r'^(#{1,3} |```|\||- )', lines[i]):
                paragraph.append(lines[i].strip())
                i += 1
            body.append('<p>' + '<br>'.join(inline(p) for p in paragraph) + '</p>')
    css = '''
:root{color-scheme:light;--ink:#203143;--muted:#607286;--blue:#14567d;--line:#dbe4ec}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:24px}
body{margin:0;background:#f3f6f9;color:var(--ink);font:16px/1.85 system-ui,"Noto Sans CJK SC","Microsoft YaHei",sans-serif}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.layout{display:grid;grid-template-columns:255px minmax(0,1fr);max-width:1650px;margin:auto}
aside{padding:30px 20px;position:sticky;top:0;height:100vh;overflow:auto;font-size:13px;line-height:1.6}
aside strong{font-size:15px;display:block;margin-bottom:12px}nav a{display:block;padding:7px 0;border-bottom:1px solid var(--line)}
main{min-width:0;background:white;padding:40px clamp(20px,4vw,65px) 80px}
.badge{font-size:12px;color:var(--blue);letter-spacing:.08em;font-weight:650}.tools{font-size:13px;color:var(--muted);margin:10px 0 25px}
h1{font-size:clamp(25px,2.6vw,36px);line-height:1.45;margin:8px 0 22px}
h2{font-size:24px;color:var(--blue);margin:56px 0 20px;padding-top:16px;border-top:2px solid var(--line)}
h3{font-size:19px;margin:28px 0 12px}p{margin:14px 0}li{margin:8px 0}strong{font-weight:700}
code{font:13px/1.6 ui-monospace,monospace;background:#edf2f6;padding:2px 5px;border-radius:4px;overflow-wrap:anywhere}
pre{overflow:auto;padding:22px;background:#edf3f7;border-left:3px solid var(--blue);line-height:1.65}
pre code{padding:0;font-size:13px;white-space:pre;background:none}.cite{font-size:12px;white-space:nowrap}
.table-wrap{overflow-x:auto;margin:20px 0;border:1px solid var(--line);border-radius:5px}
table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.7;min-width:650px}
th,td{padding:12px 14px;text-align:left;vertical-align:top;border-right:1px solid var(--line);border-bottom:1px solid var(--line)}
th{background:#e9f1f6;color:#173e5c;font-weight:650}tr:nth-child(even) td{background:#f8fafc}td:first-child{min-width:115px}
.note{padding:12px 16px;background:#eef5fa;font-size:13px;color:var(--muted)}
@media(max-width:900px){.layout{display:block}aside{position:static;height:auto;padding:20px}nav{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 20px}main{padding:25px 18px 60px}h2{font-size:21px}}
@media print{body{background:white;font-size:10pt}.layout{display:block}aside,.tools,.badge{display:none}main{padding:0}h1{font-size:22pt}h2{font-size:15pt;margin-top:24pt;break-after:avoid}h3{break-after:avoid}.table-wrap{overflow:visible}table{min-width:0;font-size:8pt}th,td{padding:6pt}tr{break-inside:avoid}pre{white-space:pre-wrap}a{color:inherit}p,li{orphans:3;widows:3}}
'''
    source_link = os.path.relpath(SOURCE, HERE)
    document = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    document += '<title>MoE推理联合仿真研究总报告 · 2026-10-18</title><style>' + css + '</style></head><body>'
    document += '<div class="layout"><aside><strong>研究总报告 · 2026-10-18</strong><nav>' + ''.join(toc) + '</nav></aside><main>'
    document += '<div class="badge">MoE NETWORK RESEARCH / SCALE-OUT</div><div class="tools">离线阅读版 · <a href="' + escape(source_link, quote=True) + '">可编辑Markdown原文</a> · 可使用浏览器打印为PDF</div>'
    document += ''.join(body) + '<p class="note">本页只汇总已有证据和后续规划，不启动仿真；完整运行条件和限制见引用报告。文档基线为2026-10-18。</p></main></div></body></html>\n'
    OUTPUT.write_text(document)
    manifest = {
        'source': os.path.relpath(SOURCE, HERE),
        'source_sha256': sha256(SOURCE.read_bytes()).hexdigest(),
        'renderer_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
        'output': OUTPUT.name,
        'output_sha256': sha256(OUTPUT.read_bytes()).hexdigest(),
        'scope': 'documentation_only_no_new_simulation',
    }
    (HERE / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(f'Rendered {OUTPUT.name}: {len(toc)} sections, {len(document)} characters')


if __name__ == '__main__':
    render()
