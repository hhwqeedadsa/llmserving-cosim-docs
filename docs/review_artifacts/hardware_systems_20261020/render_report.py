#!/usr/bin/env python3
"""Render this review's limited Markdown syntax with only the Python stdlib."""
import hashlib
import html
import os
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent.parent / 'reviews/宏微结合与硬件联合仿真研究_20261020.md'


def inline(value):
    tokens = []

    def preserve(match):
        label, target = match.groups()
        if not re.match(r'https?://', target):
            path, separator, fragment = target.partition('#')
            target = os.path.relpath((SOURCE.parent / path).resolve(), HERE)
            if separator:
                target += '#' + fragment
        tokens.append('<a href="' + html.escape(target, quote=True) + '">'
                      + html.escape(label) + '</a>')
        return f'\x00{len(tokens)-1}\x00'

    value = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', preserve, value)

    def code(match):
        tokens.append('<code>' + html.escape(match[1]) + '</code>')
        return f'\x00{len(tokens)-1}\x00'

    value = re.sub(r'`([^`]+)`', code, value)
    value = html.escape(value)
    value = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', value)
    return re.sub(r'\x00(\d+)\x00', lambda m: tokens[int(m[1])], value)


def render():
    lines = SOURCE.read_text().splitlines()
    parts, toc = [], []
    i, heading_count = 0, 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith('```'):
            block = []
            i += 1
            while i < len(lines) and not lines[i].startswith('```'):
                block.append(lines[i])
                i += 1
            parts.append('<pre><code>' + html.escape('\n'.join(block)) + '</code></pre>')
            i += 1
            continue
        match = re.match(r'^(#{1,3}) (.+)$', line)
        if match:
            level, label = len(match[1]), match[2]
            heading_count += 1
            anchor = f'section-{heading_count}'
            parts.append(f'<h{level} id="{anchor}">{inline(label)}</h{level}>')
            if level == 2:
                toc.append(f'<a href="#{anchor}">{html.escape(label)}</a>')
            i += 1
            continue
        if line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].startswith('|'):
                cells = [cell.strip() for cell in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?', cell) for cell in cells):
                    rows.append(cells)
                i += 1
            parts.append('<div class="table-wrap"><table><thead><tr>'
                         + ''.join('<th>' + inline(c) + '</th>' for c in rows[0])
                         + '</tr></thead><tbody>'
                         + ''.join('<tr>' + ''.join('<td>' + inline(c) + '</td>' for c in row)
                                   + '</tr>' for row in rows[1:])
                         + '</tbody></table></div>')
            continue
        list_match = re.match(r'^(?:- |\d+\. )(.+)$', line)
        if list_match:
            ordered = bool(re.match(r'^\d+\.', line))
            tag = 'ol' if ordered else 'ul'
            entries = []
            pattern = r'^\d+\. (.+)$' if ordered else r'^- (.+)$'
            while i < len(lines):
                match = re.match(pattern, lines[i])
                if not match:
                    break
                entries.append('<li>' + inline(match[1]) + '</li>')
                i += 1
            parts.append(f'<{tag}>' + ''.join(entries) + f'</{tag}>')
            continue
        paragraph = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r'^(?:#|\||```|- |\d+\. )', lines[i]):
            paragraph.append(lines[i])
            i += 1
        parts.append('<p>' + inline(' '.join(paragraph)) + '</p>')
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    css = '''
    :root{color-scheme:light;--ink:#202534;--muted:#596477;--accent:#5344a1}
    *{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f5f5f9;
    color:var(--ink);font:16px/1.85 system-ui,-apple-system,"Noto Sans CJK SC","Microsoft YaHei",sans-serif}
    .layout{display:grid;grid-template-columns:240px minmax(0,1100px);gap:32px;max-width:1420px;margin:0 auto;padding:28px}
    nav{position:sticky;top:24px;height:calc(100vh - 48px);overflow:auto;font-size:14px}
    nav b{display:block;margin-bottom:16px}nav a{display:block;padding:7px 10px;border-left:2px solid #ddd9ef}
    a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}main{min-width:0;background:white;
    border:1px solid #e5e4ed;border-radius:12px;padding:36px 40px}h1{font-size:29px;line-height:1.5;margin-top:0}
    h2{font-size:23px;border-top:1px solid #e5e4ed;padding-top:28px;margin-top:38px;scroll-margin-top:20px}
    h3{font-size:19px;margin-top:27px}p{margin:16px 0}li{margin:10px 0}strong{font-weight:650}
    .meta{color:var(--muted);font-size:13px;border-bottom:1px solid #e5e4ed;padding-bottom:16px;margin-bottom:24px}
    .table-wrap{overflow-x:auto;margin:20px 0}table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.75}
    th,td{border:1px solid #dddfe8;padding:11px 13px;text-align:left;vertical-align:top;min-width:125px}
    th{background:#f1eff9;font-weight:650}tbody tr:nth-child(even){background:#fafafe}
    code{font-family:ui-monospace,monospace;font-size:.88em;background:#f1f1f6;padding:2px 4px;border-radius:3px;overflow-wrap:anywhere}
    pre{overflow:auto;background:#f4f3fa;padding:18px;border-radius:7px;line-height:1.8}pre code{padding:0;white-space:pre;overflow-wrap:normal}
    footer{font-size:12px;color:var(--muted);margin-top:35px;overflow-wrap:anywhere}
    @media(max-width:950px){.layout{display:block;padding:12px}nav{position:static;height:auto;margin-bottom:20px}
    nav a{display:inline-block;padding:5px 10px}main{padding:22px 18px}h1{font-size:25px}}
    @media print{body{background:white;font-size:11pt}.layout{display:block;padding:0}nav{display:none}
    main{border:0;padding:0}.table-wrap{overflow:visible}table{font-size:9pt}h2,h3{break-after:avoid}tr{break-inside:avoid}}
    '''
    document = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>MoE Scale-Out：宏微结合与硬件联合仿真研究</title><style>' + css + '</style></head><body>'
                '<div class="layout"><nav aria-label="目录"><b>研究评审 · 2026-10-20</b>'
                + ''.join(toc) + '</nav><main><div class="meta">源码核查与研究方案 · 已验证事实与待验证假设分别标注</div>'
                + '\n'.join(parts) + '<footer>来源Markdown SHA256：' + digest
                + '。本页离线可读，无外部脚本、字体或统计请求。</footer></main></div></body></html>\n')
    (HERE / 'report.html').write_text(document)
    print('Rendered', len(toc), 'main sections to', HERE / 'report.html')


if __name__ == '__main__':
    render()
