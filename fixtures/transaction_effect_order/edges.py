"""Native bypass and online incremental admission; legal carrier-order networks."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from run import ROOT, work, save, read
from moe_ub_cosim.host_probe import probe

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True);a=p.parse_args()
    root=a.runtime.resolve();out=root/'edges';out.mkdir();records=[]
    topology=root/'after/topology/topology.json';protocol=root/'after/protocol.json'
    backend=root/'build/bin/ns3.42-moe-rdma-transaction-probe'
    def native(name,w):
        save(out/(name+'.json'),w)
        cmd=list(map(str,[backend,topology,protocol,out/(name+'.json'),out/(name+'-result.json')]))
        r=probe(cmd,out/(name+'-monitor'),rss_bytes=2**30,address_bytes=2**31,
            artifact_bytes=64*2**20,wall_seconds=60,artifact_roots=[out],min_free_bytes=8*2**30)
        records.append(r)
        assert r['status']=='child_failed' and r['child_exit_code']==1,name
        assert 'remote effect order' in (out/(name+'-monitor/stderr.log')).read_text(),name
        assert not (out/(name+'-result.json')).exists()
    native('prepared-cross',work('unsafe'))
    # Existing non-atomic ordering requirements must also hold on native v1-v4.
    for version in range(1,5):
        w=work('unsafe');w['schema_version']=f'moe-rdma-transactions/{version}'
        w.pop('submission_model');w['windows']=[dict(owner=4,key=7,address=4096,
            allow_atomic=True,hex='00'*64)]
        w['operations']=[deepcopy(w['operations'][i]) for i in [0,2]]
        for op in w['operations']:
            op['after']=[];op.pop('aggregate_requests')
            if version==1:op.pop('kind')
        if version<4:w.pop('local_memory_model')
        if version<3:w.pop('source_model')
        if version<2:w.pop('local_model')
        native('unordered-v'+str(version),w)
    for name in ['same-carrier','indirect-completion']:
        w=work('unsafe')
        if name=='same-carrier':
            w['operations'][2]['connection']=0
        else:
            bridge=deepcopy(w['operations'][0]);bridge.update(id=6,address=4144,
                after=[dict(operation=0,event='prepared')])
            w['operations'].insert(1,bridge)
            w['operations'][2]['after']=[dict(operation=6,event='request_acked')]
        save(out/(name+'.json'),w)
        cmd=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-transaction-probe',
            '--topology',str(topology.parent),'--protocol',str(protocol),'--config',str(out/(name+'.json')),
            '--backend',str(backend),'--output',str(out/name)]
        r=probe(cmd,out/(name+'-monitor'),rss_bytes=2**30,address_bytes=2**31,
            artifact_bytes=64*2**20,wall_seconds=60,artifact_roots=[out],min_free_bytes=8*2**30)
        records.append(r);assert r['status']=='completed',name
        result=read(out/name/'result.json')
        copied=[e for e in result['transactions']['local_memory_events'] if e['kind']=='committed']
        assert result['passed'] and copied[0]['hex']=='22'*8
    # Raw native protocol bypasses the Python API on purpose. First accept only
    # the old request, then reject the unsafe extension atomically as a batch.
    w=work('unsafe');initial=deepcopy(w);initial['operations']=[];save(out/'initial.json',initial)
    cmd=list(map(str,[root/'build/bin/ns3.42-moe-rdma-transaction-session',topology,
                     protocol,out/'initial.json',out/'session-result.json']))
    transcript=[]
    with (out/'session.stderr').open('w') as se:
        child=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=se,text=True)
        try:
            for batch in [[w['operations'][0]],w['operations'][1:]]:
                line=child.stdout.readline();transcript.append(line)
                assert line.startswith('MOE_TX_EVENT '),line
                event=json.loads(line.removeprefix('MOE_TX_EVENT '))
                command=dict(type='COMMIT',decision_id=event['decision_id'],now_ns=event['now_ns'],
                             submissions=batch,finish=False)
                transcript.append(json.dumps(command)+'\n');child.stdin.write(transcript[-1]);child.stdin.flush()
            more,_=child.communicate(timeout=10);transcript.append(more)
            assert child.returncode==1
        finally:
            if child.poll() is None:child.kill();child.wait()
    (out/'session.transcript').write_text(''.join(transcript))
    result=read(out/'session-result.json')
    assert 'remote effect order' in result['execution_error']
    registrations=result['session']['registrations']
    assert [r['operation'] for r in registrations]==[0]
    assert [o['id'] for o in result['transactions']['operations']]==[0]
    save(out/'verification.json',dict(passed=True,native_rejections=5,legal_networks=2,
        online_rejected_batch_after_legal_registration=registrations,records=records))
    print('native v1-v5 reject unsafe order; two ordered networks and atomic online rejection passed')
if __name__=='__main__':main()
