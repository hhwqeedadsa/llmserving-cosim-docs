"""Freeze read-only inputs and the source-confirmation observation prefixes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def files(p):return {str(f.relative_to(p)):sha(f) for f in sorted(p.rglob('*')) if f.is_file()}
def save(p,v):
    with p.open('x') as stream:json.dump(v,stream,ensure_ascii=False,indent=2);stream.write('\n')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();study=a.study.resolve();old=load(study/'frozen.json');out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    paths={n:Path(old['inherited_cases'][n]['output']) for n in ('control-normal','control-local')}
    paths.update({n:Path(old['new_cases'][n]['output']) for n in ('fixed-local','guarded-normal','guarded-local')})
    report=load(paths['guarded-local']/'whole_call_report.json')
    starts=[d for d in report['source_ack_guard']['decisions'] if d['call']=='target' and d['reason']=='source_timeout']
    assert len(starts)==1;deadline=starts[0]['time_ns'];assert deadline==19030
    prefixes={}
    for condition in ('normal','local'):
        path=paths['guarded-'+condition];raw=[];decide=None
        for line in (path/'native.stdout.log').read_text().splitlines():
            if not line.startswith('MOE_RDMA_EVENT '):continue
            e=json.loads(line[len('MOE_RDMA_EVENT '):]);now=e.get('time_ns',e.get('now_ns'))
            if now>deadline:break
            if e['type']=='DECIDE' and now==deadline:
                decide=e;break
            if e['type']=='OBSERVE':raw.append(e)
        if condition=='local':assert decide is not None
        # Normal may not have a DECIDE at that precise time. Its observations
        # through that time are still a legitimate saved prefix comparison.
        graph=load(path/'graphs/g0.w0/wave.json');ids={m['id'] for m in graph['messages']}
        callbacks=[e for e in raw if e.get('graph_id')=='g0.w0' and e.get('message') in ids and
            e['kind'] in ('message_send_post','message_send_complete')]
        prefix=dict(condition=condition,deadline_ns=deadline,observed_sequence=raw[-1]['sequence'],
            observations=callbacks,graph=graph,decision=decide,scope='source post/completion only; no receiver or later graph truth')
        save(out/('source-prefix-'+condition+'.json'),prefix);prefixes[condition]=dict(path=str(out/('source-prefix-'+condition+'.json')),sha256=sha(out/('source-prefix-'+condition+'.json')))
    source_paths=['python/moe_ub_cosim/scaleout_service.py','python/moe_ub_cosim/whole_call.py',
        'python/moe_ub_cosim/sequential_whole.py','python/moe_ub_cosim/contribution_binding.py',
        'backend/rdma/service.cc','backend/rdma/astra_adapter.cc','docs/inflight_helpers.md']
    frozen=dict(passed=True,new_network_executions=0,platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        previous_frozen_sha256=sha(study/'frozen.json'),deadline_ns=deadline,prefixes=prefixes,
        records={n:dict(path=str(path),sha256=files(path)) for n,path in paths.items()},
        method_sha256=sha(Path(__file__).with_name('protocol.md')),
        analyzer_sources_sha256={str(p.relative_to(ROOT)):sha(p) for p in Path(__file__).parent.glob('*.py')},
        source_sha256={n:sha(ROOT/n) for n in source_paths})
    save(out/'frozen.json',frozen);print(json.dumps(dict(passed=True,source_prefixes=2,read_only_networks=5,network_executed=False)))

if __name__=='__main__':main()
