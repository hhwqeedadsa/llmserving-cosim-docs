"""Final evidence/link/version audit only; added after network execution."""
import argparse
import ast
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess

from common import ROOT, HERE, load, need, save, sha
from moe_ub_cosim.host_probe import disk_usage


def check(root, exported, png_visually_reviewed=False):
    receipt = load(exported/'receipt.json')
    need(receipt['network_executions']==2 and receipt['successful_network_reruns']==0,'wrong execution scope')
    for name,digest in receipt['export_hashes'].items():
        need(sha(exported/name)==digest,'changed exported evidence '+name)
    prepared = load(root/'prepared/preparation.json')
    for name,digest in prepared['input_hashes'].items():
        need(sha(root/'prepared'/name)==digest,'changed frozen input '+name)
    for name,digest in prepared['method_hashes'].items():
        need(sha(HERE/name)==digest,'changed pre-execution method '+name)
    for name,digest in receipt['source_hashes'].items():
        need(sha(HERE/name)==digest,'changed source before closeout '+name)
    backend = receipt['backend']
    for name,digest in {backend['service']:backend['service_sha256'],**backend['libraries'],
                        **{str(ROOT/k):v for k,v in backend['source_hashes'].items()}}.items():
        need(sha(Path(name))==digest,'changed backend or library '+name)
    feedback = load(exported/'analysis/feedback.json')
    need(feedback['passed'] and feedback['actual_SACK_bytes_and_source_RX_checked'] and
         feedback['new_network_executions']==0,'missing actual feedback audit')
    links = []
    class Page(HTMLParser):
        def handle_starttag(self,tag,attrs):
            for name,value in attrs:
                if (tag=='a' and name=='href') or (tag=='img' and name=='src'):
                    links.append(value)
    count = 0
    for path in exported.rglob('*.html'):
        links.clear()
        page = Page();page.feed(path.read_text());page.close()
        for link in links:
            if not link.startswith(('https:','http:','#')):
                need((path.parent/link.split('#')[0]).is_file(),'missing HTML target '+link)
                count += 1
    for path in (HERE/'README.md',HERE/'protocol.md',exported/'README.md',exported/'interpretation.md'):
        for link in re.findall(r'\]\(([^)]+)\)',path.read_text()):
            if not link.startswith(('https:','http:','#')):
                need((path.parent/link.split('#')[0]).is_file(),'missing Markdown target '+link)
    for path in HERE.glob('*.py'):
        ast.parse(path.read_text(),filename=str(path))
    with (root/'deps.stdout').open('x') as out,(root/'deps.stderr').open('x') as err:
        code = subprocess.run(['bash','scripts/check_deps.sh'],cwd=ROOT,stdout=out,stderr=err).returncode
    need(code==0,'dependency check failed')
    diff = subprocess.run(['git','diff','--check'],cwd=ROOT,text=True,capture_output=True)
    need(diff.returncode==0,'whitespace check failed: '+diff.stdout+diff.stderr)
    probes = {str(p.relative_to(root)):load(p) for p in sorted((root/'monitors').glob('*/probe.json'))}
    required = {f'monitors/{name}/probe.json' for name in ('prepare','normal','local','analyze','feedback','closeout')}
    need(required <= set(probes) and all(p['status']=='completed' and p['child_exit_code']==0 and
         not p['remaining_live_group_pids'] for p in probes.values()),'failed or missing completed commands')
    seconds = sum(p['wall_seconds'] for p in probes.values())
    need(seconds<900,'time budget exceeded')
    usage = disk_usage(root)
    export_usage = disk_usage(exported)
    need(max(usage.values())+max(export_usage.values())<512*2**20,'task artifact budget exceeded')
    result = dict(passed=True,network_executions=2,new_network_executions_during_check=0,
        execution_commit=receipt['execution_commit'],frozen_inputs_and_methods_revalidated=True,
        original_result_and_export_hashes_revalidated=True,actual_feedback_checked=True,
        html_links_and_images_checked=count,html_parsed=True,scientific_PNG_visual_review_declared=png_visually_reviewed,
        dependency_check_exit=code,git_whitespace_check_exit=diff.returncode,
        completed_monitors_before_final_check=probes,
        monitored_seconds_before_final_check=seconds,peak_group_rss_before_final_check=max(
            p['peak_sampled_group_rss_bytes'] for p in probes.values()),
        raw_usage=usage,export_usage=export_usage,
        post_execution_readers=['feedback.py','check.py'],remaining_research_goal='active; no novel algorithm gain established',
        export_sha256={str(p.relative_to(exported)):sha(p) for p in exported.rglob('*') if p.is_file()})
    save(exported/'final-audit.json',result)
    print(json.dumps({k:result[k] for k in ('passed','network_executions','html_links_and_images_checked',
        'monitored_seconds_before_final_check','peak_group_rss_before_final_check','raw_usage','export_usage')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--exported',type=Path,required=True)
    parser.add_argument('--png-visually-reviewed',action='store_true',help='Record a completed manual review; no automatic rendering claim')
    args = parser.parse_args()
    check(args.root.resolve(),args.exported.resolve(),args.png_visually_reviewed)
