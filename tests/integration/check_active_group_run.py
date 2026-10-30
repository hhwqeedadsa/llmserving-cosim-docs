"""Check a relocated actual active-group service and reject forged admission."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from moe_ub_cosim.scaleout_verify import verify


def hashes(path):
    return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run=a.run.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    before=hashes(run);moved=out/'relocated';shutil.copytree(run,moved)
    result=verify(moved);c=json.loads((moved/'input.json').read_bytes());f=json.loads((moved/'frontend.json').read_bytes())
    assert c['route_replay']['request_layout']=='sparse_active_groups/1' and f['admitted_group_indices']==[0]
    r=json.loads((moved/'result.json').read_bytes());assert r['gpu_count']==128
    assert [w['graph_id'] for w in r['service']['waves']]==['g0.w0']
    primary=set(c['parallel']['groups'][0]);standby=set(c['parallel']['groups'][1])
    assert len(primary)==len(standby)==64 and not primary&standby
    assert all(m['src'] in primary and m['dst'] in primary for m in r['service_transport']['messages'])
    assert all(s['source_payload_bytes']==0 for s in r['service_transport']['native']['streams'] if s['src'] in standby)
    paths=[moved/'input.json',moved/'frontend.json'];original={p:p.read_bytes() for p in paths};rejected=[]
    try:
        for name in ('extra_admission','missing_admission','inactive_wave','fake_request','legacy_layout'):
            cc,ff=deepcopy(c),deepcopy(f)
            if name=='extra_admission':ff['admitted_group_indices']=[0,1]
            elif name=='missing_admission':ff.pop('admitted_group_indices')
            elif name=='inactive_wave':
                ff['history'][0]['group_index']=1
            elif name=='fake_request':ff['requests'].append(dict(ff['requests'][0],id=1))
            else:cc['route_replay']['request_layout']='sparse_explicit/1'
            (moved/'input.json').write_text(json.dumps(cc));(moved/'frontend.json').write_text(json.dumps(ff))
            try:verify(moved)
            except ValueError as error:rejected.append(dict(case=name,error=str(error)))
            else:raise AssertionError('forged group admission accepted: '+name)
            finally:
                for path,raw in original.items():path.write_bytes(raw)
        assert len(rejected)==5 and before==hashes(run)==hashes(moved)
        (out/'verification.json').write_text(json.dumps(dict(passed=True,public_verification=result,
            relocated=True,new_network_executions=0,standby_data_bytes=0,originals_unchanged=True,rejected_mutations=rejected),indent=2)+'\n')
        print(json.dumps(dict(passed=True,rejected=5,standby_data_bytes=0)))
    finally:
        for path,raw in original.items():path.write_bytes(raw)


if __name__=='__main__':main()
