"""Audit two saved native runs and reject corrupted causal/source evidence; no reruns."""
import argparse
import json
from pathlib import Path
import shutil

from moe_ub_cosim.simai_verify import verify


def check(stage,output):
    assert not output.exists();output.mkdir(parents=True)
    normal=verify(stage/'normal');loss=verify(stage/'loss')
    assert normal['gpu_count']==loss['gpu_count']==64
    assert normal['messages']==loss['messages']==64
    assert normal['remote_payload_bytes']==loss['remote_payload_bytes']==917504
    assert normal['recovery']['timeout_retransmissions']==0
    assert loss['recovery']['timeout_retransmissions']==3 and loss['finish_ns']>normal['finish_ns']
    for name in ('normal','loss'):
        f=json.loads((stage/name/'frontend.json').read_bytes())
        assert f['history'][1]['start_ns']==f['history'][0]['finished_ns']
        a=json.loads((stage/(name+'-arrivals')/'summary.json').read_bytes())
        assert a['capture_complete'] and a['slice']['complete']
        assert a['accepted_bytes']==917504
    refused=[]
    for change in ('feedback','choices','idle-compute','accepted-bytes'):
        copy=output/change;shutil.copytree(stage/'normal',copy)
        if change=='feedback':
            path=copy/'frontend.json';v=json.loads(path.read_bytes());v['history'][1]['start_ns']+=1
        elif change in ('choices','idle-compute'):
            path=copy/'graphs/g0.w0/wave.json';v=json.loads(path.read_bytes())
            if change=='choices':v['routes'][0]['tokens'][0]['experts'][0]^=1
            else:v['rank_nodes']['1'][0]['duration_ns']=1000
        else:
            path=copy/'result.json';v=json.loads(path.read_bytes());v['messages'][0]['receiver_contiguous_bytes']-=1
        path.write_text(json.dumps(v)+'\n')
        try:verify(copy)
        except ValueError as error:refused.append(dict(case=change,error=str(error)))
        else:raise AssertionError('corrupted evidence accepted: '+change)
    result=dict(passed=True,normal=normal,loss=loss,refused=refused,
                new_network_runs=0,scope='audit existing native execution and causal/source corruption rejection')
    (output/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();check(a.stage.resolve(),a.output.resolve())
