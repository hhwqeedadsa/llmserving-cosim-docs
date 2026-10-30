"""Package verified local results, exact execution receipts and resource limits."""
import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote,urlsplit

from prepare import ROOT,files,load,sha

class Links(HTMLParser):
    def __init__(self):super().__init__();self.targets=[];self.scripts=0
    def handle_starttag(self,tag,attrs):
        self.scripts+=tag=='script';self.targets.extend(v for k,v in attrs if k in ('href','src'))

def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--task-root',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    task=a.task_root.resolve();report=a.report.resolve();out=a.output.resolve()
    frozen=load(task/'study/frozen.json');analysis=load(task/'analysis/analysis.json');mechanism=load(task/'analysis/mechanism.json')
    assert analysis['passed'] and mechanism['passed'] and load(task/'analysis/prefix-checks.json')['passed']
    assert files(Path(frozen['prepared']))==frozen['prepared_sha256']
    for path,digest in frozen['source_sha256'].items():assert sha(ROOT/path)==digest
    assert sha(Path(frozen['backend']['path']))==frozen['backend']['sha256']
    for path,digest in frozen['loaded_ns3_libraries'].items():assert sha(Path(path))==digest
    for case in frozen['inherited_cases'].values():assert files(Path(case['output']))==case['sha256']
    assert sha(Path(__file__).with_name('analyze.py'))==analysis['analyzer_sha256']
    assert sha(Path(__file__).with_name('explain.py'))==mechanism['analyzer_sha256']
    stages={p.parent.name:load(p) for p in (task/'monitors').glob('*/probe.json')}
    assert {name for name,value in stages.items() if value['status']!='completed'}=={'cpu-regression'}
    assert stages['cpu-regression']['child_exit_code']==1
    assert all(not v['remaining_live_group_pids'] for v in stages.values())
    networks={};native_probes=list((task/'study').glob('*/execution/probe.json'));assert len(native_probes)==4
    for name,case in frozen['new_cases'].items():
        path=Path(case['output']);probe=load(path/'execution/probe.json');command=load(task/'study'/(name+'-command.json'))
        assert probe['status']=='completed' and probe['child_exit_code']==0 and not probe['remaining_live_group_pids']
        assert load(path/'verification.json')['passed'] and load(path/'result.json')['drained']
        assert command['status']=='completed' and command['exit_code']==0
        assert command['command'][2:6]==['-m','moe_ub_cosim','scaleout','rdma-run']
        networks[name]=dict(output=case['output'],sha256=files(path),command=command,native_probe=probe)
    assert {name for name in stages if name.startswith('network-')}=={'network-'+name for name in networks}
    measured=sum(v['wall_seconds'] for v in stages.values());nested=sum(load(p)['wall_seconds'] for p in native_probes)
    assert measured+nested+120<1200
    assert analysis['effects']['guarded-normal']['all_packets_exact'] and analysis['effects']['guarded-normal']['all_queue_events_exact']
    assert all(mechanism['fixed_guarded_network_comparison']['local'].values())
    assert len(load(task/'analysis/prefix-checks.json')['negative_cases'])==16
    assert len(set(load(ROOT/'fixtures/ack_guard_whole/pre-network.json')['tests_passed_unique']))==77
    assert len(mechanism['healthy_gates']['normal'])==len(mechanism['healthy_gates']['local'])==8
    assert all(analysis['effects'][name]['healthy_hops_matched']==4264 and analysis['effects'][name]['healthy_hops_unmatched']==0 for name in frozen['new_cases'])
    assert load(report/'frozen.json')==frozen
    for name in ('analysis.json','mechanism.json','prefix-checks.json'):assert sha(report/name)==sha(task/'analysis'/name)
    assert len(list((report/'figures').glob('*.png')))==len(list((report/'figures').glob('*.svg')))==2
    for path in (report/'metrics').glob('*.csv'):assert b'\r' not in path.read_bytes() and sha(path)==sha(task/'analysis'/path.name)
    assert not out.exists();shutil.copytree(report,out)
    for name in ('receipt.json','resources.json','final-audit.json'):save(out/name,dict(passed=False,status='checking'))
    source_paths=sorted(path for path in Path(__file__).parent.iterdir() if path.is_file())
    receipt=dict(passed=True,preregistration_commit='eec6935810',platform_commit='5491fe2575',
        analysis_platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        new_network_executions=4,network_reruns=0,new_networks=networks,inherited_networks_unchanged=2,
        actual_commands_and_status=stages,source_sha256={str(path.relative_to(ROOT)):sha(path) for path in source_paths},
        failed_command_retained='cpu-regression: nonexistent service_wakeup_test; corrected invocation passes',
        report_layout_correction='First race legend overlapped title; corrected only renderer and retained both report stages; zero network reruns',
        new_algorithm_benefit_proven=False,overall_goal_complete=False)
    save(out/'receipt.json',receipt)
    usage={};seen=set()
    for base in (task,ROOT/'experiments/ack_guard_whole',ROOT/'fixtures/ack_guard_whole'):
        apparent=allocated=0
        for path in (base,*base.rglob('*')):
            st=path.lstat();key=st.st_dev,st.st_ino
            if key in seen:continue
            seen.add(key);apparent+=st.st_size;allocated+=st.st_blocks*512
        usage[str(base.relative_to(ROOT))]=dict(apparent_bytes=apparent,allocated_bytes=allocated)
    used=sum(max(v.values()) for v in usage.values());reserve=4*2**20
    assert used+reserve<2**30 and shutil.disk_usage(task).free>=8*2**30
    resources=dict(passed=True,completed_stage_wall_seconds=measured,nested_native_wall_seconds=nested,
        native_time_already_inside_outer_stage=True,seal_stage_reserved_seconds=120,
        conservative_wall_upper_bound_seconds=measured+nested+120,cumulative_wall_budget_seconds=1200,
        artifact_usage_before_final_receipts=usage,artifact_bytes_before_final_receipts=used,
        final_receipts_and_small_fixture_reserve_bytes=reserve,artifact_budget_bytes=2**30,
        peak_sampled_prior_group_rss_bytes=max(v['peak_sampled_group_rss_bytes'] for v in stages.values()),
        free_bytes=shutil.disk_usage(task).free,min_free_bytes=8*2**30,
        new_network_executions=4,network_reruns=0,builds=0,dependencies_modified=False,downloads=0,gpu_used=False,
        failures_retained=['cpu-regression'])
    save(out/'resources.json',resources)
    parser=Links();parser.feed((out/'report.html').read_text());links=[]
    def check(file,value):
        uri=urlsplit(value)
        if uri.scheme or uri.netloc or not uri.path:return
        path=file.parent/unquote(uri.path);assert path.exists(),str(file)+': missing '+value
        links.append(dict(file=str(file.relative_to(ROOT)),target=value))
    for target in parser.targets:check(out/'report.html',target)
    for file in (out/'README.md',Path(__file__).with_name('README.md'),ROOT/'docs/tasks/current.md'):
        text=file.read_text()
        if file.name=='current.md':text=text.split('## 2026-10-30 在线检查点')[0]
        for target in re.findall(r'\]\(([^)]+)\)',text):check(file,target)
    assert parser.scripts==0
    for path in report.rglob('*'):
        if path.is_file():assert sha(path)==sha(out/path.relative_to(report))
    final=dict(passed=True,new_networks_exactly_once=4,inherited_networks_unchanged=2,
        platform_cpu_tests_unique=77,prefix_corruptions_rejected=16,healthy_gates_independently_traced=16,
        all_sources_matrices_computes_dependencies_gates_packets_queues_source_confirmations_timers_and_kv_checked=True,
        healthy_hops_each_comparison=4264,healthy_hops_unmatched=0,links_checked=len(links),links=links,
        csv_files=len(list((out/'metrics').glob('*.csv'))),plots=2,images_visually_inspected=2,
        browser_rendering_checked=False,browser_executables={n:shutil.which(n) for n in ('chromium','chromium-browser','google-chrome','firefox','playwright')},
        new_algorithm_benefit_proven=False,overall_goal_complete=False,
        artifact_sha256={str(path.relative_to(out)):sha(path) for path in sorted(out.rglob('*')) if path.is_file() and path.name!='final-audit.json'})
    save(out/'final-audit.json',final)
    save(ROOT/'fixtures/ack_guard_whole/post-network.json',dict(passed=True,report=str(out.relative_to(ROOT)),
        final_audit_sha256=sha(out/'final-audit.json'),receipt_sha256=sha(out/'receipt.json'),
        new_network_executions=4,network_reruns=0,new_algorithm_benefit_proven=False))
    print(json.dumps(dict(passed=True,new_networks=4,links=len(links),artifact_bytes=used,wall_upper_bound_seconds=measured+nested+120)))

if __name__=='__main__':main()
