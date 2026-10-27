"""Independently count supplied raw files and every declared traffic wave."""
import argparse
import hashlib
import json
from pathlib import Path

from experiments.route_coverage.local_additions import receive
from experiments.route_coverage.analyze import save


def check(directory, existing_manifest, catalog, output):
    intake=json.loads((output/'intake.json').read_bytes())
    demand=json.loads((output/'traffic-demand.json').read_bytes())
    expected_manifest=json.loads(Path('experiments/route_coverage/results/20261026/coverage.json').read_bytes())['manifest_sha256']
    assert hashlib.sha256(existing_manifest.read_bytes()).hexdigest()==expected_manifest==intake['existing_manifest_sha256']
    selected={}; total_rows=total_choices=0; bad={}
    for item in intake['files']:
        path=directory/item['name'];raw=path.read_bytes();assert hashlib.sha256(raw).hexdigest()==item['sha256']
        data=json.loads(raw); errors=[]
        for step,entry in enumerate(data):
            missing=[str(i) for i in range(61) if str(i) not in entry]
            extra=[k for k in entry if k not in [str(i) for i in range(61)]]
            if missing or extra:errors.append(dict(entry_index=step,missing=missing,extra=extra))
        if not item['eligible_offline']:
            assert errors and errors==item['layer_set_errors'];bad[item['name']]=errors;continue
        assert not errors
        rows=choices=0
        for step,entry in enumerate(data):
            assert all(entry[str(i)] is None for i in range(3))
            for lid in range(3,61):
                tokens=entry[str(lid)]
                assert len(tokens)>0 and (step==0 or len(tokens)==1)
                for token in tokens:
                    assert len(token)==8 and len(set(token))==8 and all(type(e)is int and 0<=e<256 for e in token)
                    choices+=len(token);rows+=1
        assert (rows,choices)==(item['token_layer_rows'],item['expert_choices'])
        total_rows+=rows;total_choices+=choices;selected[item['name']]=data
    assert set(bad)=={'899.json','1479.json'} and len(selected)==15
    assert (total_rows,total_choices)==(145232,1161856)
    assert intake['populations']['new_user_supplied']['files']==11
    assert bad['899.json']==[dict(entry_index=127,missing=[str(i) for i in range(43,61)],extra=[])]
    assert bad['1479.json']==[dict(entry_index=128,missing=[str(i) for i in range(38,61)],extra=[])]
    checked=0
    for b in demand['batches']:
        if b['batch']==16:
            assert b['state']=='insufficient_distinct_valid_requests';continue
        values=[];unused=0
        for group,n in zip(b['groups'],b['common_decode_steps_per_group']):
            assert n==min(len(selected[name])-1 for name in group)
            assert not (set(group)&set(bad))
            unused+=sum(len(selected[name])-1-n for name in group)
            for lid in range(3,61):
                for step in range(1,n+1):
                    copies=0
                    for name in group:
                        index=demand['eligible_order'].index(name)
                        for dest in range(4):
                            if dest!=index%4 and any(dest*64<=e<(dest+1)*64 for e in selected[name][step][str(lid)][0]):copies+=1
                    values.append(copies*14336)
        assert len(values)==b['layer_step_group_waves'] and unused==b['unused_decode_steps']
        assert (min(values),max(values))==(b['dispatch_payload_bytes']['minimum'],b['dispatch_payload_bytes']['maximum'])
        assert sum(v==0 for v in values)==b['zero_remote_demand_waves']
        assert sorted(values)[(len(values)-1)//2]==b['dispatch_payload_bytes']['median']
        checked+=len(values)
    again=receive(directory,existing_manifest,catalog)
    assert again==(intake,demand)
    return dict(passed=True,independent_source_rows=total_rows,independent_expert_choices=total_choices,
        independent_demand_waves=checked,complete_files=15,new_complete_files=11,rejected_files=bad,
        rejected_files_excluded_from_all_groups=True,original_manifest_unchanged=True,
        deterministic_reanalysis=True,network_simulations=0,new_remote_identities_verified=0)


def main():
    p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--existing-manifest',type=Path,required=True);p.add_argument('--catalog-cache',type=Path,required=True)
    p.add_argument('--analysis-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();assert not a.output.exists()
    report=check(a.directory,a.existing_manifest,a.catalog_cache,a.analysis_dir);save(a.output,report)
    print('Independent intake/demand counts and deterministic replay passed:',report['independent_demand_waves'],'waves')


if __name__=='__main__':main()
