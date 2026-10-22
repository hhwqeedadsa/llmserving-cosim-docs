"""Render this bounded review Markdown with local links, without dependencies."""
import html
import os
from pathlib import Path
import re

HERE=Path(__file__).resolve().parent
SOURCE=HERE.parents[1]/'reviews/ScaleOut仿真结构改进与实践_20261022.md'


def inline(s):
    tokens=re.split(r'(\[[^\]]+\]\([^)]+\)|`[^`]+`|\*\*[^*]+\*\*)',s)
    parts=[]
    for token in tokens:
        link=re.fullmatch(r'\[([^\]]+)\]\(([^)]+)\)',token)
        if link:
            label,target=link.groups()
            if '://' not in target:target=os.path.relpath((SOURCE.parent/target).resolve(),HERE)
            parts.append('<a href="'+html.escape(target,quote=True)+'">'+html.escape(label)+'</a>')
        elif token.startswith('`') and token.endswith('`'):parts.append('<code>'+html.escape(token[1:-1])+'</code>')
        elif token.startswith('**') and token.endswith('**'):parts.append('<strong>'+html.escape(token[2:-2])+'</strong>')
        else:parts.append(html.escape(token))
    return ''.join(parts)


def main():
    parts=[];table=False
    for line in SOURCE.read_text().splitlines():
        if line.startswith('|'):
            if re.match(r'^\|[-: ]+\|',line):continue
            if not table:parts.append('<div class="table"><table>');table=True
            parts.append('<tr>'+''.join('<td>'+inline(cell.strip())+'</td>' for cell in line.strip('|').split('|'))+'</tr>')
            continue
        if table:parts.append('</table></div>');table=False
        h=re.match(r'^(#{1,3}) (.*)$',line)
        if h:parts.append(f'<h{len(h[1])}>'+inline(h[2])+f'</h{len(h[1])}>')
        elif line:parts.append('<p>'+inline(line)+'</p>')
    if table:parts.append('</table></div>')
    style='body{font:17px/1.85 system-ui,sans-serif;color:#192e3e;background:#fbfcfe;max-width:1180px;margin:40px auto;padding:0 28px}h1{font-size:32px;line-height:1.4}h2{font-size:25px;margin-top:46px;border-bottom:2px solid #d4e2eb;padding-bottom:10px}h3{font-size:20px;margin-top:28px}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:15px}td{border:1px solid #cbd9e3;padding:10px;vertical-align:top;min-width:110px}tr:first-child{font-weight:600;background:#e6f0f6}a{color:#065e98}code{font-size:.88em;background:#edf2f6;padding:2px 4px;overflow-wrap:anywhere}nav{font-size:15px;margin-bottom:24px}p{overflow-wrap:anywhere}'
    (HERE/'architecture.html').write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Scale-Out仿真结构改进与实践</title><style>'+style+'</style></head><body><nav><a href="report.html">查看实验数值</a> · <a href="summary.json">详细数据</a></nav>'+''.join(parts)+'</body></html>')


if __name__=='__main__':main()
