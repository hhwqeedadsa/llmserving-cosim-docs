"""Relocate an actual multi-Decode multi-call run and reject forged independent lifecycles."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from moe_ub_cosim.scaleout_verify import verify


def load(path):return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def hashes(path):return {str(p.relative_to(path)):sha(p) for p in path.rglob('*') if p.is_file()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run=args.run.resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    before=hashes(run);moved=out/'relocated';shutil.copytree(run,moved);clean=verify(moved)
    config=load(moved/'input.json');manifest_path=moved/config['route_replay']['manifest']['path']
    paths=[moved/name for name in ('input.json','frontend.json','result.json','whole_call_report.json')]+[manifest_path]
    originals={path:path.read_bytes() for path in paths};f=load(moved/'frontend.json')
    assert config['whole_call_replication']['schema_version']=='moe-whole-call-replication/4'
    assert set(f['whole_call_replication']['calls'])=={'target','healthy'}
    healthy=f['whole_call_replication']['calls']['healthy'];replicas={p['replica_request'] for p in healthy['request_pairs']}
    assert healthy['attempts']['replica']['submitted_ns'] is None
    mutations=('target_deadline','healthy_deadline','swapped_replica_groups','missing_call','timer_replacement',
        'healthy_fake_execution','healthy_fake_ITL','healthy_borrowed_deadline','healthy_early_result',
        'healthy_KV_release','target_KV_release','missing_final_step','intermediate_as_result','early_second_step','KV_release_at_first_step',
        'unknown_source_upgraded_to_published')
    rejected=[]
    try:
        for name in mutations:
            c=deepcopy(config);ff=deepcopy(f);r=load(moved/'result.json');manifest=load(manifest_path)
            report=ff['whole_call_replication'];call=report['calls']['healthy']
            if name=='target_deadline':c['whole_call_replication']['calls'][0]['hedge_after_ns']+=1
            elif name=='healthy_deadline':c['whole_call_replication']['calls'][1]['hedge_after_ns']=70000
            elif name=='swapped_replica_groups':
                c['whole_call_replication']['calls'][0]['replica_group']=3
                c['whole_call_replication']['calls'][1]['replica_group']=1
            elif name=='missing_call':del report['calls']['healthy']
            elif name=='timer_replacement':r['service']['control_wakeup']['events'][-1]['deadline_ns']+=1
            elif name=='healthy_fake_execution':call['attempts']['replica']['submitted_ns']=70000
            elif name=='healthy_fake_ITL':next(q for q in ff['requests'] if q['id'] in replicas)['intertoken_ns']=[1]
            elif name=='healthy_borrowed_deadline':report['configuration']['calls'][1]['hedge_after_ns']=70000
            elif name=='healthy_early_result':call['result_ready_ns']-=1
            elif name in ('healthy_KV_release','target_KV_release'):
                group=3 if name.startswith('healthy') else 1
                next(q for q in ff['standby_memory']['releases'] if q['group_index']==group)['time_ns']-=1
            elif name=='missing_final_step':report['calls']['target']['attempts']['primary']['waves'].pop()
            elif name=='intermediate_as_result':
                a=report['calls']['target']['attempts']['primary']
                a['finished_ns']=a['waves'][0]['finished_ns'];report['calls']['target']['result_ready_ns']=a['finished_ns']
            elif name=='early_second_step':report['calls']['target']['attempts']['primary']['waves'][1]['submitted_ns']-=1
            elif name=='KV_release_at_first_step':
                a=report['calls']['target']['attempts']['replica']
                next(q for q in ff['standby_memory']['releases'] if q['group_index']==1)['time_ns']=a['waves'][0]['finished_ns']
            else:
                # Keep the reference hash consistent: reject the unsupported
                # source claim itself, not merely a stale checksum.
                manifest['source']['kind']='published_model_trace'
                manifest_path.write_text(json.dumps(manifest)+'\n')
                c['route_replay']['manifest']['sha256']=sha(manifest_path)
            for path,value in ((moved/'input.json',c),(moved/'frontend.json',ff),(moved/'result.json',r),
                               (moved/'whole_call_report.json',report)):
                path.write_text(json.dumps(value)+'\n')
            try:verify(moved)
            except ValueError as error:rejected.append(dict(case=name,error=str(error)))
            else:raise AssertionError('forged independent lifecycle accepted: '+name)
            finally:
                for path,raw in originals.items():
                    if path.read_bytes()!=raw:path.write_bytes(raw)
        assert len(rejected)==len(mutations) and before==hashes(run)==hashes(moved)
        result=dict(passed=True,relocated_public_verification=clean,rejected_mutations=rejected,
            original_files_unchanged=True,restored_copy_identical=True,new_network_executions=0)
        (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(dict(passed=True,rejected=len(rejected),relocated=True)))
    finally:
        for path,raw in originals.items():
            if path.read_bytes()!=raw:path.write_bytes(raw)


if __name__=='__main__':main()
