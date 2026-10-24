"""Shared physical NIC survival and replies queued after local reply-source failure."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from moe_ub_cosim.host_probe import probe

def read(p):return json.loads(p.read_text())
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True)
    r=p.parse_args().runtime.resolve();out=r/'shared-source';out.mkdir();cases={}
    def execute(n,w,pc,kind):
        for suffix,data in [('work',w),('protocol',pc)]:
            (out/(n+'-'+suffix+'.json')).write_text(json.dumps(data,indent=2)+'\n')
        cmd=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-'+kind+'-probe',
            '--topology',r/'accept/topology','--protocol',out/(n+'-protocol.json'),'--config',out/(n+'-work.json'),
            '--backend',r/'revised-build/bin'/('ns3.42-moe-rdma-'+kind+'-probe'),'--output',out/n]
        m=probe(list(map(str,cmd)),out/(n+'-monitor'),rss_bytes=2**30,address_bytes=2**31,
            artifact_bytes=64*2**20,wall_seconds=60,artifact_roots=[out/n],min_free_bytes=8*2**30)
        assert m['child_exit_code']==1 and m['status']=='child_failed' and not m['remaining_live_group_pids']
        result=read(out/n/'result.json');assert not result['passed'] and 'execution_error' not in result
        cases[n]=dict(result=str(out/n/'result.json'));return result
    w=read(r/'late-submit-after/late-submit-work.json');pc=read(r/'late-submit-after/late-submit-protocol.json')
    w['streams'][1]['src']=0;w['work'][1]['rank']=0
    result=execute('same-nic',w,pc,'stream');t=result['persistent_transport']
    assert next(x for x in t['work'] if x['id']==1)['acked_ns']>100000
    assert len(t['recovery']['failures'])==1
    w=read(r/'accept/response-source-failure-work.json');pc=read(r/'accept/response-source-failure-protocol.json')
    w['connections'][0]['queue_depth']=4
    w['operations'].append(dict(id=11,kind='local',rank=0,duration_ns=100000,after=[]))
    op=deepcopy(w['operations'][0]);op.update(id=2,after=[dict(operation=11,event='completed')]);w['operations'].append(op)
    result=execute('late-generated-response',w,pc,'transaction');tx=result['transactions'];t=tx['transport']
    assert len(tx['target_events'])==3 and next(o for o in tx['operations'] if o['id']==1)['completed_ns']>100000
    assert next(o for o in tx['operations'] if o['id']==2)['applied_ns']>t['recovery']['failure']['time_ns']
    late=[x for x in t['work'] if x['stream']==1 and x['ready_ns']>t['recovery']['failure']['time_ns']]
    assert len(late)==1 and late[0]['published_ns']==late[0]['acked_ns']==-1
    (out/'verification.json').write_text(json.dumps(dict(passed=True,cases=cases),indent=2)+'\n')
    print('Same-NIC healthy QP and terminal-source generated reply passed')

if __name__=='__main__':main()
