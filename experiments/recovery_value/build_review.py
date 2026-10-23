"""Render saved recovery-value evidence and the versioned report offline."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import shutil


def inline(text):
    text = html.escape(text)
    text = re.sub(r'\[([^]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    return re.sub(r'`([^`]+)`', r'<code>\1</code>', text)


def render(text):
    out = []; table = False; ordered = False
    for line in text.splitlines():
        if table and not line.startswith('|'):
            out.append('</tbody></table></div>'); table = False
        if ordered and not re.match(r'^\d+\. ', line):
            out.append('</ol>'); ordered = False
        if not line: continue
        if line.startswith('|'):
            cells = [x.strip() for x in line.strip('|').split('|')]
            if all(re.fullmatch(r':?-+:?', c) for c in cells): continue
            if not table:
                out.append('<div class="table"><table><thead><tr>' + ''.join('<th>'+inline(c)+'</th>' for c in cells) + '</tr></thead><tbody>')
                table = True
            else: out.append('<tr>'+''.join('<td>'+inline(c)+'</td>' for c in cells)+'</tr>')
        elif line.startswith('#'):
            level = len(line) - len(line.lstrip('#'))
            out.append(f'<h{level}>'+inline(line[level:].strip())+f'</h{level}>')
        elif re.match(r'^\d+\. ', line):
            if not ordered: out.append('<ol>'); ordered = True
            out.append('<li>'+inline(re.sub(r'^\d+\. ', '', line))+'</li>')
        else: out.append('<p>'+inline(line)+'</p>')
    if table: out.append('</tbody></table></div>')
    if ordered: out.append('</ol>')
    return '\n'.join(out)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    assert not a.output.exists()
    a.output.mkdir(parents=True)
    for source, name in [('account/summary.json', 'summary.json'), ('account/inputs.json', 'inputs.json'),
                         ('verification.json', 'verification.json'), ('contribution-identity.json', 'contribution-identity.json'),
                         ('final/reproduction.json', 'reproduction.json')]:
        shutil.copyfile(a.run/source, a.output/name)
    sources = json.loads((a.run/'sources/sources.json').read_text())
    snippets = {'ptx': [('9.7.21.4. Miscellaneous Instructions: trap', 800)],
                'runtime_errors': [('cudaErrorAssert = 710', 420), ('cudaErrorIllegalInstruction = 715', 450),
                                   ('cudaErrorLaunchFailure = 719', 850)]}
    for row in sources['remote']:
        if row['status'] != 'read': continue
        raw = (a.run/'sources'/(row['id']+'.html')).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row['sha256']
        text = (a.run/'sources'/(row['id']+'.txt')).read_text()
        row['excerpts'] = []
        for marker, length in snippets[row['id']]:
            position = text.find(marker)
            assert position >= 0, marker
            row['excerpts'].append(dict(marker=marker, text=text[position:position+length]))
    (a.output/'sources.json').write_text(json.dumps(sources, ensure_ascii=False, indent=2)+'\n')
    report = (a.repo/'docs/reviews/MoE在途恢复价值与状态存活判别_20261023.md').read_text()
    # The markdown lives in reviews/; HTML is one directory further below docs/.
    body = render(report)
    body = body.replace('href="../../experiments/', 'href="../../../experiments/')
    body = body.replace('href="../review_artifacts/recovery_value_20261023/report.html"', 'href="report.html"')
    body = body.replace('href="创新点筛选与MoE安全恢复候选_20261023.md"', 'href="../../reviews/创新点筛选与MoE安全恢复候选_20261023.md"')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MoE在途恢复价值判别</title><style>body{font-family:system-ui,sans-serif;background:#f4f6fa;color:#172334;margin:0;line-height:1.85}main{max-width:1060px;margin:36px auto;padding:30px 38px;background:white;border-radius:14px}h1{font-size:1.9rem}h2{margin-top:2.2rem;color:#173f65}p,li{max-width:94ch}a{color:#155eaa}code{background:#edf2f7;padding:2px 4px;overflow-wrap:anywhere}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:.94rem}td,th{padding:10px 12px;border:1px solid #d8e0e9;text-align:left;vertical-align:top}th{background:#edf4fc}nav{background:#edf4fc;padding:12px;border-radius:8px}@media(max-width:700px){main{margin:0;padding:18px}h1{font-size:1.45rem}}</style><main><nav>证据：<a href="summary.json">工作量摘要</a> · <a href="verification.json">独立核验</a> · <a href="sources.json">来源</a> · <a href="contribution-identity.json">迁移身份检查</a> · <a href="reproduction.json">离线复现</a></nav>'+body+'</main></html>'
    (a.output/'report.html').write_text(page)
    assert sum(f.stat().st_size for f in a.output.iterdir()) < 256*1024
    print(json.dumps(dict(output=str(a.output), files=len(list(a.output.iterdir())))))


if __name__ == '__main__': main()
