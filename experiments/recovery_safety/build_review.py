"""Build a review from saved evidence; never fetch or run the simulator."""
import argparse
from copy import deepcopy
import hashlib
from html import escape
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
REPORT='docs/reviews/创新点筛选与MoE安全恢复候选_20261023.md'
PASSAGES={
 'nccl_errors':'For more advanced recovery',
 'redmark':'ReDMArk shows',
 'varuna':'A rare corner case exists',
 'byteexpress':'The measurements in this paper do not establish',
 'optinic':'Unlike traditional RDMA',
 'moc':'Partial Experts Checkpointing',
 'elastic_versioned':'Worker failure:',
 'eep':'Requests in flight at the moment of failure',
 'rcu':'grace period',
 'nccl_fixed_comms':'NCCL_SHRINK_ABORT'}

def save(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def render(markdown):
    def inline(s):
        s=escape(s)
        def link(m):
            label,target=m.groups()
            # Relative links are resolved from this report HTML's directory.
            if target.startswith('../review_artifacts/recovery_safety_20261023/'):
                target=target.removeprefix('../review_artifacts/recovery_safety_20261023/')
            elif target.startswith('../../experiments/'):target='../../../experiments/'+target.removeprefix('../../experiments/')
            return '<a href="'+target+'">'+label+'</a>'
        s=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',link,s)
        s=re.sub(r'\*\*([^*]+)\*\*',r'<strong>\1</strong>',s)
        return re.sub(r'`([^`]+)`',r'<code>\1</code>',s)
    out=[];table=False
    for line in markdown.splitlines():
        if line.startswith('|'):
            if not table:out.append('<div class="table"><table>');table=True
            if re.fullmatch(r'[| :\-]+',line):continue
            out.append('<tr>'+''.join('<td>'+inline(cell.strip())+'</td>' for cell in line.strip('|').split('|'))+'</tr>');continue
        if table:out.append('</table></div>');table=False
        if not line.strip():continue
        if line.startswith('# '):out.append('<h1>'+inline(line[2:])+'</h1>')
        elif line.startswith('## '):out.append('<h2>'+inline(line[3:])+'</h2>')
        else:out.append('<p>'+inline(line)+'</p>')
    if table:out.append('</table></div>')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>创新点筛选与 MoE 安全恢复候选</title><style>
body{max-width:1160px;margin:40px auto;padding:0 24px;font:16px/1.85 system-ui,sans-serif;color:#18243a;background:#fafbfd}
h1{font-size:29px}h2{margin-top:38px;border-bottom:1px solid #ccd5e1;padding-bottom:8px}p{max-width:1050px}
.table{overflow-x:auto}table{border-collapse:collapse;background:white;width:100%;font-size:15px}
td{border:1px solid #d7dfeb;padding:12px;vertical-align:top;min-width:140px}tr:first-child{background:#eaf0f8;font-weight:600}
a{color:#175e9b}code{background:#e8edf4;padding:1px 4px;border-radius:3px}strong{color:#713f12}
@media print{body{margin:0;max-width:none}h2{break-after:avoid}tr{break-inside:avoid}}
</style><main>'''+''.join(out)+'</main></html>\n'


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    run=a.run.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    summary=json.loads((run/'model/summary.json').read_text());assert summary['passed']
    assert summary['source_sha256']==sha(ROOT/'experiments/recovery_safety/check_model.py')
    sources=[];total=0
    for group in ('sources','sources-followup','sources-targeted'):
        manifest=json.loads((run/group/'remote-sources.json').read_text())
        for record in manifest['sources']:
            r=deepcopy(record);r['snapshot']=str(Path(group)/(r['id']+'.html'))
            if r['status']=='read':
                raw=run/r['snapshot'];assert sha(raw)==r['sha256'];total+=raw.stat().st_size
                text=(run/group/(r['id']+'.txt')).read_text()
                if r['id']=='elastic':r['content_scope']='redirect_only; use elastic_versioned for substantive content'
                elif r['id'].endswith('search'):r['content_scope']='search metadata, not paper-body verification or comprehensive novelty search'
                else:
                    r['content_scope']='selected primary document'
                    needle=PASSAGES[r['id']];where=text.lower().find(needle.lower())
                    assert where>=0,(r['id'],needle)
                    # Tiny passage locator, not an extraction of the whole work.
                    r['passage']=dict(character_offset=where,text=text[where:where+200])
            sources.append(r)
    assert total<=12*2**20
    fixed=json.loads((run/'sources/fixed-sources.json').read_text())
    save(out/'summary.json',summary);save(out/'sources.json',dict(fixed=fixed,remote=sources,downloaded_bytes=total,
        novelty_search_complete=False,scope='source identity and selected scope checks; no production exploit or performance proof'))
    (out/'report.html').write_text(render((ROOT/REPORT).read_text()))
    save(out/'inputs.json',dict(model_sha256=sha(run/'model/summary.json'),report_sha256=sha(ROOT/REPORT),
        scripts={str(p.relative_to(ROOT)):sha(p) for p in sorted((ROOT/'experiments/recovery_safety').glob('*.py'))},
        total_network_orders=sum(c['counts']['network_orders'] for c in summary['cases']),
        total_consumer_interleavings=sum(c['counts']['consumer_interleavings'] for c in summary['cases'])))
    print(json.dumps(dict(passed=True,documents=len(sources),fixed_files=len(fixed['sources']),downloaded_bytes=total,
        report=str(out/'report.html'),production_vulnerability_demonstrated=False)))
if __name__=='__main__':main()
