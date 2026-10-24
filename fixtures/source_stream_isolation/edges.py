"""Terminal independent CNP, sparse source leases, and online recovery regression."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from moe_ub_cosim.host_probe import probe

ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True)
    a=p.parse_args();runtime=a.runtime.resolve();out=runtime/'edges';out.mkdir();records=[]
    bins=runtime/'revised-build/bin';base=runtime/'accept';topology=base/'topology'
    def run(n,cmd,code=0):
        m=probe(list(map(str,cmd)),out/(n+'-monitor'),rss_bytes=2**30,address_bytes=2**31,
            artifact_bytes=64*2**20,wall_seconds=60,artifact_roots=[out/n],min_free_bytes=8*2**30)
        records.append(m);save(out/'commands.json',records)
        assert m['child_exit_code']==code and m['status'] in ['completed','child_failed'] and not m['remaining_live_group_pids'],n
        assert sum(read(q)['wall_seconds'] for q in runtime.rglob('probe.json'))<450
        assert sum(f.stat().st_size for f in runtime.rglob('*') if f.is_file())<192*2**20
    def execute(n,w,pc,kind):
        save(out/(n+'-work.json'),w);save(out/(n+'-protocol.json'),pc)
        run(n,[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-'+kind+'-probe',
            '--topology',topology,'--protocol',out/(n+'-protocol.json'),'--config',out/(n+'-work.json'),
            '--backend',bins/('ns3.42-moe-rdma-'+kind+'-probe'),'--output',out/n],1)
        return read(out/n/'result.json')
    ref=read(base/'independent-cnp-work.json');pc=read(base/'independent-cnp-protocol.json');pc.pop('faults')
    ref['transport_recovery'].update(initial_rto_ns=1000,max_rto_ns=1000,max_retries=1)
    r=execute('terminal-cnp',ref,pc,'stream');t=r['persistent_transport']
    assert t['source_isolation']['terminal_feedback_complete']
    terminal_cnp=[e for e in t['source_isolation']['terminal_feedback'] if e['ip_protocol']==255]
    assert terminal_cnp and any(e['kind']=='cnp_generated' for e in t['dcqcn_reference']['events'])
    tx=read(base/'transaction-leases-work.json');pc=read(base/'transaction-leases-protocol.json')
    tx['schema_version']='moe-rdma-transactions/5';tx['submission_model']=dict(mode='explicit_prefix/1')
    for w in tx['windows']:
        b=w.pop('hex');w.update(bytes=len(b)//2,initialization='zero_fill/1',segments=[dict(offset=0,hex=b)])
    for op in tx['operations']:
        if op['kind']=='remote':op['aggregate_requests']=False
    r=execute('sparse-leases',tx,pc,'transaction');v=r['transactions']
    assert len(v['active_source_leases'])==1 and v['active_source_leases'][0]['operation']==0
    assert v['operations'][0]['completed_ns']==-1 and v['operations'][1]['completed_ns']>=100000
    # Existing online control still derives the next payload from real reply.
    program=read(ROOT/'examples/scaleout_rdma_transactions/session-program.json')
    program['transport_recovery']=dict(tx['transport_recovery'],initial_rto_ns=80000,max_rto_ns=640000,max_retries=3)
    save(out/'session-program.json',program)
    pc=read(base/'default-stream-new-protocol.json')
    pc['faults']=[dict(node=0,port=0,message_id=1,kind='data',sequence=0,count=1,start_ns=0,end_ns=5000000)]
    save(out/'session-protocol.json',pc)
    run('online-loss',[sys.executable,'-B',ROOT/'examples/scaleout_rdma_transactions/session_driver.py',
        '--topology',topology,'--protocol',out/'session-protocol.json','--config',out/'session-program.json',
        '--backend',bins/'ns3.42-moe-rdma-transaction-session','--output',out/'online-loss'])
    r=read(out/'online-loss/result.json');assert r['passed'] and r['fault_injection']['drop_counts']==[1]
    actual=r['transactions']['operations'][0]['returned_value']
    effective=read(out/'online-loss/effective-program.json')
    assert int.from_bytes(bytes.fromhex(effective['operations'][1]['hex']),'little')==actual+100
    save(out/'verification.json',dict(passed=True,terminal_cnp_packets=len(terminal_cnp),commands=len(records),
        sparse_lease_retained=True,online_actual_return=actual))
    print('Terminal CNP, sparse leases and online real-reply regression passed')

if __name__=='__main__':main()
