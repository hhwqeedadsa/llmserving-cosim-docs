"""Real online callback execution must preserve health after a peer-source failure."""
import argparse
import json
from pathlib import Path
import sys
from moe_ub_cosim.host_probe import probe

ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text())
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True)
    r=p.parse_args().runtime.resolve();out=r/'online-failure';out.mkdir()
    config=read(r/'edges/sparse-leases-work.json');protocol=read(r/'accept/transaction-leases-protocol.json')
    for name,value in [('online-work.json',config),('online-protocol.json',protocol)]:
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    cmd=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-transaction-session',
        '--topology',r/'accept/topology','--protocol',out/'online-protocol.json','--config',out/'online-work.json',
        '--backend',r/'revised-build/bin/ns3.42-moe-rdma-transaction-session','--output',out/'online']
    m=probe(list(map(str,cmd)),out/'monitor',rss_bytes=2**30,address_bytes=2**31,
        artifact_bytes=64*2**20,wall_seconds=60,artifact_roots=[out/'online'],min_free_bytes=8*2**30)
    assert m['child_exit_code']==1 and m['status']=='child_failed' and not m['remaining_live_group_pids']
    result=read(out/'online/result.json');tx=result['transactions'];session=result['session']
    assert not result['passed'] and not session['finished'] and 'execution_error' not in result
    assert tx['transport']['recovery']['failure']['time_ns']==70000
    assert tx['operations'][0]['completed_ns']==-1 and tx['operations'][1]['completed_ns']>=100000
    assert len(tx['active_source_leases'])==1 and tx['active_source_leases'][0]['operation']==0
    assert any(e['operation']==1 and e['event']=='completed' for e in session['events'])
    assert not any(e['operation']==0 and e['event'] in ['request_acked','completed'] for e in session['events'])
    report=dict(passed=True,partial_result=True,healthy_completed_ns=tx['operations'][1]['completed_ns'],
        failed_source_lease_retained=True,decisions=session['decisions'],callback_events=len(session['events']))
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':main()
