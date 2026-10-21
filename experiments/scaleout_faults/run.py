"""Fixed offered Decode requests: packet loss, ACK loss, and shared EP fabric.

Uses only public CLI and saved run artifacts, never tests or private drivers.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend-json',type=Path,required=True)
    p.add_argument('--protoc',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(exist_ok=False)
    backend=read(a.backend_json);commands=[]
    def cli(label,argv,success=True):
        if sum(f.stat().st_size for f in out.rglob('*') if f.is_file())>128*1024**2 or shutil.disk_usage(out).free<8*1024**3:
            raise RuntimeError('experiment artifact/free-space budget exceeded; no expansion')
        cmd=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,argv)]
        with (out/(label+'.stdout')).open('w') as stdout,(out/(label+'.stderr')).open('w') as stderr:
            r=subprocess.run(cmd,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=90)
        commands.append(dict(label=label,argv=cmd,cwd=str(ROOT),exit_code=r.returncode,expected_success=success))
        save(out/'commands.json',commands)
        if (r.returncode==0)!=success:raise RuntimeError(label+': unexpected exit; inspect logs')
    config=read(ROOT/'examples/scaleout_rdma/service.json')
    config['parallel']['groups']=[[0,2,4,6],[1,3,5,7]]
    config['model']['layers']=1;config['workload']['decode_steps']=2
    save(out/'service.json',config)
    cli('topology',['topology','--config',ROOT/'examples/scaleout_rdma/small-clos-100g.json','--output',out/'topology'])
    protocol=read(ROOT/'examples/scaleout_rdma/rdma.json')
    protocol['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000)
    summaries={};target=None
    for label,kind in [('baseline',None),('middle-data','data'),('final-ack','ack'),('switch-first','switch')]:
        rdma=deepcopy(protocol)
        if target is not None:
            if kind=='switch':
                first=read(out/'baseline/result.json')['messages']
                flow=next(m for m in first if m['id']==target['id'])
                node=flow['first_packet_path'][1]['node']
            else:node=target['dst'] if kind=='data' else target['src']
            rdma['faults']=[dict(node=node,port=0,message_id=target['id'],kind='data' if kind=='switch' else kind,
                sequence=0 if kind=='switch' else 1000 if kind=='data' else target['bytes'],
                start_ns=0,end_ns=5000000,count=1)]
        save(out/(label+'.json'),rdma)
        cli(label,['rdma-run','--config',out/'service.json','--topology',out/'topology',
            '--protocol',out/(label+'.json'),'--backend',backend['service'],'--protoc',a.protoc,'--output',out/label],kind!='ack')
        cli(label+'-diagnose',['rdma-diagnose','--run',out/label,'--output',out/(label+'-diagnosis.json')])
        result=read(out/label/'result.json');front=read(out/label/'frontend.json');diag=read(out/(label+'-diagnosis.json'))
        if not result['packet_capture']['complete']:raise RuntimeError('capture truncated; do not infer missing events')
        if target is None:
            wave=read(out/'baseline/graphs/g0.w0/wave.json')
            target=next(m for m in wave['messages'] if m['phase']=='dispatch' and m['src']==0 and m['dst']==4)
            save(out/'selected-message.json',target)
        elif result['fault_injection']['drop_counts']!=[1]:raise RuntimeError('intervention did not hit exactly once')
        flow=next(m for m in result['messages'] if m['id']==target['id'])
        callbacks=next(m for m in result['service']['message_callbacks'] if m['id']==target['id'])
        summaries[label]=dict(run_passed=result['passed'],target=flow,callbacks=callbacks,
            group_history=front['history'],request_states={state:sum(q['state']==state for q in front['requests'])
                for state in sorted({q['state'] for q in front['requests']})},
            graph_progress=diag['graph_progress'],outstanding_graph_edges=diag['outstanding_graph_edges'],
            packet_events=len(result['packet_capture']['events']),
            fault_drop_counts=result.get('fault_injection',{}).get('drop_counts',[]))
        # Offered workload fixed; compare every graph that was actually submitted
        # in both executions. A stalled group cannot submit its next graph.
        for path in (out/label/'graphs').glob('*/wave.json'):
            base=out/'baseline/graphs'/path.parent.name/'wave.json'
            if base.read_bytes()!=path.read_bytes():raise RuntimeError('common submitted graph changed')
    stalled=summaries['final-ack']
    if stalled['callbacks']['recv_callback_ns'] is None or stalled['callbacks']['send_callback_ns'] is not None:
        raise RuntimeError('ACK/data completion semantics differ from experiment definition')
    completed_other=[w for w in stalled['group_history'] if w['group_index']==1 and 'finished_ns' in w]
    if len(completed_other)!=2:raise RuntimeError('independent EP group did not finish both waves')
    sources=[ROOT/'backend/rdma/fabric.cc',ROOT/'backend/rdma/fabric.hh',ROOT/'python/moe_ub_cosim/scaleout_verify.py',
             ROOT/'python/moe_ub_cosim/scaleout_diagnostics.py',Path(__file__).resolve()]
    save(out/'summary.json',dict(passed=True,scope='8 endpoints, two interleaved EP4 groups, one layer, two ready-KV Decode steps; declared compute',
        target=target,cases=summaries,independent_group_completed_steps=2,
        graph_identity_scope='Every common submitted wave; offered requests fixed; stalled future waves do not exist',
        source_hashes={str(s.relative_to(ROOT)):hashlib.sha256(s.read_bytes()).hexdigest() for s in sources},
        limitations=['Deterministic injected drops, not calibrated hardware fault rates.',
            'Stalled means incomplete within 5ms; finite segment and fixed cost do not establish production SLO impact.',
            'Workload arrivals and expert placement fixed; this is not a new admission strategy or recovery protocol.']))
    print('fault experiment completed, including preserved expected failure')


if __name__=='__main__':main()
