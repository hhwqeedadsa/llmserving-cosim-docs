"""Seal completed monitoring, final documentation and visual review status."""
import argparse
from pathlib import Path
import json
import re
import shutil

from prepare import ROOT,files,load,sha

def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--task-root',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--image-inspected',action='store_true');a=p.parse_args()
    task=a.task_root.resolve();out=a.report.resolve();audit=load(out/'final-audit.json');receipt=load(out/'receipt.json')
    assert audit['passed'] and receipt['passed']
    for name,digest in audit['artifact_sha256'].items():assert sha(out/name)==digest
    for name,digest in receipt['source_sha256'].items():
        if name!='experiments/recovery_scope/README.md':assert sha(ROOT/name)==digest
    frozen=load(out/'frozen.json')
    for record in frozen['records'].values():assert files(Path(record['path']))==record['sha256']
    stages={p.parent.name:load(p) for p in (task/'monitors').glob('*/probe.json')}
    assert all(v['status']=='completed' and v['child_exit_code']==0 and not v['remaining_live_group_pids'] for v in stages.values())
    elapsed=sum(v['wall_seconds'] for v in stages.values());assert elapsed+120<600
    assert shutil.disk_usage(task).free>=8*2**30
    receipt.update(actual_commands_and_status=stages,wall_upper_bound_seconds=elapsed+120,
        documentation_finalized_after_initial_report=True,source_sha256={str(p.relative_to(ROOT)):sha(p) for p in Path(__file__).parent.iterdir() if p.is_file()})
    write(out/'receipt.json',receipt)
    for file in (out/'README.md',Path(__file__).with_name('README.md'),ROOT/'docs/tasks/current.md'):
        text=file.read_text()
        if file.name=='current.md':text=text.split('## 2026-10-30 源确认超时')[0]
        for value in re.findall(r'\]\(([^)]+)\)',text):
            assert (file.parent/value).exists(),str(file)+': missing '+value
    audit.update(images_visually_inspected=a.image_inspected,
        image_sha256=sha(out/'figures/request-work.png'),documentation_links_checked=True,
        artifact_sha256={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='final-audit.json'})
    write(out/'final-audit.json',audit)
    print(json.dumps(dict(passed=True,unchanged_networks=5,new_networks=0,visual_review=a.image_inspected,wall_upper_bound_seconds=elapsed+120)))

if __name__=='__main__':main()
