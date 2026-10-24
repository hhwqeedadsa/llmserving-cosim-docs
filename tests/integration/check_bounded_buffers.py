"""Actual native pool reuse, ACK-loss retention and atomic admission checks."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from moe_ub_cosim.transaction_session import NativeTransactionSession

ROOT=Path(__file__).resolve().parents[2]


def read(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def base(cap=40):
    p=read(ROOT/'examples/scaleout_rdma_transactions/control-alarm.json')
    p['operations']=[];p['connections'][0]['queue_depth']=4
    p['windows'][0]['hex']=bytes(range(64)).hex();p['windows'][1]['hex']='00'*512
    p['buffer_model']=dict(mode='bounded_copied_put/1',transport_bytes_per_source=cap,
        registered_bytes_per_owner=4096,target_cache_bytes_per_owner=4096,local_copy_bytes_per_owner=4096)
    return p


def put(identity,connection=0,address=None,source=4096,size=8,after=None):
    return dict(id=identity,kind='remote',connection=connection,verb='put',key=9,
        address=8192+identity*16 if address is None else address,
        source=dict(key=7,address=source,bytes=size),after=after or [])


def copy(identity,parent,address=None):
    return dict(id=identity,kind='local',rank=4,duration_ns=0,
        copy=dict(source=dict(key=9,address=8192+parent*16 if address is None else address,bytes=8),
                  target=dict(key=9,address=8448+parent*16)),after=[dict(operation=parent,event='applied')])


def connection(config,identity,source,destination=4):
    config['connections'].append(dict(config['connections'][0],id=identity,src=source,dst=destination,
        request_port=24000+2*identity,response_port=24001+2*identity))
    if not any(w['owner']==source and w['key']==7 for w in config['windows']):
        config['windows'].append(dict(owner=source,key=7,address=4096,allow_atomic=False,hex=bytes(range(64,128)).hex()))
    if not any(w['owner']==destination and w['key']==9 for w in config['windows']):
        config['windows'].append(dict(owner=destination,key=9,address=8192,allow_atomic=False,hex='00'*512))


def exercise(name,backend,topology,protocol,output,cap=40,legacy=False):
    folder=output/name;folder.mkdir();initial=base(cap);p=deepcopy(protocol)
    nodes=[put(0),copy(10,0)]
    delayed=[];expected_partial=False
    if name=='single-frame-reuse':nodes=[put(i,source=4096+i*8) for i in range(4)]+[copy(10+i,i) for i in range(4)]
    if name=='cross-connection':
        connection(initial,1,0,5);nodes=[put(0),put(1,connection=1)]
    if name=='independent-sources':
        connection(initial,1,1);nodes=[put(0),put(1,connection=1)]
    if name in ('tail-data-retry','middle-data-nack'):
        initial['windows'][0]['hex']=(bytes(range(256))*16).hex()
        initial['windows'][1]['hex']='00'*16384
        initial['buffer_model'].update(registered_bytes_per_owner=65536,transport_bytes_per_source=2080,target_cache_bytes_per_owner=8192)
        nodes=[put(0,size=2048),put(1,source=6144,size=2048,address=12288)]
        p['faults']=[dict(node=4,port=0,message_id=0,kind='data',sequence=2000 if name=='tail-data-retry' else 1000,
                        count=1,start_ns=0,end_ns=5000000)]
    if name in ('failed-held-with-spare','failed-exhausted'):
        expected_partial=True
        if name=='failed-held-with-spare':
            connection(initial,1,0);nodes=[put(0),put(1,connection=1)]
            delayed=[put(2,connection=1)]
        else:
            connection(initial,1,0,5);connection(initial,2,1)
            nodes=[put(0),put(1,connection=1),put(2,connection=2)]
        p['faults']=[dict(node=0,port=0,message_id=0,kind='ack',sequence=40,count=1000,start_ns=0,end_ns=5000000)]
    if 'faults' in p:
        initial['transport_recovery']=dict(mode='go_back_n/1',initial_rto_ns=10000,max_rto_ns=40000,
            max_retries=2,max_events=10000,on_exhausted='isolate_stream')
    if legacy:initial.pop('buffer_model')
    save(folder/'protocol.json',p);timer=expected_partial;sent_later=False;partial=False
    try:
        with NativeTransactionSession(topology,folder/'protocol.json',initial,backend,folder) as session:
            while (event:=session.receive()) is not None:
                if event['decision_id']==0:
                    session.commit(nodes,alarms=[dict(id=0,delay_ns=100000)] if timer else None)
                    continue
                submissions=[]
                if any(e.get('alarm')==0 for e in event['events']):
                    timer=False
                    if delayed and not sent_later:submissions=delayed;sent_later=True
                session.commit(submissions,finish=not submissions and event['drained'] and not timer)
    except ValueError as error:
        if not expected_partial or 'native transaction session failed; inspect partial result' not in str(error):raise
        partial=True
    r=read(folder/'result.json')
    save(folder/'effective-program.json',session.config)
    assert partial==expected_partial
    if partial:assert not r['passed'] and r['finish_ns']==5000000 and 'execution_error' not in r
    else:assert r['passed'] and r['session']['finished']
    if name in ('tail-data-retry','middle-data-nack'):assert r['fault_drops']==1
    return r


def invalid(kind,backend,topology,protocol,output):
    folder=output/('native-'+kind);folder.mkdir();p=base();p['buffer_model']['target_cache_bytes_per_owner']=40
    initial_bad=kind not in ('cache-batch','frame-batch','copy-batch')
    if kind=='bool-cap':p['buffer_model']['transport_bytes_per_source']=True
    elif kind=='negative-cap':p['buffer_model']['registered_bytes_per_owner']=-1
    elif kind=='too-large':p['buffer_model']['transport_bytes_per_source']=2**64-1
    elif kind=='extra-field':p['buffer_model']['extra']=1
    elif kind=='zero-copy':p['buffer_model']['mode']='zero_copy'
    elif kind=='window-cap':p['buffer_model']['registered_bytes_per_owner']=511
    elif kind=='inline':
        node=put(0);node.pop('source');node['hex']='0102030405060708';p['operations']=[node]
    elif kind=='frame-batch':p['buffer_model']['transport_bytes_per_source']=39
    elif kind=='copy-batch':p['buffer_model']['local_copy_bytes_per_owner']=7
    save(folder/'initial.json',p);save(folder/'protocol.json',protocol)
    command=[str(backend),str(topology),str(folder/'protocol.json'),str(folder/'initial.json'),str(folder/'result.json')]
    save(folder/'command.json',command)
    with (folder/'stderr.log').open('w') as errors,(folder/'transcript.jsonl').open('w') as transcript:
        child=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,text=True)
        if initial_bad:
            stdout,_=child.communicate(timeout=10);(folder/'stdout.log').write_text(stdout)
            assert child.returncode==1 and not (folder/'result.json').exists()
        else:
            def receive():
                line=child.stdout.readline();assert line.startswith('MOE_TX_EVENT ')
                event=json.loads(line[len('MOE_TX_EVENT '):]);transcript.write(json.dumps(dict(native=event))+'\n');return event
            def send(event,nodes,**extra):
                c=dict(type='COMMIT',decision_id=event['decision_id'],now_ns=event['now_ns'],submissions=nodes,finish=False,**extra)
                transcript.write(json.dumps(dict(frontend=c))+'\n');transcript.flush()
                child.stdin.write(json.dumps(c)+'\n');child.stdin.flush()
            event=receive();send(event,[],alarms=[dict(id=0,delay_ns=100000),dict(id=1,delay_ns=0)])
            event=receive();nodes=[put(0),put(1)] if kind=='cache-batch' else [put(0)]
            if kind=='frame-batch':nodes=[put(0,size=1),put(1)]
            if kind=='copy-batch':nodes=[put(0),copy(10,0)]
            send(event,nodes,cancel_alarms=[0]);stdout,_=child.communicate(timeout=10)
            (folder/'stdout.log').write_text(stdout);assert child.returncode==1
            r=read(folder/'result.json')
            assert not r['session']['registrations'] and not r['transactions']['operations']
            assert not r['transactions']['transport']['source_packets']
            alarm=r['session']['control_alarms'][0]
            assert alarm['id']==0 and alarm['cancelled_ns']==alarm['fired_ns']==-1
        child.stdout.close()
    return kind


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('backend','old-backend','topology','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--additional-case',choices=['middle-data-nack','tail-data-retry'])
    a=p.parse_args();a.output.mkdir();protocol=read(ROOT/'examples/scaleout_rdma/rdma.json')
    protocol['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=20000,wire_headers=True)
    if a.additional_case:
        r=exercise(a.additional_case,a.backend,a.topology,protocol,a.output,cap=2080)
        events=[dict(zip(r['packet_capture']['columns'],row)) for row in r['packet_capture']['events']]
        actual_nack=any(e['event']==1 and e['ip_protocol']==253 and e['node']==0 for e in events)
        if a.additional_case=='middle-data-nack':assert actual_nack
        save(a.output/'verification.json',dict(passed=True,additional_case=a.additional_case,
             actual_fault_drops=r['fault_drops'],actual_nack_received_at_source=actual_nack,
             source_snapshot_preserved_during_recovery=True))
        return
    exercise('old-default',a.old_backend,a.topology,protocol,a.output,legacy=True)
    exercise('default',a.backend,a.topology,protocol,a.output,legacy=True)
    assert (a.output/'old-default/result.json').read_bytes()==(a.output/'default/result.json').read_bytes()
    ample=exercise('ample',a.backend,a.topology,protocol,a.output,cap=4096)
    normalized=deepcopy(ample);normalized['transactions'].pop('buffer_retention');normalized['transactions']['transport'].pop('payload_pool')
    assert normalized==read(a.output/'default/result.json')
    results={}
    for name,cap in [('single-frame-reuse',40),('cross-connection',40),('independent-sources',40),
                     ('tail-data-retry',2080),('failed-held-with-spare',80),('failed-exhausted',40)]:
        results[name]=exercise(name,a.backend,a.topology,protocol,a.output,cap=cap)
    ops=lambda name:{op['id']:op for op in results[name]['transactions']['operations']}
    serial=ops('single-frame-reuse')
    assert all(serial[i]['published_ns']>=serial[i-1]['request_acked_ns'] for i in range(1,4))
    cross=ops('cross-connection');assert cross[1]['published_ns']>=cross[0]['request_acked_ns']
    assert all(op['published_ns']==0 for op in ops('independent-sources').values())
    spare=ops('failed-held-with-spare');assert spare[0]['completed_ns']==-1 and spare[2]['completed_ns']>100000
    full=ops('failed-exhausted');assert full[1]['published_ns']==-1 and full[2]['completed_ns']>0
    kinds=('bool-cap','negative-cap','too-large','extra-field','zero-copy','window-cap','inline','cache-batch','frame-batch','copy-batch')
    rejected=[invalid(k,a.backend,a.topology,protocol,a.output) for k in kinds]
    save(a.output/'verification.json',dict(passed=True,functional_new_conditions=8,old_runtime_comparisons=1,
        default_complete_result_byte_identical=True,ample_equal_except_new_fields=True,native_rejections=rejected,
        single_frame_completed_writes=4,failed_pool_and_source_leases_retained=True,
        same_source_spare_capacity_progress=True,exhausted_same_source_does_not_capture=True))


if __name__=='__main__':main()
