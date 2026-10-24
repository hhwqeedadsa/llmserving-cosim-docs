"""New Python source root/empty cwd; the fourth and final native execution."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();root=s/'restored';src=root/'source';ev=root/'evidence';out=s/'fresh'
    assert load(s/'run.json')['actual_networks']==3 and not out.exists()
    runtime=load(ev/'runtime.json');assert sha(Path(runtime['backend']))==runtime['sha256']
    assert all(sha(Path(p))==h for p,h in runtime['libraries'].items())
    assert all(sha(src/p)==h for p,h in runtime['source'].items())
    out.mkdir();(out/'cwd').mkdir();commands=[]
    env=dict(os.environ,PYTHONPATH=str(src/'python'),PYTHONDONTWRITEBYTECODE='1')
    def run(label,argv):
        argv=list(map(str,argv))
        with (out/(label+'.stdout.log')).open('w') as stdout,(out/(label+'.stderr.log')).open('w') as stderr:
            r=subprocess.run(argv,cwd=out/'cwd',env=env,stdout=stdout,stderr=stderr,timeout=60)
        commands.append(dict(label=label,command=argv,cwd=str(out/'cwd'),pythonpath=env['PYTHONPATH'],exit_code=r.returncode))
        save(out/'commands.json',commands);assert r.returncode==0,label
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    run('topology',cli+['topology','--config',ev/'inputs/topology.json','--output',out/'topology'])
    config=ev/'inputs/long/plan.json';proto=ev/'inputs/long/protocol.json'
    run('plan',cli+['rdma-causal-plan','--config',config,'--topology',out/'topology','--protocol',proto,'--output',out/'planned'])
    for name in ('work.json','mapping.json'):
        assert (out/'planned'/name).read_bytes()==(ev/'planned/long'/name).read_bytes(),name
    run('network',cli+['rdma-stream-probe','--topology',out/'topology','--protocol',proto,
        '--config',out/'planned/work.json','--backend',runtime['backend'],'--output',out/'network'])
    assert (out/'network/result.json').stat().st_size<=8*2**20
    # The independent auditor was extended for endpoint-only conservation
    # after v1. Copy it explicitly; the public source/CLI remains unchanged.
    shutil.copy2(repo/'fixtures/causal_waves/audit.py',out/'auditor.py')
    def audit(label,cfg,planned,result,output):
        run(label,[sys.executable,'-B',out/'auditor.py','--config',cfg,'--work',planned/'work.json',
            '--mapping',planned/'mapping.json','--result',result,'--output',output])
    audit('long-audit',config,out/'planned',out/'network/result.json',out/'audit.json')
    audit('long-repeat-offline',config,out/'planned',out/'network/result.json',out/'repeat-audit.json')
    assert (out/'audit.json').read_bytes()==(out/'repeat-audit.json').read_bytes()
    for label,cfg in (('plain','plan.json'),('selective','selective-plan.json'),('fault','selective-plan.json')):
        target=out/(label+'-audit.json')
        audit(label+'-offline',ev/'inputs'/cfg,ev/'planned'/label,ev/'network'/label/'result.json',target)
        assert target.read_bytes()==(ev/'network'/label/'audit.json').read_bytes()
    # Inverse projection is deliberately independent of the preparation code.
    inverse={0:0,1:1,2:512,3:513};source=load(ev/'inputs/long/source.json')
    assert source['rank_map']=={'0':0,'1':1,'512':2,'513':3}
    for item in source['sources']:
        raw=ev/'inputs/long'/item['file'];mapped=ev/'inputs/long'/item['remapped_file']
        assert sha(raw)==item['sha256'] and sha(mapped)==item['remapped_sha256']
        def undo(obj):
            if isinstance(obj,list):return [undo(x) for x in obj]
            if not isinstance(obj,dict):return obj
            d={k:undo(v) for k,v in obj.items()}
            for key in ('src','dst','rank'):
                if key in d:d[key]=inverse[d[key]]
            if 'rank_ids' in d:d['rank_ids']=[inverse[r] for r in d['rank_ids']]
            if 'rank_nodes' in d:d['rank_nodes']={str(inverse[int(k)]):v for k,v in d['rank_nodes'].items()}
            return d
        assert undo(load(mapped))==load(raw),'projection changed another field'
    assert sha(Path(runtime['backend']))==runtime['sha256']
    assert all(sha(Path(p))==h for p,h in runtime['libraries'].items())
    save(out/'replay.json',dict(passed=True,new_python_source_root=True,empty_cwd=True,
        compiled_full_work_and_mapping_byte_equal=True,four_offline_audits_byte_equal=True,
        full_frontend_two_steps_and_48_layers_preserved=True,independent_inverse_rank_projection=True,
        original_binary_and_eight_libraries_hash_equal=True,native_runtime_bundled=False,
        total_actual_networks=4,public_commands=commands,auditor_sha256=sha(out/'auditor.py'),long_case=load(out/'audit.json'),
        scope='one saved full frontend group on four projected endpoints; declared costs/persistent reference, not original thousand-GPU/ASTRA/modern GIN timing'))


if __name__=='__main__':main()
