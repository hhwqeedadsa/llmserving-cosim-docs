"""Four public native SimAI conditions, including an expected unrecoverable blackhole."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def read(p):return json.loads(p.read_bytes())
def call(root,name,args):
    argv=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,args)]
    with (root/(name+'.stdout')).open('w') as out,(root/(name+'.stderr')).open('w') as err:
        result=subprocess.run(argv,stdout=out,stderr=err,timeout=90)
    (root/(name+'.command.json')).write_text(json.dumps(dict(argv=argv,exit_code=result.returncode),indent=2)+'\n')
    return result.returncode


def check(output,runtime,backend,protoc):
    assert not output.exists();output.mkdir(parents=True);repo=Path(__file__).resolve().parents[2];example=repo/'examples/simai_severe_loss'
    assert call(output,'topology',['topology','--config',repo/'examples/simai_service/topology.json','--output',output/'topology'])==0
    checked={}
    for case in ('normal','half-burst','blackout','persistent'):
        run=output/case
        code=call(output,'run-'+case,['simai-run','--config',example/'service.json','--topology',output/'topology',
            '--protocol',example/(case+'.json'),'--runtime',runtime,'--backend',backend,'--protoc',protoc,'--output',run])
        r=read(run/'result.json');scenario=read(run/'scenario.json');assert scenario['physical_gpu_count']==64
        assert scenario['ep_groups'][0]['ep']==64 and scenario['ep_groups'][0]['experts_per_rank_per_layer']==4
        assert scenario['source_requests']==2 and r['packet_capture']['complete']
        if case=='persistent':
            assert code!=0 and not r['passed'] and r['failure']['reason']=='recovery_retry_exhausted'
            assert scenario['outcome']['state']=='failed'
        else:
            assert code==0 and r['passed'] and scenario['outcome']['state']=='completed'
            assert read(run/'verification.json')['remote_payload_bytes']==917504
        if case!='normal':
            cfg=read(run/'rdma.json')['data_loss'];total=window=dropped=0
            for row in r['packet_capture']['events']:
                p=dict(zip(r['packet_capture']['columns'],row))
                if p['node']>=64 or p['ip_protocol']!=17 or p['event'] not in (1,2):continue
                total+=1;expected=False
                if cfg['start_ns']<=p['time_ns']<cfg['end_ns']:
                    expected=(window+cfg['offset'])%cfg['denominator']<cfg['numerator'];window+=1
                assert (p['event']==2)==expected
                dropped+=p['event']==2
            assert (total,window,dropped)==tuple(r['data_loss'][k] for k in ('eligible_destination_data_packets','window_packets','dropped_packets'))
        assert call(output,'arrivals-'+case,['rdma-arrivals','--run',run,'--graph','g0.w0','--layer','0','--phase','dispatch','--output',output/(case+'-arrivals')])==0
        a=read(output/(case+'-arrivals')/'summary.json');assert a['slice']['complete']==(case!='persistent')
        checked[case]=dict(passed=True,expected_native_success=case!='persistent',native_exit=code,finish_ns=r['finish_ns'],
            captured_packets=len(r['packet_capture']['events']),data_loss=r.get('data_loss'),accepted_slice_bytes=a['slice']['accepted_bytes'])
    assert checked['normal']['finish_ns']==47603
    assert checked['blackout']['finish_ns']>checked['normal']['finish_ns']
    result=dict(passed=True,conditions=checked,new_native_runs=4,expected_failures=1,scope='artificial EP64 functional gate, not real-route scale-out research')
    (output/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--backend',type=Path,required=True);p.add_argument('--protoc',default='protoc');a=p.parse_args()
    check(a.output.resolve(),a.runtime.resolve(),a.backend.resolve(),a.protoc)
