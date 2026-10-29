"""Read saved actual services through public readers; never executes networks."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.run_summary import describe
from moe_ub_cosim.service_terminal import service_terminal
from moe_ub_cosim.scaleout_topology import require


def check(directory, full_check):
    directory=Path(directory)
    paths=[directory/name for name in ('input.json','result.json','frontend.json')]
    require(all(p.stat().st_size<=128*2**20 for p in paths),'saved input exceeds audit limit')
    raw=[p.read_bytes() for p in paths]
    config,result,frontend=[json.loads(v) for v in raw]
    terminal=service_terminal(result,frontend,require_complete=result['passed'])
    is_simai=config.get('schema_version')=='moe-simai-service/1'
    public=describe(directory)['outcome'] if is_simai else None
    if public is not None:
        require(all(public[k]==v for k,v in terminal.items()),'public summary differs')
        require(public['finish_ns']==result['finish_ns'],'legacy finish semantics changed')
    verified=None
    if full_check and result['passed']:
        if is_simai:
            from moe_ub_cosim.simai_verify import verify
        else:
            from moe_ub_cosim.scaleout_verify import verify
        verified=verify(directory)
        require(all(verified[k]==v for k,v in terminal.items()),'public verifier timing differs')
    mutations=[]
    if result['passed']:
        for name in ('native_wave','final_commit','history'):
            r,f=deepcopy(result),deepcopy(frontend)
            if name=='native_wave':r['service']['waves'][0]['finished_ns']+=1
            elif name=='final_commit':f['transcript'][-1]['reply']['now_ns']+=1
            else:f['history'][0]['finished_ns']+=1
            try:service_terminal(r,f,require_complete=True)
            except ValueError:mutations.append(name)
            else:raise ValueError('terminal contradiction accepted: '+name)
    require([p.read_bytes() for p in paths]==raw,'saved originals changed')
    return dict(run=str(directory),passed=True,original_passed=result['passed'],**terminal,
                public_summary_checked=public is not None,full_public_verifier_checked=verified is not None,
                rejected_mutations=mutations,input_sha256={p.name:hashlib.sha256(v).hexdigest() for p,v in zip(paths,raw)})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,action='append',required=True)
    parser.add_argument('--full-check',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=dict(schema_version='moe-service-terminal-audit/1',new_network_runs=0,
                cases=[check(path,args.full_check) for path in args.run])
    with args.output.open('x') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2);stream.write('\n')
