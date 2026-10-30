"""Freeze normal-only message deadlines, unchanged sources and four networks."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x') as stream:json.dump(v,stream,ensure_ascii=False,indent=2);stream.write('\n')
def files(p):return {str(f.relative_to(p)):sha(f) for f in sorted(p.rglob('*')) if f.is_file()}

def normalized(c):
    c=deepcopy(c);c['route_replay']['manifest']=None;o=c['whole_call_replication']
    o['schema_version']=None;o['max_wakeup_events']=None
    o.pop('max_guard_events',None);o.pop('max_guard_messages',None)
    for call in o['calls']:
        if call['id']=='target':call['hedge_after_ns']=None
        call.pop('source_ack_timeout_ns',None)
    return c

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--previous-study',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();previous=a.previous_study.resolve();old=load(previous/'frozen.json')
    normal=Path(old['cases']['control-normal']['output'])
    assert load(normal/'verification.json')['passed']
    r=load(normal/'whole_call_report.json');primary=r['calls']['target']['attempts']['primary']
    graphs={w['graph_id'] for w in primary['waves']};messages={}
    for line in (normal/'execution_events.jsonl').read_text().splitlines():
        e=json.loads(line)
        if e.get('graph_id') not in graphs or e['kind'] not in ('message_send_post','message_send_complete'):continue
        key=e['message'];name='post_ns' if e['kind']=='message_send_post' else 'ack_ns'
        row=messages.setdefault(key,{});assert name not in row;row[name]=e['time_ns']
    assert messages and all(set(m)=={'post_ns','ack_ns'} and m['ack_ns']>=m['post_ns'] for m in messages.values())
    longest=max(m['ack_ns']-m['post_ns'] for m in messages.values());timeout=((longest+999)//1000)*1000
    first=min(m['post_ns'] for m in messages.values());fixed=first+timeout
    fallback=((r['calls']['target']['result_ready_ns']+999)//1000)*1000
    assert 0<fixed<fallback==127000
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    calibration=dict(rule='ceil_all_normal_target_post_ack_max_to_1000ns/1',normal_messages=len(messages),
        normal_max_post_ack_ns=longest,source_ack_timeout_ns=timeout,first_normal_post_ns=first,
        fixed_early_deadline_ns=fixed,full_fallback_ns=fallback,normal_samples=1,
        normal_only=True,result_tuned=False,independent_holdout=False,
        messages={str(k):v for k,v in sorted(messages.items())},
        source=str(normal/'execution_events.jsonl'),source_sha256=sha(normal/'execution_events.jsonl'),
        full_result_sha256=sha(normal/'whole_call_report.json'))
    save(out/'calibration.json',calibration)
    source=previous/'source-manifest.json'
    for entry in load(source)['files']:
        path=(ROOT/entry['file']['path']).resolve();assert sha(path)==entry['file']['sha256']
    backend=old['backend'];assert sha(Path(backend['path']))==backend['sha256']
    for path,digest in old['loaded_ns3_libraries'].items():assert sha(Path(path))==digest
    prepared=out/'input'
    subprocess.run([sys.executable,'-B',str(ROOT/'examples/scaleout_multicall/prepare.py'),
        '--source-manifest',str(source),'--target-files','4','--decode-steps','2',
        '--target-deadline-ns',str(fallback),'--target-source-ack-timeout-ns',str(timeout),
        '--healthy-deadline-ns','4000000','--output',str(prepared)],cwd=ROOT,check=True)
    guarded=load(prepared/'service-multicall.json');early=deepcopy(guarded);o=early['whole_call_replication']
    o['schema_version']='moe-whole-call-replication/4';o.pop('max_guard_events');o.pop('max_guard_messages')
    for call in o['calls']:
        call.pop('source_ack_timeout_ns')
        if call['id']=='target':call['hedge_after_ns']=fixed
    save(prepared/'service-fixed.json',early)
    baseline=load(normal/'input.json')
    assert normalized(baseline)==normalized(early)==normalized(guarded)
    for name in ('rdma-multicall-normal.json','rdma-multicall-local.json','topology/topology.json'):
        assert sha(prepared/name)==sha(Path(old['prepared'])/name)
    cases={}
    for policy in ('fixed','guarded'):
        for condition in ('normal','local'):
            name=policy+'-'+condition
            cases[name]=dict(output=str(out/name),config=str(prepared/('service-fixed.json' if policy=='fixed' else 'service-multicall.json')),
                protocol=str(prepared/('rdma-multicall-'+condition+'.json')),topology=str(prepared/'topology'))
    inherited={name:dict(output=old['cases'][name]['output'],sha256=files(Path(old['cases'][name]['output'])))
        for name in ('control-normal','control-local')}
    frozen=dict(passed=True,network_executed=False,new_networks_planned=4,calibration=calibration,
        platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        backend=backend,loaded_ns3_libraries=old['loaded_ns3_libraries'],prepared=str(prepared),prepared_sha256=files(prepared),
        previous_study=str(previous),source_manifest_sha256=sha(source),source_lineage=old['source_lineage'],
        inherited_cases=inherited,new_cases=cases,simulated_endpoints=128,ep_sizes=[32]*4,decode_steps=2,
        source_sha256={str(path.relative_to(ROOT)):sha(path) for path in Path(__file__).parent.glob('*.py')},
        method_sha256=sha(Path(__file__).with_name('protocol.md')),
        ancestor_inspector_sha256=sha(ROOT/'experiments/sequential_externality/analyze.py'))
    save(out/'frozen.json',frozen)
    print(json.dumps(dict(passed=True,normal_messages=len(messages),timeout_ns=timeout,fixed_ns=fixed,fallback_ns=fallback,new_networks_planned=4)))

if __name__=='__main__':main()
