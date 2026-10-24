"""Offline receipt, documentation, syntax, dependency and public-help checks."""
import argparse
import ast
from pathlib import Path
import re
import shutil
import subprocess
import sys
from moe_ub_cosim.context_transactions import check
from fixtures.context_transactions.stage import load,save,sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--repository',type=Path,required=True);p.add_argument('--stage',type=Path,required=True);a=p.parse_args()
    repo=a.repository.resolve();s=a.stage.resolve();negative=s/'receipt-guards';negative.mkdir();rejected=[]
    for kind in ('input_roles','mapping_derivation','plan_hash'):
        case=negative/kind;case.mkdir();plan=case/'plan';run=case/'run'
        shutil.copytree(s/'plan',plan);run.mkdir()
        for name in ('inputs.json','result.json'):shutil.copyfile(s/'healthy'/name,run/name)
        if kind=='input_roles':
            receipt=load(run/'inputs.json');x,y=receipt['command'][1:3]
            receipt['hashes'][x],receipt['hashes'][y]=receipt['hashes'][y],receipt['hashes'][x];save(run/'inputs.json',receipt)
        else:
            mapping=load(plan/'bindings.json');mapping['bindings'][0]['gin_connection']=99;save(plan/'bindings.json',mapping)
            if kind=='mapping_derivation':
                manifest=load(plan/'manifest.json');manifest['files']['bindings.json']=sha(plan/'bindings.json');save(plan/'manifest.json',manifest)
        try:check(plan,run,case/'unexpected.json')
        except ValueError:rejected.append(kind)
        else:raise AssertionError('invalid receipt/plan accepted: '+kind)
        assert not (case/'unexpected.json').exists()
    checked=[]
    for directory in ('python/moe_ub_cosim','fixtures/context_transactions'):
        for source in (repo/directory).rglob('*.py'):ast.parse(source.read_text(),filename=str(source));checked.append(str(source.relative_to(repo)))
    links=[]
    for rel in ('examples/scaleout_context_transactions/README.md','fixtures/context_transactions/README.md'):
        source=repo/rel
        for target in re.findall(r'\]\(([^)]+)\)',source.read_text()):
            if '://' in target or target.startswith('#'):continue
            target=target.split('#')[0];assert (source.parent/target).exists(),(rel,target);links.append(target)
    commands=[]
    for label,argv in [('deps',['bash','scripts/check_deps.sh']),
        ('plan-help',[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-context-plan','--help']),
        ('check-help',[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-context-check','--help'])]:
        with (s/(label+'.stdout')).open('w') as out,(s/(label+'.stderr')).open('w') as err:
            r=subprocess.run(argv,cwd=repo,stdout=out,stderr=err,timeout=55)
        commands.append(dict(command=argv,cwd=str(repo),exit_code=r.returncode));assert r.returncode==0,label
    save(s/'checks.json',dict(passed=True,receipt_tamper_rejected=rejected,syntax_files=len(checked),links=len(links),commands=commands))


if __name__=='__main__':main()
