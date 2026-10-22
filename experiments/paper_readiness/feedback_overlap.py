"""Fixed 32-token-capped real-route study: check feedback overlap, not optimize timers."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from prefill_pressure import project

ROOT=Path(__file__).resolve().parents[2]


def load(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(a):
    out=a.output.resolve();assert not out.exists()
    manifest=load(a.manifest)
    assert manifest['source']['dataset_revision']=='27febb7b2d24169560a9f6f83d38eb53f94916cf'
    assert len(manifest['files'])==4
    raw=[]
    for entry in manifest['files']:
        path=ROOT/entry['file']['path']
        assert path.stat().st_size<4*2**20 and sha(path)==entry['file']['sha256']
        raw.append(load(path))
    lengths=[len(r[0]['4']) for r in raw];assert lengths==[105,28,10,6]
    out.mkdir();observed=out/'observed';truth=out/'truth';observed.mkdir();truth.mkdir()
    shutil.copyfile(a.manifest,truth/'manifest.json')
    work=[];refs=[];counts=[]
    for src,r in enumerate(raw):
        choices=r[0]['4'][:32];counts.append(len(choices))
        for token,chosen in enumerate(choices):
            assert len(chosen)==len(set(chosen))==8 and all(type(e) is int and 0<=e<256 for e in chosen)
            for dst in sorted({e//64 for e in chosen}-{src}):
                wid=len(work)
                work.append(dict(id=wid,stream=src*4+dst,bytes=14464,after=[]))
                refs.append(dict(id=wid,source_rank=src,token=token,destination_rank=dst))
    assert counts==[32,28,10,6]
    save(truth/'provenance.json',dict(layer=4,entry=0,cap=32,original_lengths=lengths,
        source_tokens=counts,work=refs,expert_placement='expert_id//64',
        independent_heldout=False,observed_synchronization=False))
    topology=load(ROOT/'examples/scaleout_rdma/small-clos-100g.json')
    topology['deployment']['gpu_type']='NONE'
    for tier in ('tor_agg','agg_core','tor_core'):topology['links'][tier]['rate_bps']=25000000000
    save(observed/'topology.json',topology)
    protocol=load(ROOT/'examples/scaleout_rdma/rdma.json')
    protocol.update(schema_version='moe-scaleout-rdma/2',queue_metrics=True,
        packet_capture=dict(nodes=[0,2,4,6],start_ns=0,end_ns=5000000,max_events=100000),
        limits=dict(max_gpus=32,max_switches=16,max_links=256,max_messages=8192,
            max_queue_events=200000,queue_capture='summary',max_sim_ns=5000000,
            wall_seconds=60,rss_bytes=2**30,address_bytes=2**31,
            artifact_bytes=64*2**20,result_json_bytes=16*2**20))
    protocol['ecn_by_rate']['25000000000']=deepcopy(protocol['ecn_by_rate']['100000000000'])
    save(observed/'protocol.json',protocol)
    cases=[]
    for depth in (1,8):
        for recovery in (55000,110000):
            name=f'd{depth}-r{recovery}'
            streams=[dict(id=s*4+d,src=s*2,dst=d*2,plane=0,wire_port=21000+s*4+d,
                source_nic=1,destination_nic=1,queue_depth=depth)
                for s in range(4) for d in range(4) if s!=d]
            config=dict(mode='figure7_reference/1',np_interval_ns=50000,alpha_interval_ns=55000,
                recovery_interval_ns=recovery,recovery_bytes=10000000,min_rate_bps=100000000,
                additive_bps=5000000,hyper_bps=50000000,fast_recovery_rounds=5,gain=1/256,
                cnp_model_bytes=48,max_events=20000)
            save(observed/(name+'.json'),dict(schema_version='moe-rdma-stream-work/2',
                limits=dict(max_streams=12,max_work=228),streams=streams,work=work,
                dcqcn_reference=config,source_rate_capture=dict(start_ns=0,end_ns=5000000,max_events=20000)))
            cases.append(dict(name=name,depth=depth,recovery_ns=recovery))
    save(observed/'index.json',dict(schema_version='moe-feedback-overlap/1',source_token_counts=counts,
        original_token_counts=lengths,work_count=len(work),payload_bytes=14464*len(work),cases=cases,
        artifacts={p.name:dict(sha256=sha(p),bytes=p.stat().st_size) for p in sorted(observed.iterdir())},
        hardware_calibrated=False,independent_heldout=False,complete_inference_modeled=False))
    print(json.dumps(dict(prepared=True,tokens=counts,work_count=len(work),conditions=4)))


def run(a):
    observed,backend,out=(p.resolve() for p in (a.observed,a.backend,a.output))
    assert not out.exists() and backend.is_file()
    index=load(observed/'index.json')
    for name,entry in index['artifacts'].items():assert sha(observed/name)==entry['sha256']
    out.mkdir();commands=[];executed=[];backend_sha=sha(backend)
    def cli(label,tail):
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,tail)]
        record=dict(label=label,command=command,cwd=str(ROOT));commands.append(record);start=time.monotonic()
        try:
            with (out/(label+'.stdout.log')).open('w') as stdout,(out/(label+'.stderr.log')).open('w') as stderr:
                child=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=60)
            record['exit_code']=child.returncode
            return child.returncode
        finally:
            record['wall_seconds']=time.monotonic()-start;save(out/'commands.json',commands)
    code=cli('topology',['topology','--config',observed/'topology.json','--output',out/'topology'])
    assert code==0,'Topology CLI failed: stop'
    failure=None
    for case in index['cases']:
        name=case['name'];assert sha(backend)==backend_sha
        code=cli(name,['rdma-stream-probe','--topology',out/'topology','--protocol',observed/'protocol.json',
            '--config',observed/(name+'.json'),'--backend',backend,'--output',out/name])
        state=dict(name=name,exit_code=code)
        executed.append(state)
        if code:
            failure='public_cli_failed';state['result_available']=(out/name/'result.json').is_file();break
        r=load(out/name/'result.json');t=r['persistent_transport']
        if not r['packet_capture']['complete']:failure='capture_truncated'
        elif r['fault_drops']:failure='fault_drop'
        elif any(s['source_payload_bytes']!=s['submitted_bytes'] for s in t['streams']):failure='retransmission'
        elif any(p['pfc_pause'] or p['pfc_resume'] for p in r['ports']):failure='unsupported_pfc_control'
        if failure:state['unsupported']=failure;break
    save(out/'execution.json',dict(completed=failure is None,conditions_planned=4,executed=executed,
        unexecuted=[c['name'] for c in index['cases'][len(executed):]],stop_reason=failure,
        backend_path=str(backend),backend_sha256=backend_sha,platform_behavior_commit='591c62a',
        base_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        observed_index_sha256=sha(observed/'index.json'),new_network_runs=len(executed),retry=False))
    print(json.dumps(dict(completed=failure is None,network_runs=len(executed),stop_reason=failure)))
    if failure:raise SystemExit(1)


def analyze(a):
    observed,runs,out=a.observed,a.run,a.output;assert not out.exists()
    index=load(observed/'index.json');execution=load(runs/'execution.json')
    physical=load(runs/'topology/topology.json');rows=[];per_stream=[]
    for actual in execution['executed']:
        name=actual['name'];case=next(c for c in index['cases'] if c['name']==name)
        path=runs/name/'result.json'
        if not path.is_file():
            rows.append(dict(**case,status='failed_without_result',exit_code=actual['exit_code']));continue
        r=load(path);t=r['persistent_transport'];description=load(observed/(name+'.json'))
        streams={s['id']:s for s in description['streams']}
        predicted,endpoints,_=project(physical,description,11)
        bound=max(Fraction((v['data']+v['ack'])*8*10**9,endpoints[k][1]) for k,v in predicted.items())
        events=t['dcqcn_reference']['events'];kinds=Counter(e['kind'] for e in events)
        samples=[dict(zip(t['source_rate_capture']['columns'],s)) for s in t['source_rate_capture']['samples']]
        source=[dict(zip(t['source_packet_columns'],s)) for s in t['source_packets']]
        capture=[dict(zip(r['packet_capture']['columns'],p)) for p in r['packet_capture']['events']]
        own_after=own_after_bytes=low_tx=0
        credit_waits=[]
        for sid,s in streams.items():
            tx=[p for p in source if p['stream']==sid]
            notices=[e for e in events if e['stream']==sid and e['kind']=='cnp_received']
            first=min((e['time_ns'] for e in notices),default=None)
            after=[p for p in tx if first is not None and p['time_ns']>=first]
            own_after+=len(after);own_after_bytes+=sum(p['payload_bytes'] for p in after)
            rate=[p for p in samples if p['stream']==sid and p['event']==0]
            low_tx+=sum(p['rate_bps']<100000000000 for p in rate)
            timeline=[(p['sequence'],p['time_ns'],p['payload_bytes']) for p in tx]
            selected=sorted([w for w in t['work'] if w['stream']==sid],key=lambda w:w['ticket'])
            occupancy=[]
            for pos,w in enumerate(selected):
                if w['published_ns']<0:continue
                owner=selected[pos-case['depth']] if pos>=case['depth'] else None
                if owner:
                    assert owner['acked_ns']>=0
                    assert w['published_ns']==max(w['ready_ns'],owner['acked_ns'])
                    credit_waits.append(dict(case=name,work=w['id'],owner_work=owner['id'],
                        ready_ns=w['ready_ns'],owner_ack_ns=owner['acked_ns'],published_ns=w['published_ns']))
                elif w['published_ns']!=w['ready_ns']:raise AssertionError('Unexplained initial credit wait')
                occupancy.append(w['published_ns']-w['ready_ns'])
            per_stream.append(dict(case=name,stream=sid,src=s['src'],dst=s['dst'],
                first_cnp_rx_ns=first,last_source_tx_ns=max((p['time_ns'] for p in tx),default=None),
                source_packets=len(tx),packets_after_first_cnp=len(after),payload_after_first_cnp=sum(p['payload_bytes'] for p in after),
                minimum_rate_at_tx_bps=min((p['rate_bps'] for p in rate),default=None),
                tx_samples_below_line_rate=sum(p['rate_bps']<100000000000 for p in rate),
                source_timeline_sha256=hashlib.sha256(json.dumps(timeline).encode()).hexdigest(),
                max_credit_wait_ns=max(occupancy,default=0)))
        waits=[w['published_ns']-w['ready_ns'] for w in t['work'] if w['published_ns']>=0]
        completed=r['passed'] and r['drained']
        prefix_acked=[w['acked_ns'] for w in t['work'] if w['acked_ns']>=0]
        row=dict(**case,status='complete' if completed else 'censored',exit_code=actual['exit_code'],
            result_sha256=sha(path),declared_work=len(description['work']),acked_work=len(prefix_acked),
            published_work=sum(w['published_ns']>=0 for w in t['work']),
            accepted_work=sum(w['accepted_ns']>=0 for w in t['work']),
            final_acked_ns=max(prefix_acked) if completed else None,
            last_observed_work_ack_ns=max(prefix_acked,default=None),
            observation_end_ns=r['finish_ns'],physical_finish_ns=r['finish_ns'] if completed else None,
            minimum_declared_port_service_ns=float(bound),source_packets=len(source),
            payload_sent=sum(p['payload_bytes'] for p in source),packets_after_first_cnp=own_after,
            payload_after_first_cnp=own_after_bytes,tx_samples_below_line_rate=low_tx,
            minimum_rate_at_actual_tx_bps=min(p['rate_bps'] for p in samples if p['event']==0),
            unique_destination_ce=sum(p['ip_protocol']==17 and p['event']==1 and p['ecn']==3 for p in capture),
            cnp_count=kinds['cnp_generated'],reference_events=dict(kinds),
            peak_switch_queue_bytes=max(p.get('queue_metrics',{}).get('peak_bytes',0) for p in r['ports']),
            switch_queue_area_byte_ns=sum(p.get('queue_metrics',{}).get('area_byte_ns',0) for p in r['ports']),
            waiting_published_work=sum(v>0 for v in waits),max_credit_wait_ns=max(waits,default=0),
            overlapping_credit_wait_sum_ns=sum(waits),credit_owner_checks=len(credit_waits))
        if completed:
            assert float(bound)<=row['final_acked_ns']
            for p in r['ports']:
                v=predicted[p['node'],p['port']]
                assert p['data_tx_bytes']==v['data'] and p['control_tx_bytes']>=v['ack']
        row['credit_owners']=credit_waits
        rows.append(row)
    comparisons=[]
    for depth in (1,8):
        selected=[r for r in rows if r['depth']==depth and r['status']=='complete']
        if len(selected)==2:
            selected.sort(key=lambda r:r['recovery_ns']);x,y=selected
            comparisons.append(dict(depth=depth,low_recovery_ns=x['recovery_ns'],high_recovery_ns=y['recovery_ns'],
                ack_delta_ns=y['final_acked_ns']-x['final_acked_ns'],
                source_timeline_equal=all(
                    next(s for s in per_stream if s['case']==x['name'] and s['stream']==sid)['source_timeline_sha256']==
                    next(s for s in per_stream if s['case']==y['name'] and s['stream']==sid)['source_timeline_sha256'] for sid in streams)))
    out.mkdir()
    save(out/'summary.json',dict(cases=rows,unexecuted=execution['unexecuted'],comparisons=comparisons,
        analysis_reads_truth=False,hardware_calibrated=False,source_tokens=index['source_token_counts'],
        declared_work=index['work_count'],declared_payload=index['payload_bytes'],
        censored_completion_is_unknown=True,full_inference=False,overlapping_credit_wait_is_not_total_delay=True))
    save(out/'streams.json',per_stream)
    print(json.dumps(dict(analyzed=True,cases=[dict(name=r['name'],status=r['status']) for r in rows])))


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='stage',required=True)
    q=sub.add_parser('prepare')
    for key in ('manifest','output'):q.add_argument('--'+key,type=Path,required=True)
    q=sub.add_parser('run')
    for key in ('observed','backend','output'):q.add_argument('--'+key,type=Path,required=True)
    q=sub.add_parser('analyze')
    for key in ('observed','run','output'):q.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();globals()[a.stage](a)


if __name__=='__main__':main()
