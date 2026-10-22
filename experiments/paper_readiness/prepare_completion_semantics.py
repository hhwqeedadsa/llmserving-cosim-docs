"""Change only an explicit data-microbenchmark dependency event, preserving old evidence."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

def load(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline','output'):parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args();base=args.baseline.resolve();out=args.output.resolve();assert not out.exists()
    index=load(base/'observed/index.json');assert index['config']['schema_version']=='moe-dispatch-validation/1'
    for name,item in index['artifacts'].items():assert sha(base/'observed'/name)==item['sha256']
    packet_check=load(base/'checked/verification.json');assert packet_check['passed'] and len(packet_check['cases'])==18
    baseline_identity={}
    for case in packet_check['cases']:
        path=base/'run'/case['case']/'result.json';assert sha(path)==case['result_sha256']
        baseline_identity[case['case']]=dict(path=str(path),sha256=case['result_sha256'],bytes=path.stat().st_size)
    out.mkdir();observed=out/'observed';observed.mkdir();changes=[]
    shutil.copytree(base/'truth',out/'truth')
    cfg=index['config']
    for name in index['artifacts']:
        if not name.startswith('work-'):shutil.copyfile(base/'observed'/name,observed/name)
    for condition in cfg['placement_conditions']:
        original=load(base/'observed'/f'work-{condition}.json');changed=deepcopy(original);count=0
        for work in changed['work']:
            for dependency in work['after']:
                assert dependency['event']=='acked';dependency['event']='accepted';count+=1
        assert count>0;save(observed/f'work-{condition}.json',changed)
        restored=deepcopy(changed)
        for work in restored['work']:
            for dependency in work['after']:dependency['event']='acked'
        assert restored==original
        changes.append(dict(condition=condition,dependencies=count,work=len(original['work'])))
    save(observed/'index.json',dict(config=cfg,dependency_event='accepted',
        artifacts={p.name:dict(sha256=sha(p),bytes=p.stat().st_size) for p in sorted(observed.iterdir())},
        scope='scheduling-assumption sensitivity on independent byte writes; not a GPU/modern-library optimization'))
    save(out/'baseline.json',dict(directory=str(base),observed_index_sha256=sha(base/'observed/index.json'),
        result_identities=baseline_identity,packet_verification_sha256=sha(base/'checked/verification.json'),changes=changes))
    print(json.dumps(dict(prepared=True,new_conditions=18,changed_field='after.event only')))


if __name__=='__main__':main()
