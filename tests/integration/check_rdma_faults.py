"""Public CLI fault/capture gate: normal, recovering, stalled and service paths."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys


ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-json',type=Path,required=True)
    parser.add_argument('--protoc',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(exist_ok=False)
    backend=read(args.backend_json);commands=[]
    def cli(name,argv,success=True):
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,argv)]
        with (out/(name+'.stdout')).open('w') as stdout,(out/(name+'.stderr')).open('w') as stderr:
            r=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=90)
        commands.append(dict(name=name,argv=command,cwd=str(ROOT),exit_code=r.returncode,expected_success=success))
        save(out/'commands.json',commands)
        assert (r.returncode==0)==success, (name,r.returncode)
    examples=ROOT/'examples/scaleout_rdma'
    cli('topology',['topology','--config',examples/'small-clos-100g.json','--output',out/'topology'])
    protocol=read(examples/'rdma.json')
    capture=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000)
    message=[dict(id=50000,src=0,dst=4,bytes=4000,plane=0,ready_ns=0)]
    save(out/'messages.json',message)
    def rule(kind,sequence,count=1,node=None):
        return dict(node=(4 if kind=='data' else 0) if node is None else node,port=0,message_id=50000,
            kind=kind,sequence=sequence,count=count,start_ns=0,end_ns=5000000)
    cases=[('plain',[],True,None),('captured',[],True,capture),
        ('middle',[rule('data',1000)],True,capture),
        ('tail',[rule('data',3000)],False,capture),
        ('early-ack',[rule('ack',1000)],True,capture),
        ('final-ack',[rule('ack',4000)],False,capture),
        ('lost-nack',[rule('data',1000),rule('nack',1000)],False,capture),
        ('repeat-middle',[rule('data',1000,count=2)],False,capture),
        ('switch-first',[rule('data',0,node=8)],True,capture),
        ('capture-bounded',[],True,dict(capture,max_events=3)),
        ('window-miss',[dict(rule('data',1000),start_ns=4000000)],True,capture)]
    reports={}
    for name,rules,success,cap in cases:
        config=deepcopy(protocol)
        if cap is not None:config['packet_capture']=cap
        if rules:config['faults']=rules
        save(out/(name+'.json'),config)
        cli(name,['rdma-probe','--topology',out/'topology','--config',out/(name+'.json'),
            '--messages',out/'messages.json','--backend',backend['probe'],'--output',out/name],success)
        cli(name+'-diagnose',['rdma-diagnose','--run',out/name,'--output',out/(name+'-diagnosis.json')])
        reports[name]=read(out/name/'result.json')
    def strip_observer(result):
        result=deepcopy(result);result.pop('packet_capture',None)
        for m in result['messages']:
            m.pop('completion_state',None);m.pop('receiver_contiguous_bytes',None)
            m.pop('source_unique_data_payload_bytes',None);m.pop('first_packet_uid',None)
        return result
    assert strip_observer(reports['plain'])==strip_observer(reports['captured'])
    assert strip_observer(reports['plain'])==strip_observer(reports['capture-bounded'])
    assert reports['capture-bounded']['packet_capture']['complete'] is False
    assert reports['capture-bounded']['packet_capture']['omitted_events']>0
    for name in ('middle','tail','early-ack','final-ack'):
        assert reports[name]['fault_injection']['drop_counts']==[1]
    assert reports['lost-nack']['fault_injection']['drop_counts']==[1,1]
    assert reports['repeat-middle']['fault_injection']['drop_counts']==[2]
    assert reports['switch-first']['fault_injection']['drop_counts']==[1]
    assert reports['window-miss']['fault_injection']['drop_counts']==[0]
    assert reports['middle']['messages'][0]['source_data_payload_bytes']>4000
    assert reports['tail']['messages'][0]['receiver_contiguous_bytes']==3000
    assert reports['final-ack']['messages'][0]['completion_state']=='awaiting_sender_ack'
    assert reports['final-ack']['messages'][0]['receiver_contiguous_bytes']==4000
    # Real frontend feedback: one data fault recovers, final ACK loss blocks SEND
    # while the same transfer's RECV and a downstream expert can complete.
    config=read(examples/'service.json');config['model']['layers']=1;config['workload']['decode_steps']=2
    save(out/'service.json',config)
    service_reports={}
    for name,kind,success in [('service-normal',None,True),('service-middle','data',True),('service-final-ack','ack',False)]:
        p=deepcopy(protocol);p['packet_capture']=capture
        if kind:
            p['faults']=[dict(rule(kind,1000 if kind=='data' else 2048),message_id=0,node=1 if kind=='data' else 0)]
        save(out/(name+'.json'),p)
        cli(name,['rdma-run','--topology',out/'topology','--protocol',out/(name+'.json'),
            '--config',out/'service.json','--backend',backend['service'],'--protoc',args.protoc,'--output',out/name],success)
        cli(name+'-diagnose',['rdma-diagnose','--run',out/name,'--output',out/(name+'-diagnosis.json')])
        service_reports[name]=read(out/name/'result.json')
        if kind:assert service_reports[name]['fault_injection']['drop_counts']==[1]
    stalled=service_reports['service-final-ack']
    f=next(m for m in stalled['messages'] if m['id']==0)
    assert f['bytes']==2048 and f['receive_ready_ns'] is not None and f['ack_ns'] is None
    cb=next(m for m in stalled['service']['message_callbacks'] if m['id']==0)
    assert cb['recv_callback_ns'] is not None and cb['send_callback_ns'] is None
    save(out/'summary.json',dict(passed=True,probe_cases=len(cases),service_cases=3,
        recovered_payload_bytes=reports['middle']['messages'][0]['source_data_payload_bytes'],
        capture_invariance=True,bounded_capture_invariance=True,failed_service_kept_result_and_frontend=True))
    print('RDMA fault, capture and frontend gates passed')


if __name__=='__main__':main()
