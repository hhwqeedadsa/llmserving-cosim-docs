"""Read-only final source/archive/report checks for the fixed underfill study."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

from check_feedback_parameter_delivery import ReportParser
from moe_ub_cosim.host_probe import disk_usage

ROOT=Path(__file__).resolve().parents[2]


def load(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('task','report','output'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();task=args.task.resolve();report=args.report.resolve();assert not args.output.exists()
    files=sorted((ROOT/'experiments/paper_readiness').glob('*.py'))
    for path in files:ast.parse(path.read_text(),filename=str(path))
    subprocess.run(['git','diff','--check'],cwd=ROOT,check=True)
    deps=load(task/'deps-monitor/probe.json');assert deps['status']=='completed' and deps['child_exit_code']==0
    assert 'PASS: checked 13 dependency checkouts' in (task/'deps-monitor/stdout.log').read_text()
    entries=load(task/'checkpoint/members.json');receipt=load(report/'archive.json');archive=Path(receipt['path'])
    assert sha(archive)==receipt['sha256'] and archive.stat().st_size<=48*2**20
    assert sum(e['bytes'] for e in entries.values())<=96*2**20
    sources=0
    for name,entry in entries.items():
        path=Path(entry['source']);assert path.stat().st_size==entry['bytes'] and sha(path)==entry['sha256'],name
        sources+=name.startswith('source/')
    seen=set()
    with tarfile.open(archive,'r:gz') as tar:
        for member in tar:
            if member.name=='members.json':assert json.load(tar.extractfile(member))==entries;continue
            assert member.name not in seen;data=tar.extractfile(member).read();entry=entries[member.name]
            assert len(data)==entry['bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256'];seen.add(member.name)
    assert seen==entries.keys()
    correction=load(report/'direction-correction.json')
    for field in ('original_capacity','corrected_capacity'):
        assert sha(Path(correction[field]['path']))==correction[field]['sha256']
    assert correction['network_inputs_or_results_changed'] is False and correction['network_rerun'] is False
    for item in correction['observed_files_byte_identical']:
        assert sha(task/'input/observed'/item['file'])==sha(task/'input-corrected/observed'/item['file'])==item['sha256']
    verification=load(report/'verification.json');summary=load(report/'summary.json');facts=load(report/'findings.json')
    assert verification['verified'] and verification['all_declared_completed'] and verification['maximum_sender_peer_work']==53
    assert verification['explanation_streams_checked']==36 and len(summary['cases'])==3
    assert sha(report/'verification.json')==sha(task/'checked-final/verification.json')
    assert sha(report/'summary.json')==sha(task/'analysis/summary.json')
    by_depth={c['depth']:c for c in summary['cases']}
    assert by_depth[1024]['acked_ns']==457910 and by_depth[1024]['finish_ns']==482849
    assert by_depth[1024]['credit_delayed_work']==0 and by_depth[1024]['published_at_zero']==180
    assert by_depth[1024]['acked_ns']<by_depth[1]['acked_ns']<by_depth[8]['acked_ns']
    assert not facts['shallow_credit_is_best'] and not facts['paper_main_contribution_established']
    for path in report.glob('*.json'):load(path)
    parsed=ReportParser();parsed.feed((report/'report.html').read_text());parsed.close()
    assert parsed.tables==3 and parsed.rows==15 and not parsed.stack
    for link in parsed.links:
        if link.startswith('https://'):continue
        assert (report/link).is_file() or (report/link).resolve()==args.output.resolve(),link
    monitors=[]
    for path in sorted(task.glob('*-monitor/probe.json')):
        if path.parent.name=='final-monitor':continue
        row=load(path);assert row['status']=='completed' and row['child_exit_code']==0 and not row['remaining_live_group_pids']
        monitors.append(dict(stage=path.parent.name,wall_seconds=row['wall_seconds']))
    seconds=sum(r['wall_seconds'] for r in monitors);assert seconds<240
    usage=dict(apparent_bytes=0,allocated_bytes=0);inodes=set()
    for root in (task,report):
        for key,value in disk_usage(root,inodes).items():usage[key]+=value
    assert max(usage.values())<128*2**20
    result=dict(verified=True,archive_sha256=receipt['sha256'],verified_members=len(seen),
        sources_match_snapshot=sources,syntax_files=len(files),fixed_dependencies=13,
        direction_correction_verified=True,network_inputs_unchanged_by_correction=True,
        new_network_runs=1,reused_successful_runs=2,additional_delivery_network_runs=0,
        html_tables=3,html_rows=15,monitored_seconds_before_final=seconds,final_stage_hard_wall_seconds=60,
        stage_monitors=monitors,usage_before_final_receipt=usage,raw_volatile=True,persistent_backup=False,
        paper_main_contribution_established=False)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(verified=True,members=len(seen),sources=sources,seconds_before_final=seconds)))


if __name__=='__main__':main()
