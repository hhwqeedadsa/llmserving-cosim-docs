"""Public 8-GPU workflow: old/current/timed service and independent observer checks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

from moe_ub_cosim.host_probe import probe, disk_usage
from moe_ub_cosim.host_timing import read_trace

ROOT=Path(__file__).resolve().parents[2]
def read(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--backend',type=Path,required=True)
    parser.add_argument('--protoc',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(exist_ok=False)
    start=time.monotonic();example=ROOT/'examples/scaleout_rdma'
    (out/'cwd').mkdir();os.chdir(out/'cwd');commands=[]
    def cli(name,argv):
        assert time.monotonic()-start<300
        assert max(disk_usage(out).values())<128*1024**2
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,argv)]
        report=probe(command,out/(name+'-monitor'),rss_bytes=1024**3,address_bytes=2*1024**3,
            artifact_bytes=32*1024**2,wall_seconds=60,artifact_roots=[out/name] if name!='topology' else [out/'topology'],
            min_free_bytes=8*1024**3)
        commands.append(report);save(out/'commands.json',commands)
        assert report['status']=='completed',name
    cli('topology',['topology','--config',example/'small-clos-100g.json','--output',out/'topology'])
    for name,backend,timed in [('reference',args.reference,False),('current',args.backend,False),('timed',args.backend,True)]:
        cli(name,['rdma-run','--config',example/'service.json','--topology',out/'topology',
            '--protocol',example/'rdma.json','--backend',backend.resolve(),'--protoc',args.protoc,
            '--output',out/name,*(['--host-timing'] if timed else [])])
    runs=[out/name for name in ('reference','current','timed')]
    for path in ('result.json','log/astra_nodes.log'):
        assert len({sha(run/path) for run in runs})==1,path
    def normalize(run,path):return json.dumps(read(run/path),sort_keys=True).replace(str(run),'RUN')
    assert len({normalize(run,'frontend.json') for run in runs})==1
    events=[]
    for run in runs:
        events.append([json.loads(line[len('MOE_RDMA_EVENT '):]) for line in (run/'native.stdout.log').read_text().splitlines()
            if line.startswith('MOE_RDMA_EVENT ')])
    assert events[0]==events[1]==events[2]
    for run in runs[:2]:assert not list(run.glob('*_host.jsonl'))
    result=read(out/'timed/result.json');count=len(result['messages'])
    traces={name:read_trace(out/f'timed/{name}_host.jsonl') for name in ('worker','native')}
    for trace in traces.values():assert trace['trace_complete'] and not trace['open_spans']
    native=traces['native'];worker=traces['worker']
    # Independent stack reconstruction and duration conservation from raw records.
    for name,trace in traces.items():
        stack=[];durations={};child_sums={};completed=0
        for row in map(json.loads,(out/f'timed/{name}_host.jsonl').read_text().splitlines()):
            if row['type']=='begin':
                assert row['parent']==(stack[-1]['id'] if stack else 0)
                stack.append(row);child_sums[row['id']]=[0,0]
            elif row['type']=='end':
                before=stack.pop();assert before['id']==row['id'];completed+=1
                delta=[row[key]-before[key] for key in ('wall_ns','cpu_ns')]
                durations[row['id']]=(*delta,*(a-b for a,b in zip(delta,child_sums[row['id']])))
                if stack:
                    child_sums[stack[-1]['id']]=[a+b for a,b in zip(child_sums[stack[-1]['id']],delta)]
        assert not stack and completed==len(trace['completed_spans'])
        for span in trace['completed_spans']:
            assert durations[span['id']]==tuple(span[key] for key in
                ('inclusive_wall_ns','inclusive_cpu_ns','exclusive_wall_ns','exclusive_cpu_ns'))
        root=next(s for s in trace['completed_spans'] if s['parent']==0)
        for clock in ('wall','cpu'):
            assert sum(s[f'exclusive_{clock}_ns'] for s in trace['completed_spans'])==root[f'inclusive_{clock}_ns']
    final=next(s['values'] for s in native['snapshots'] if s['name']=='message_resources_final')
    assert final==dict(registered_messages=count,send_posts=count,recv_posts=count,send_callbacks=count,
        recv_callbacks=count,outstanding_send_callbacks=0,outstanding_recv_callbacks=0)
    assert len(result['service']['message_callbacks'])==count
    exchanges=sorted((s for s in native['completed_spans'] if s['name']=='control_exchange'),key=lambda s:s['id'])
    decisions=sorted((s for s in worker['completed_spans'] if s['name']=='frontend_decide'),key=lambda s:s['id'])
    assert len(exchanges)==len(decisions)==len(events[2])
    for exchange,decision in zip(exchanges,decisions):
        assert exchange['begin_wall_ns']<=decision['begin_wall_ns']<=decision['end_wall_ns']<=exchange['end_wall_ns']
    assert next(s['values']['processed_events'] for s in native['snapshots'] if s['name']=='ns3_events')>0
    cli('summary',['host-timing-check','--run',out/'timed','--output',out/'summary.json'])
    assert read(out/'summary.json')['all_traces_complete']
    # Simulate a saved timeout prefix by copying only genuine flushed records.
    (out/'partial').mkdir();lines=(out/'timed/native_host.jsonl').read_bytes().splitlines(keepends=True)
    cutoff=next(i for i,line in enumerate(lines) if json.loads(line).get('name')=='simulator_run')
    (out/'partial/native_host.jsonl').write_bytes(b''.join(lines[:cutoff+1])+b'{"type":')
    cli('partial-summary',['host-timing-check','--run',out/'partial','--output',out/'partial-summary.json'])
    partial=read(out/'partial-summary.json')
    assert not partial['all_traces_complete'] and partial['missing_writers']==['worker']
    assert 'simulator_run' not in partial['traces']['native']['phases']
    assert [s['name'] for s in partial['traces']['native']['open_spans']]==['native_total','simulator_run']
    assert max(disk_usage(out).values())<128*1024**2 and shutil.disk_usage(out).free>=8*1024**3
    save(out/'verification.json',dict(passed=True,result_byte_identical=True,node_log_byte_identical=True,
        frontend_equal_after_only_run_path_normalization=True,stdout_control_events_equal=True,
        untimed_no_sidecars=True,nested_arithmetic_independently_verified=True,cross_process_exchange_encloses_decision=True,
        final_message_counters=final,partial_prefix_read=True,partial_prefix_is_copied_not_real_timeout=True,
        finish_ns=result['finish_ns'],messages=count,waves=len(result['service']['waves']),
        verified_nodes=read(out/'timed/verification.json')['verified_nodes'],
        source_hashes={str(p.relative_to(ROOT)):sha(p) for p in
            [ROOT/'python/moe_ub_cosim/host_timing.py',ROOT/'python/moe_ub_cosim/scaleout_service.py',
             ROOT/'backend/rdma/host_timing.hh',ROOT/'backend/rdma/service.cc',ROOT/'backend/rdma/astra_adapter.cc']},
        phase_totals={name:trace['phases'] for name,trace in traces.items()},commands=commands,
        wall_seconds=time.monotonic()-start,scope='small functional observer equivalence; no thousand-GPU/hardware or speedup claim'))
    print(json.dumps(dict(passed=True,output=str(out),messages=count,finish_ns=result['finish_ns'])))


if __name__=='__main__':main()
