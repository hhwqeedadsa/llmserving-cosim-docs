"""Exercise public control alarms, real memory delivery and native bypass rejection."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import time

from moe_ub_cosim.transaction_session import NativeTransactionSession

ROOT=Path(__file__).resolve().parents[2]


def read(path):return json.loads(Path(path).read_text())
def save(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n')


def exercise(name,mode,backend,topology,protocol,output,host_delay=False):
    folder=output/name;folder.mkdir()
    program=read(ROOT/'examples/scaleout_rdma_transactions/control-alarm.json')
    put,copy=deepcopy(program['operations'])
    config=dict(program,operations=[])
    gpu=dict(id=50,kind='local',rank=4,duration_ns=100000,after=[])
    healthy=dict(put,id=2,connection=1,address=8256)
    healthy_copy=dict(copy,id=3,copy=dict(source=dict(key=9,address=8256,bytes=8),target=dict(key=9,address=8288)),
                      after=[dict(operation=2,event='applied')])
    p=deepcopy(protocol)
    if mode=='pending_wait':
        config['windows'][0]['hex']='0100000000000000'
        put['address']=8248;copy['copy']['source']['address']=8248
        gpu.update(duration_ns=0,local_wait=[dict(key=9,address=8248,compare='eq',value=1)])
    if mode=='source_failed':
        config['connections'].append(dict(config['connections'][0],id=1,src=1,request_port=24002,response_port=24003))
        config['windows'].append(dict(owner=1,key=7,address=4096,allow_atomic=False,hex='1011121314151617'))
        config['transport_recovery']=dict(mode='go_back_n/1',initial_rto_ns=10000,max_rto_ns=40000,
                    max_retries=2,max_events=10000,on_exhausted='isolate_stream')
        p['faults']=[dict(node=0,port=0,message_id=0,kind='ack',sequence=40,count=1000,start_ns=0,end_ns=5000000)]
    save(folder/'protocol.json',p)
    pending=set();applied=set();copied=set();sent=False;copy_sent=False;healthy_sent=False;healthy_copy_sent=False
    expected_partial=False
    try:
        with NativeTransactionSession(topology,folder/'protocol.json',config,backend,folder) as session:
            while (event:=session.receive()) is not None:
                if event['decision_id']==0:
                    if host_delay:time.sleep(.02)
                    alarms=None
                    if mode in ('default','legacy_gpu'):
                        nodes=[put,copy] if mode=='default' else [put,gpu]
                        sent=True
                    elif mode in ('busy_gpu','pending_wait'):
                        nodes=[gpu];alarms=[dict(id=0,delay_ns=20000)]
                    elif mode=='idle_alarm':nodes=[];alarms=[dict(id=0,delay_ns=20000)]
                    elif mode=='timer_only':nodes=[];alarms=[dict(id=0,delay_ns=20000)]
                    elif mode=='cancel_and_finish':nodes=[];alarms=[dict(id=0,delay_ns=20000),dict(id=1,delay_ns=100000)]
                    elif mode=='zero_alarm':nodes=[put];sent=True;alarms=[dict(id=0,delay_ns=0),dict(id=1,delay_ns=0)]
                    else:nodes=[put];sent=True;alarms=[dict(id=0,delay_ns=100000)]
                    pending.update(a['id'] for a in alarms or [])
                    session.commit(nodes,alarms=alarms)
                    if mode=='default':copy_sent=True
                    continue
                fired=set()
                for row in event['events']:
                    if 'alarm' in row:fired.add(row['alarm']);pending.remove(row['alarm'])
                    elif row['event']=='applied':applied.add(row['operation'])
                    elif row['event']=='completed':copied.add(row['operation'])
                nodes=[];cancel=None
                if mode=='cancel_and_finish' and 0 in fired:
                    session.commit([],finish=True,cancel_alarms=[1]);pending.remove(1);continue
                if mode in ('idle_alarm','busy_gpu','pending_wait') and 0 in fired and not sent:
                    nodes.append(put);sent=True
                if 0 in applied and not copy_sent:
                    nodes.append(copy);copy_sent=True
                if mode=='source_failed':
                    if 0 in fired and not healthy_sent:nodes.append(healthy);healthy_sent=True
                    if 2 in applied and not healthy_copy_sent:nodes.append(healthy_copy);healthy_copy_sent=True
                if mode=='control_cancel' and 1 in copied and 0 in pending:
                    cancel=[0];pending.remove(0)
                finish=event['drained'] and not nodes and not pending
                session.commit(nodes,finish=finish,cancel_alarms=cancel)
    except ValueError as error:
        if mode!='source_failed' or str(error)!='Scale-Out topology: native transaction session failed; inspect partial result and native.stderr.log':raise
        expected_partial=True
    result=read(folder/'result.json')
    if mode=='source_failed':
        assert expected_partial and not result['passed'] and result['finish_ns']==5000000 and 'execution_error' not in result
        assert result['transactions']['active_source_leases']==[dict(operation=0,owner=0,key=7,address=4096,bytes=8)]
        assert any(op['id']==2 and op['completed_ns']>100000 for op in result['transactions']['operations'])
    else:assert result['passed'] and result['session']['finished']
    save(folder/'effective-program.json',session.config)
    return result


def native_invalid(kind,backend,topology,protocol,base,output):
    folder=output/('native-'+kind);folder.mkdir();save(folder/'initial.json',base);save(folder/'protocol.json',protocol)
    put=read(ROOT/'examples/scaleout_rdma_transactions/control-alarm.json')['operations'][0]
    argv=[str(backend),str(topology),str(folder/'protocol.json'),str(folder/'initial.json'),str(folder/'result.json')]
    stages=[]
    two_stage={'cancel_duplicate','cancel_fired','id_reuse_fired','pending_finish','finish_and_arm','invalid_node_cancel','lifetime_limit','id_reuse_cancelled'}
    with (folder/'stderr.log').open('w') as errors:
        child=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors,text=True)
        def decision():
            line=child.stdout.readline();stages.append(line);assert line.startswith('MOE_TX_EVENT ')
            return json.loads(line[len('MOE_TX_EVENT '):])
        def command(event,**extra):
            return dict(type='COMMIT',decision_id=event['decision_id'],now_ns=event['now_ns'],submissions=[],finish=False,**extra)
        def send(message):
            child.stdin.write(json.dumps(message)+'\n');child.stdin.flush()
        event=decision()
        if kind in two_stage:
            alarms=[dict(id=i,delay_ns=0) for i in range(32)] if kind=='lifetime_limit' else [dict(id=0,delay_ns=100000),dict(id=1,delay_ns=0)]
            send(command(event,alarms=alarms));event=decision()
            if kind=='id_reuse_cancelled':
                send(command(event,alarms=[dict(id=2,delay_ns=0)],cancel_alarms=[0]));event=decision()
        cmd=command(event);cmd['submissions']=[put]
        if kind=='bool_id':cmd['alarms']=[dict(id=True,delay_ns=1)]
        elif kind=='negative_id':cmd['alarms']=[dict(id=-1,delay_ns=1)]
        elif kind=='large_id':cmd['alarms']=[dict(id=65536,delay_ns=1)]
        elif kind=='bool_delay':cmd['alarms']=[dict(id=0,delay_ns=True)]
        elif kind=='negative_delay':cmd['alarms']=[dict(id=0,delay_ns=-1)]
        elif kind=='cutoff':cmd['alarms']=[dict(id=0,delay_ns=5000000)]
        elif kind=='beyond_cutoff':cmd['alarms']=[dict(id=0,delay_ns=5000001)]
        elif kind=='duplicate':cmd['alarms']=[dict(id=0,delay_ns=1),dict(id=0,delay_ns=2)]
        elif kind=='wrong_fields':cmd['alarms']=[dict(id=0,delay_ns=1,operation=0)]
        elif kind=='wrong_list':cmd['alarms']=True
        elif kind=='over_budget':cmd['alarms']=[dict(id=i,delay_ns=1) for i in range(33)]
        elif kind=='unknown_cancel':cmd['cancel_alarms']=[999]
        elif kind=='bool_cancel':cmd['cancel_alarms']=[True]
        elif kind=='new_cancel':cmd.update(alarms=[dict(id=0,delay_ns=1)],cancel_alarms=[0])
        elif kind=='cancel_duplicate':cmd['cancel_alarms']=[0,0]
        elif kind=='cancel_fired':cmd['cancel_alarms']=[1]
        elif kind=='id_reuse_fired':cmd['alarms']=[dict(id=1,delay_ns=1)]
        elif kind=='id_reuse_cancelled':cmd['alarms']=[dict(id=0,delay_ns=1)]
        elif kind=='lifetime_limit':cmd['alarms']=[dict(id=32,delay_ns=1)]
        elif kind=='pending_finish':cmd.update(submissions=[],finish=True)
        elif kind=='finish_and_arm':cmd.update(submissions=[],finish=True,alarms=[dict(id=2,delay_ns=1)],cancel_alarms=[0])
        elif kind=='invalid_node_cancel':cmd.update(submissions=[dict(put,address=True)],cancel_alarms=[0])
        else:raise AssertionError(kind)
        save(folder/'invalid-command.json',cmd)
        tail,_=child.communicate(json.dumps(cmd)+'\n',timeout=10)
        (folder/'stdout.log').write_text(''.join(stages)+tail)
        assert child.returncode==1
    result=read(folder/'result.json')
    assert not result['passed'] and result['execution_error']
    assert not result['transactions']['operations'] and not result['session']['registrations']
    assert not result['transactions']['transport']['source_packets']
    if kind in two_stage:
        a={row['id']:row for row in result['session']['control_alarms']}
        if kind=='id_reuse_cancelled':assert a[0]['cancelled_ns']==0
        elif kind!='lifetime_limit':assert a[0]['cancelled_ns']==-1 and a[0]['fired_ns']==-1
    else:assert 'control_alarms' not in result['session']
    return dict(kind=kind,rejected=True,atomic_no_work_registered=True,error=result['execution_error'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('backend','old-backend','topology','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();a.output.mkdir(exist_ok=False)
    protocol=read(ROOT/'examples/scaleout_rdma/rdma.json')
    protocol['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=10000,wire_headers=True)
    base=read(ROOT/'examples/scaleout_rdma_transactions/control-alarm.json');base['operations']=[]
    old=exercise('old-default','default',a.old_backend,a.topology,protocol,a.output)
    cases={}
    for mode in ('default','legacy_gpu','control_held','control_cancel','busy_gpu','idle_alarm','zero_alarm','timer_only','cancel_and_finish','pending_wait','source_failed'):
        cases[mode]=exercise(mode,mode,a.backend,a.topology,protocol,a.output,host_delay=mode=='default')
    assert (a.output/'old-default/result.json').read_bytes()==(a.output/'default/result.json').read_bytes()
    times=lambda r:{row['id']:row for row in r['local_work']}
    assert times(cases['legacy_gpu'])[1]['completed_ns']==100000
    assert times(cases['control_held'])[1]['completed_ns']==6036
    assert times(cases['control_cancel'])[1]['completed_ns']==6036
    assert cases['control_cancel']['finish_ns']==12060
    assert cases['busy_gpu']['session']['control_alarms'][0]['fired_ns']==20000
    assert times(cases['busy_gpu'])[50]['completed_ns']==100000
    assert cases['idle_alarm']['transactions']['operations'][0]['published_ns']==20000
    assert cases['zero_alarm']['session']['control_alarms'][0]['fired_ns']==0
    assert len([e for e in cases['zero_alarm']['session']['events'] if 'alarm' in e])==2
    assert cases['timer_only']['finish_ns']==20000 and not cases['timer_only']['local_work']
    assert cases['cancel_and_finish']['finish_ns']==20000
    assert times(cases['pending_wait'])[50]['completed_ns']==26036
    kinds=('bool_id','negative_id','large_id','bool_delay','negative_delay','cutoff','beyond_cutoff','duplicate','wrong_fields','wrong_list','over_budget',
           'unknown_cancel','bool_cancel','new_cancel','cancel_duplicate','cancel_fired','id_reuse_fired','id_reuse_cancelled','lifetime_limit','pending_finish','finish_and_arm','invalid_node_cancel')
    invalid=[native_invalid(k,a.backend,a.topology,protocol,base,a.output) for k in kinds]
    save(a.output/'verification.json',dict(passed=True,default_complete_result_byte_identical=True,
        deliberate_host_delay_ms=20,functional_conditions=list(cases),native_invalid=invalid,
        legacy_copy_ns=100000,control_copy_ns=6036,old_failed_source_lease_retained=True,
        same_native_clock=True,hardware_calibrated=False))
    print(json.dumps(dict(functional_conditions=len(cases),native_rejections=len(invalid),default_byte_identical=True)))


if __name__=='__main__':main()
