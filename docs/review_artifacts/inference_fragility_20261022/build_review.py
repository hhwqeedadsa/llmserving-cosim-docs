"""Render the current architecture and conditional proof for offline review."""
import html
import os
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
DOCS = HERE.parents[1]
STYLE = """body{font:17px/1.85 system-ui,sans-serif;color:#192e3e;background:#fbfcfe;max-width:1180px;margin:40px auto;padding:0 28px}
h1{font-size:32px;line-height:1.4}h2{font-size:25px;margin-top:42px;border-bottom:2px solid #d4e2eb;padding-bottom:10px}
h3{font-size:20px}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:15px}
td{border:1px solid #cbd9e3;padding:10px;vertical-align:top;min-width:100px}tr:first-child{font-weight:600;background:#e6f0f6}
a{color:#065e98}code{font-size:.88em;background:#edf2f6;padding:2px 4px;overflow-wrap:anywhere}
pre{white-space:pre-wrap;background:#edf2f6;padding:16px}nav{font-size:15px;margin-bottom:24px}p{overflow-wrap:anywhere}
@media print{body{max-width:none;margin:0;font-size:12px}h2{break-after:avoid}tr{break-inside:avoid}nav{display:none}}
"""


def inline(line, source):
    tokens = re.split(r'(\[[^\]]+\]\([^)]+\)|`[^`]+`|\*\*[^*]+\*\*)', line)
    parts = []
    for token in tokens:
        link = re.fullmatch(r'\[([^\]]+)\]\(([^)]+)\)', token)
        if link:
            label, target = link.groups()
            if '://' not in target and not target.startswith('#'):
                target = os.path.relpath((source.parent / target).resolve(), HERE)
            parts.append('<a href="' + html.escape(target, quote=True) + '">' + html.escape(label) + '</a>')
        elif token.startswith('`') and token.endswith('`'):
            parts.append('<code>' + html.escape(token[1:-1]) + '</code>')
        elif token.startswith('**') and token.endswith('**'):
            parts.append('<strong>' + html.escape(token[2:-2]) + '</strong>')
        else:
            parts.append(html.escape(token))
    return ''.join(parts)


def render(source, output, title):
    parts = []
    table = False
    code = False
    for line in source.read_text().splitlines():
        if line.startswith('```'):
            parts.append('</code></pre>' if code else '<pre><code>')
            code = not code
            continue
        if code:
            parts.append(html.escape(line) + '\n')
            continue
        if line.startswith('|'):
            if re.match(r'^\|[-: ]+\|', line):
                continue
            if not table:
                parts.append('<div class="table"><table>')
                table = True
            parts.append('<tr>' + ''.join('<td>' + inline(cell.strip(), source) + '</td>'
                                         for cell in line.strip('|').split('|')) + '</tr>')
            continue
        if table:
            parts.append('</table></div>')
            table = False
        heading = re.match(r'^(#{1,3}) (.*)$', line)
        if heading:
            n = len(heading[1])
            parts.append(f'<h{n}>' + inline(heading[2], source) + f'</h{n}>')
        elif line:
            parts.append('<p>' + inline(line, source) + '</p>')
    if table:
        parts.append('</table></div>')
    assert not code, 'Unclosed Markdown code fence'
    nav = '<nav><a href="architecture.html">整体改进</a> · <a href="report.html">实验数值</a> · <a href="formal.html">条件证明</a> · <a href="summary.json">详细数据</a></nav>'
    output.write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                      '<meta name="viewport" content="width=device-width,initial-scale=1"><title>'
                      + html.escape(title) + '</title><style>' + STYLE + '</style></head><body>'
                      + nav + ''.join(parts) + '</body></html>')


if __name__ == '__main__':
    render(DOCS / 'reviews/推理通信脆弱性与形式化改进_20261022.md', HERE / 'architecture.html', '推理通信脆弱性与联合仿真改进')
    render(DOCS / 'communication_fragility.md', HERE / 'formal.html', '推理通信脆弱性：条件证明')
