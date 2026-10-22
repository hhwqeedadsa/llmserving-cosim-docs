"""Fixed AI/ECN sensitivity study. No tuning, platform modifications or test inputs."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from fractions import Fraction
import hashlib
from itertools import product
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from prefill_pressure import project
from check_feedback_factorial import check_rp, notification_times, route

ROOT=Path(__file__).resolve().parents[2]


def load(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def prepare(a):
    out=a.output.resolve();base=a.baseline.resolve();assert not out.exists()
    manifest=load(a.manifest);assert manifest==load(base/'input/truth/manifest.json')
    assert manifest['source']['dataset_revision']=='27febb7b2d24169560a9f6f83d38eb53f94916cf'
    expected=[];counts=[]
    for src,item in enumerate(manifest['files']):
        path=ROOT/item['file']['path'];assert sha(path)==item['file']['sha256'] and path.stat().st_size<4*2**20
        request=load(path)[0]['4'][:32];counts.append(len(request))
        for choices in request:
            for dst in sorted({expert//64 for expert in choices}-{src}):
                expected.append(dict(id=len(expected),stream=src*4+dst,bytes=14464,after=[]))
    assert counts==[32,28,10,6] and len(expected)==180
    out.mkdir();observed=out/'observed';truth=out/'truth';observed.mkdir();truth.mkdir()
    shutil.copyfile(a.manifest,truth/'manifest.json')
    shutil.copyfile(base/'input/truth/provenance.json',truth/'provenance.json')
    shutil.copyfile(base/'input/observed/topology.json',observed/'topology.json')
    for pmax,label in ((1.0,'p1'),(0.01,'p001')):
        protocol=load(base/'input/observed/protocol.json')
        for value in protocol['ecn_by_rate'].values():value['pmax']=pmax
        save(observed/('protocol-'+label+'.json'),protocol)
    cases=[]
    for depth,ai,(pmax,label) in product((1,8),(5000000,40000000),((1.0,'p1'),(0.01,'p001'))):
        prior=f'd{depth}-r55000';name=f'd{depth}-ai{ai//1000000}-{label}'
        work=load(base/'input/observed'/(prior+'.json'))
        assert work['work']==expected
        work['dcqcn_reference']['additive_bps']=ai
        save(observed/(name+'.json'),work)
        cases.append(dict(name=name,depth=depth,additive_bps=ai,pmax=pmax,
            protocol='protocol-'+label+'.json',baseline_case=prior if ai==5000000 and pmax==1 else None))
    save(observed/'index.json',dict(schema_version='moe-feedback-parameters/1',cases=cases,
        source_token_counts=counts,work_count=180,payload_bytes=2603520,
        artifacts={p.name:dict(sha256=sha(p),bytes=p.stat().st_size) for p in sorted(observed.iterdir())},
        hardware_calibrated=False,independent_heldout=False))
    print(json.dumps(dict(prepared=True,conditions=8,baseline_reuse_candidates=2)))


def run(a):
    observed,backend,out=(p.resolve() for p in (a.observed,a.backend,a.output));assert not out.exists()
    index=load(observed/'index.json');out.mkdir();commands=[];executed=[];binary_sha=sha(backend)
    for name,entry in index['artifacts'].items():assert sha(observed/name)==entry['sha256']
    def cli(label,tail):
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,tail)]
        entry=dict(label=label,command=command,cwd=str(ROOT));commands.append(entry);start=time.monotonic()
        try:
            with (out/(label+'.stdout.log')).open('w') as stdout,(out/(label+'.stderr.log')).open('w') as stderr:
                child=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=60)
            entry['exit_code']=child.returncode;return child.returncode
        finally:entry['wall_seconds']=time.monotonic()-start;save(out/'commands.json',commands)
    assert cli('topology',['topology','--config',observed/'topology.json','--output',out/'topology'])==0
    failure=None
    for case in index['cases']:
        name=case['name'];assert sha(backend)==binary_sha
        entry=dict(name=name,kind='new');executed.append(entry)
        if a.reuse_baseline and case['baseline_case']:
            base=a.reuse_baseline.resolve();oldname=case['baseline_case'];old=base/'run'/oldname
            assert load(observed/(name+'.json'))==load(base/'input/observed'/(oldname+'.json'))
            assert load(observed/case['protocol'])==load(base/'input/observed/protocol.json')
            assert sha(out/'topology/topology.json')==sha(base/'run/topology/topology.json')
            inputs=load(old/'inputs.json');command=inputs['command']
            paths=(backend,base/'run/topology/topology.json',base/'input/observed/protocol.json',base/'input/observed'/(oldname+'.json'))
            assert all(sha(p)==inputs['hashes'][command[i]] for i,p in enumerate(paths))
            previous=next(c for c in load(base/'analysis/summary.json')['cases'] if c['name']==oldname)
            assert sha(old/'result.json')==previous['result_sha256']
            r=load(old/'result.json');assert r['passed'] and r['drained'] and r['completed_work']==180
            target=out/name;target.mkdir()
            shutil.copyfile(old/'result.json',target/'result.json');shutil.copyfile(old/'inputs.json',target/'prior-inputs.json')
            entry.update(kind='reused_success',prior_case=oldname,result_sha256=sha(old/'result.json'),
                prior_inputs_sha256=sha(old/'inputs.json'),exit_code=0)
            save(target/'reuse.json',entry)
        else:
            entry['exit_code']=cli(name,['rdma-stream-probe','--topology',out/'topology','--protocol',observed/case['protocol'],
                '--config',observed/(name+'.json'),'--backend',backend,'--output',out/name])
            if entry['exit_code']:
                failure='public_cli_failed';break
            r=load(out/name/'result.json')
        if not r['packet_capture']['complete']:failure='capture_truncated'
        elif r['fault_drops']:failure='fault_drop'
        elif any(s['source_payload_bytes']!=s['submitted_bytes'] for s in r['persistent_transport']['streams']):failure='retransmission'
        elif any(p['pfc_pause'] or p['pfc_resume'] for p in r['ports']):failure='unsupported_pfc'
        if failure:break
    save(out/'execution.json',dict(completed=failure is None,executed=executed,stop_reason=failure,
        unexecuted=[c['name'] for c in index['cases'][len(executed):]],backend_path=str(backend),backend_sha256=binary_sha,
        new_network_runs=sum(e['kind']=='new' for e in executed),reused_successful_runs=sum(e['kind']=='reused_success' for e in executed),
        base_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),retry=False))
    print(json.dumps(dict(completed=failure is None,stop_reason=failure)))
    if failure:raise SystemExit(1)


def analyze(a):
    observed,runs,out=a.observed,a.run,a.output;assert not out.exists()
    index=load(observed/'index.json');execution=load(runs/'execution.json');physical=load(runs/'topology/topology.json');rows=[]
    for item in execution['executed']:
        name=item['name'];case=next(c for c in index['cases'] if c['name']==name)
        path=runs/name/'result.json'
        if not path.is_file():rows.append(dict(**case,status='failed_no_result'));continue
        r=load(path);t=r['persistent_transport'];w=load(observed/(name+'.json'));events=t['dcqcn_reference']['events']
        source=[dict(zip(t['source_packet_columns'],p)) for p in t['source_packets']]
        samples=[dict(zip(t['source_rate_capture']['columns'],p)) for p in t['source_rate_capture']['samples']]
        capture=[dict(zip(r['packet_capture']['columns'],p)) for p in r['packet_capture']['events']]
        predicted,endpoints,_=project(physical,w,11)
        bound=max(Fraction((v['data']+v['ack'])*8*10**9,endpoints[k][1]) for k,v in predicted.items())
        after=after_bytes=0
        for stream in w['streams']:
            sid=stream['id'];first=min((e['time_ns'] for e in events if e['kind']=='cnp_received' and e['stream']==sid),default=None)
            selected=[p for p in source if p['stream']==sid and first is not None and p['time_ns']>=first]
            after+=len(selected);after_bytes+=sum(p['payload_bytes'] for p in selected)
        complete=r['passed'] and r['drained'];acked=[w['acked_ns'] for w in t['work'] if w['acked_ns']>=0]
        kinds=Counter(e['kind'] for e in events)
        rows.append(dict(**case,kind=item['kind'],status='complete' if complete else 'censored',
            result_sha256=sha(path),acked_ns=max(acked) if complete else None,
            finish_ns=r['finish_ns'] if complete else None,observation_end_ns=r['finish_ns'],acked_work=len(acked),
            minimum_port_service_ns=float(bound),source_packets=len(source),post_feedback_packets=after,post_feedback_payload_bytes=after_bytes,
            tx_samples_below_line_rate=sum(p['rate_bps']<100000000000 for p in samples if p['event']==0),
            minimum_rate_at_tx_bps=min(p['rate_bps'] for p in samples if p['event']==0),
            unique_destination_ce=sum(p['event']==1 and p['ip_protocol']==17 and p['ecn']==3 for p in capture),
            cnp_count=kinds['cnp_generated'],reference_events=dict(kinds),
            peak_switch_queue_bytes=max(p.get('queue_metrics',{}).get('peak_bytes',0) for p in r['ports']),
            queue_area_byte_ns=sum(p.get('queue_metrics',{}).get('area_byte_ns',0) for p in r['ports']),
            source_timeline_sha256=hashlib.sha256(json.dumps(sorted((p['stream'],p['sequence'],p['time_ns'],p['payload_bytes']) for p in source)).encode()).hexdigest()))
    effects=[]
    for factor in ('depth','additive_bps','pmax'):
        levels={'depth':(1,8),'additive_bps':(5000000,40000000),'pmax':(0.01,1.0)}[factor]
        for low in [r for r in rows if r[factor]==levels[0] and r['status']=='complete']:
            high=next((r for r in rows if r[factor]==levels[1] and r['status']=='complete' and all(r[k]==low[k] for k in ('depth','additive_bps','pmax') if k!=factor)),None)
            if high:effects.append(dict(factor=factor,low_case=low['name'],high_case=high['name'],
                ack_delta_ns=high['acked_ns']-low['acked_ns'],cnp_delta=high['cnp_count']-low['cnp_count'],
                source_timeline_equal=low['source_timeline_sha256']==high['source_timeline_sha256']))
    out.mkdir();save(out/'summary.json',dict(cases=rows,effects=effects,unexecuted=execution['unexecuted'],
        analysis_reads_truth=False,hardware_calibrated=False,single_rng_seed=11,
        source='Known inference route window; not full LLMServingSim, training, independent holdout or production tuning',
        new_network_runs=execution['new_network_runs'],reused_successful_runs=execution['reused_successful_runs']))
    print(json.dumps(dict(analyzed=True,conditions=len(rows),comparisons=len(effects))))


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='stage',required=True)
    p=sub.add_parser('prepare')
    for k in ('baseline','manifest','output'):p.add_argument('--'+k,type=Path,required=True)
    p=sub.add_parser('run')
    for k in ('observed','backend','output'):p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--reuse-baseline',type=Path)
    p=sub.add_parser('analyze')
    for k in ('observed','run','output'):p.add_argument('--'+k,type=Path,required=True)
    a=parser.parse_args();globals()[a.stage](a)


if __name__=='__main__':main()
