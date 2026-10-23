"""Independent publication/packet checks through fresh public and native entrypoints."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from check_remote_transaction_workflow import audit
from check_transaction_session_workflow import validate_events

ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def normalize(result):
    result=deepcopy(result);result['transactions'].pop('submission',None)
    for row in result['transactions']['operations']:row.pop('prepared_ns',None)
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('backend','session-backend','old-backend','output'):parser.add_argument('--'+name,type=Path,required=True)
    a=parser.parse_args();out=a.output.resolve();out.mkdir(exist_ok=False)
    env=dict(os.environ,PYTHONPATH=str(ROOT/'python'),PYTHONDONTWRITEBYTECODE='1');commands=[]
    def invoke(name,cmd,expected=0,stdin=None):
        cmd=list(map(str,cmd))
        with (out/(name+'.stdout')).open('w') as stdout,(out/(name+'.stderr')).open('w') as stderr:
            p=subprocess.run(cmd,cwd=out,env=env,input=stdin,text=True,stdout=stdout,stderr=stderr,timeout=20)
        commands.append(dict(name=name,command=cmd,cwd=str(out),exitcode=p.returncode,expected=expected))
        save(out/'commands.json',commands);assert p.returncode==expected,name
    cli=[sys.executable,'-m','moe_ub_cosim','scaleout']
    protocol=read(ROOT/'examples/scaleout_rdma/rdma.json');protocol.update(payload_bytes=64,chunk_bytes=256)
    save(out/'protocol.json',protocol)
    invoke('topology',cli+['topology','--config',ROOT/'examples/scaleout_rdma/small-clos-100g.json','--output','topology'])
    def run(name,config,session=False,old=False,partial=False):
        save(out/(name+'.json'),config)
        backend=a.old_backend if old else a.session_backend if session else a.backend
        if partial:
            if session:
                invoke(name,cli+['rdma-transaction-session','--topology','topology','--protocol','protocol.json',
                    '--config',name+'.json','--backend',backend.resolve(),'--output',name],1)
                r=read(out/name/'result.json');validate_events(r);return r
            invoke(name,[backend.resolve(),out/'topology/topology.json',out/'protocol.json',out/(name+'.json'),out/(name+'-result.json')],1)
            return read(out/(name+'-result.json'))
        invoke(name,cli+['rdma-transaction-session' if session else 'rdma-transaction-probe',
            '--topology','topology','--protocol','protocol.json','--config',name+'.json',
            '--backend',backend.resolve(),'--output',name])
        r=read(out/name/'result.json');audit(r,config,64)
        if session:validate_events(r)
        return r
    config=read(ROOT/'examples/scaleout_rdma_transactions/submission-prefix.json')
    main_result=run('prefix',config);online=run('online',config,session=True)
    difference=deepcopy(online);del difference['session'];assert difference==main_result
    tx=main_result['transactions'];rows={r['id']:r for r in tx['operations']};net=tx['transport']
    assert rows[0]['prepared_ns']==0 and rows[0]['published_ns']==rows[2]['published_ns']==20000
    assert rows[2]['returned_value']==7 and len(tx['submission']['publications'])==1
    assert len(net['source_packets'])==5 and all(p[0]>=20000 for p in net['source_packets'])
    source={e['kind']:e for e in tx['source_events']}
    assert source['acquired']['time_ns']==0 and source['captured']['time_ns']==20000
    assert source['released']['time_ns']==rows[0]['completed_ns']>20000
    events={(e['operation'],e['event']):e['time_ns'] for e in online['session']['events']}
    regs={r['operation']:r['time_ns'] for r in online['session']['registrations']}
    assert regs[1]==events[0,'prepared']==0 and regs[2]==events[1,'completed']==20000
    assert next(w for w in online['local_work'] if w['id']==1)['start_ns']==0
    changed=deepcopy(config);changed['windows'][0]['segments'][0]['hex']='ff'+changed['windows'][0]['segments'][0]['hex'][2:]
    different=run('changed-initial',changed)
    assert different['transactions']['transport']['source_payload_packets']!=net['source_payload_packets']
    other=deepcopy(config)
    other['operations'].insert(1,dict(id=3,kind='remote',connection=1,verb='put',key=9,address=8192,hex='aa',
        aggregate_requests=False,after=[dict(operation=0,event='prepared')]))
    ro=run('other-connection',other);packets=ro['transactions']['transport']['source_packets']
    assert any(p[1]==2 and p[0]<20000 for p in packets)
    assert all(p[0]>=20000 for p in packets if p[1]==0)
    # Default mode remains a complete result oracle, including packet UIDs/timing.
    legacy=read(ROOT/'examples/scaleout_rdma_transactions/sparse-windows.json')
    default=run('default',legacy);previous=run('previous',legacy,old=True);assert default==previous
    explicit=deepcopy(legacy);explicit['submission_model']=dict(mode='explicit_prefix/1')
    for op in explicit['operations']:
        if op['kind']=='remote':op['aggregate_requests']=False
    rf=run('all-false',explicit);assert normalize(rf)==default
    # Same-ns sequential nonaggregate triggers must remain separate publications.
    burst=deepcopy(config);burst['operations']=[deepcopy(config['operations'][0]),deepcopy(config['operations'][2])]
    for op in burst['operations']:op['aggregate_requests']=False;op['after']=[]
    rb=run('two-publications',burst);assert len(rb['transactions']['submission']['publications'])==2
    credit=deepcopy(burst);credit['connections'][0]['queue_depth']=1
    rcredit=run('credit-feedback',credit);credit_rows=rcredit['transactions']['operations']
    assert credit_rows[1]['ready_ns']==0 and credit_rows[1]['prepared_ns']==credit_rows[0]['completed_ns']>0
    partials=[]
    for name in ('depth-one','aggregate-tail','fence-does-not-publish'):
        c=deepcopy(config)
        if name=='depth-one':c['connections'][0]['queue_depth']=1
        else:
            c['operations']=c['operations'][:1]
            if name=='fence-does-not-publish':
                trigger=deepcopy(config['operations'][2]);trigger['after']=[dict(operation=0,event='prepared')];trigger['queue_fence']=[0]
                c['operations'].append(trigger)
        r=run(name,c,partial=True);t=r['transactions'];rs={op['id']:op for op in t['operations']}
        assert not r['passed'] and not r['drained'] and r['finish_ns']==5000000
        assert t['submission']['publications']==[] and t['transport']['source_packets']==[]
        assert rs[0]['prepared_ns']==0 and rs[0]['published_ns']==rs[0]['completed_ns']==-1
        assert len(t['active_source_leases'])==1 and not any(e['kind']=='captured' for e in t['source_events'])
        for w in t['windows']:
            initial=next(s for s in c['windows'] if s['owner']==w['owner'])
            data=bytearray(w['bytes'])
            for page in w['pages']:data[page['offset']:page['offset']+len(page['hex'])//2]=bytes.fromhex(page['hex'])
            expected=bytearray(w['bytes'])
            for segment in initial['segments']:expected[segment['offset']:segment['offset']+len(segment['hex'])//2]=bytes.fromhex(segment['hex'])
            assert data==expected
        if name=='depth-one':assert rs[2]['prepared_ns']==rs[2]['published_ns']==-1
        if name=='fence-does-not-publish':assert len(rs)==1 and t['queue_fences'][0]['completed_ns']==-1
        session_partial=run(name+'-online',c,session=True,partial=True)
        assert session_partial['finish_ns']==r['finish_ns'] and session_partial['transactions']==t
        partials.append(name)
    conflict=deepcopy(config);conflict['operations']=conflict['operations'][:1]+[dict(id=4,kind='local',rank=0,duration_ns=0,
        copy=dict(source=dict(key=7,address=4224,bytes=8),target=dict(key=7,address=4096)),after=[dict(operation=0,event='prepared')])]
    rc=run('source-conflict',conflict,partial=True)
    assert 'execution_error' in rc and len(rc['transactions']['active_source_leases'])==1
    assert not rc['transactions']['transport']['source_packets']
    # Session receives a valid first and invalid second member in ONE COMMIT.
    # No node may be registered/executed from that rejected batch.
    initial=deepcopy(config);initial['operations']=[];save(out/'initial.json',initial)
    bad=deepcopy(config['operations'][2]);bad['after']=[];bad['aggregate_requests']=1
    command=dict(type='COMMIT',decision_id=0,now_ns=0,submissions=[config['operations'][0],bad],finish=False)
    invoke('invalid-batch',[a.session_backend.resolve(),out/'topology/topology.json',out/'protocol.json',out/'initial.json',out/'invalid-batch-result.json'],1,json.dumps(command)+'\n')
    rejected=read(out/'invalid-batch-result.json')
    assert not rejected['transactions']['operations'] and not rejected['session']['registrations']
    assert not rejected['transactions']['transport']['source_packets']
    negative=[]
    for name in ('missing-flag','integer-flag','unknown-mode','local-flag','old-schema','unselected-flag','unselected-prepared'):
        c=deepcopy(config)
        if name=='missing-flag':del c['operations'][0]['aggregate_requests']
        elif name=='integer-flag':c['operations'][0]['aggregate_requests']=1
        elif name=='unknown-mode':c['submission_model']['mode']='unknown'
        elif name=='local-flag':c['operations'][1]['aggregate_requests']=True
        elif name=='old-schema':c['schema_version']='moe-rdma-transactions/4'
        elif name=='unselected-flag':del c['submission_model']
        else:
            del c['submission_model']
            for op in c['operations']:op.pop('aggregate_requests',None)
        save(out/(name+'.json'),c)
        invoke(name+'-probe',[a.backend.resolve(),out/'topology/topology.json',out/'protocol.json',out/(name+'.json'),out/(name+'-rejected.json')],1)
        assert not (out/(name+'-rejected.json')).exists()
        # For populated configs submit the batch to an initially empty session;
        # unknown/old root modes are instead rejected at initialization.
        if name in ('unknown-mode','old-schema'):
            c['operations']=[];save(out/(name+'-initial.json'),c);stdin=None;init=out/(name+'-initial.json')
        else:
            init=out/'initial.json'
            if name.startswith('unselected'):
                start=deepcopy(initial);del start['submission_model'];save(out/(name+'-initial.json'),start);init=out/(name+'-initial.json')
            stdin=json.dumps(dict(type='COMMIT',decision_id=0,now_ns=0,submissions=c['operations'],finish=False))+'\n'
        invoke(name+'-session',[a.session_backend.resolve(),out/'topology/topology.json',out/'protocol.json',init,out/(name+'-session-result.json')],1,stdin)
        result=out/(name+'-session-result.json')
        if result.exists():
            r=read(result);assert r['session']['registrations']==[] and r['transactions']['operations']==[]
        negative.append(name)
    mutated=[]
    for kind in ('prepared-time','publication-prefix','early-source-packet'):
        r=deepcopy(main_result)
        if kind=='prepared-time':r['transactions']['operations'][0]['prepared_ns']=1
        elif kind=='publication-prefix':r['transactions']['submission']['publications'][0]['exclusive']=1
        else:r['transactions']['transport']['source_packets'][0][0]=19999
        try:audit(r,config,64)
        except AssertionError:mutated.append(kind)
        else:raise AssertionError('oracle accepted '+kind)
    summary=dict(passed=True,commands=len(commands),real_source_packets=len(net['source_packets']),publication_ns=20000,
        actual_atomic_old_value=rows[2]['returned_value'],actual_prepared_callback_drives_online_submission=True,
        default_previous_equal=True,all_false_except_new_fields_equal=True,online_except_session_equal=True,
        partial_cases=partials,source_protected_while_unpublished=True,invalid_batch_registered_nothing=True,
        native_rejection_classes=negative,oracle_mutations_rejected=mutated,hardware_calibrated=False,
        binaries={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in (a.backend,a.session_backend,a.old_backend)})
    save(out/'verification.json',summary);print(json.dumps(summary))

if __name__=='__main__':main()
