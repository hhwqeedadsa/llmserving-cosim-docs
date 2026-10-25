"""Recompile the modified transaction object and every direct consumer."""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
from fixtures.incremental_progress.stage import load,save,sha


def prepare(repo,stage):
    old=Path('/dev/shm/moe-session-host-timing-20261024')
    previous=load(old/'runtime-identity.json')
    fixed={k:v for k,v in previous['unchanged_objects'].items() if not k.endswith('/remote_transactions.o')}
    assert all(sha(Path(path))==digest for path,digest in fixed.items())
    runtime=stage/'runtime';runtime.mkdir();(runtime/'lib').mkdir();(stage/'objects').mkdir()
    for name,digest in previous['files'].items():
        if name.startswith('lib/'):
            shutil.copyfile(old/'runtime'/name,runtime/name);assert sha(runtime/name)==digest
    for name,target in (('run-transaction-session','transaction-session'),('run-shadow-session','shadow-session'),
                        ('run-transaction-probe','transaction-probe')):
        text=(old/'runtime/run-transaction-session').read_text().replace('"$base/transaction-session"','"$base/'+target+'"')
        (runtime/name).write_text(text);(runtime/name).chmod(0o755)
    recipe=load(old/'build-commands.json')['compile'];commands={}
    for unit in ('remote_transactions','transaction_session','transaction_probe','shadow_remote_transactions','shadow_transaction_session'):
        shadow=unit.startswith('shadow_');source=unit.removeprefix('shadow_')
        command=deepcopy(recipe)
        for flag in ('-o','-MT','-MF'):
            command[command.index(flag)+1]=str(stage/'objects'/(unit+'.o'+('.d' if flag=='-MF' else '')))
        command[-1]=str(repo/'backend/rdma'/(source+'.cc'))
        if shadow:command.insert(1,'-DMOE_VERIFY_PROGRESS_UPDATES')
        commands[unit]=command
    for name,consumer,tx in (('session','transaction_session','remote_transactions'),
                             ('probe','transaction_probe','remote_transactions'),
                             ('shadow','shadow_transaction_session','shadow_remote_transactions')):
        executable={'session':'transaction-session','probe':'transaction-probe','shadow':'shadow-session'}[name]
        commands['link-'+name]=list(map(str,['c++',stage/'objects'/(consumer+'.o'),stage/'objects'/(tx+'.o'),
            *fixed,'-Wl,--no-as-needed',*sorted((runtime/'lib').glob('*.so')),'-o',runtime/executable]))
    save(stage/'build-commands.json',commands)
    source={str(path.relative_to(repo)):sha(path) for path in (repo/'backend/rdma').glob('*.hh')}
    for name in ('remote_transactions','transaction_session','transaction_probe'):
        path=repo/'backend/rdma'/(name+'.cc');source[str(path.relative_to(repo))]=sha(path)
    save(stage/'identity.json',dict(sources=source,unchanged_objects=fixed,
        libraries={str(runtime/'lib'/Path(name).name):sha(runtime/'lib'/Path(name).name) for name in previous['libraries']},
        actual_native_commit=previous['actual_native_commit'],clean_build=False,optimization='O0 unchanged'))
    shutil.copytree(old/'inputs',stage/'inputs');shutil.copytree(old/'original-routes',stage/'original-routes')
    shutil.copyfile(old/'route-provenance.json',stage/'route-provenance.json')
    original=stage/'original';original.mkdir()
    cases={'default-healthy':old/'default-healthy','timed-healthy':old/'timed-healthy',
        'wake-fault':Path('/dev/shm/moe-native-wakeup-20261024/wake-fault')}
    for label,directory in cases.items():
        target=original/label;target.mkdir()
        for name in ('initial.json','input.json','protocol.json','topology.json','result.json','modern-execution.json','control.jsonl'):
            shutil.copyfile(directory/name,target/name)
    for name in ('input','protocol'):
        shutil.copyfile(cases['wake-fault']/(name+'.json'),stage/'inputs'/('synthetic-fault'+('-protocol' if name=='protocol' else '')+'.json'))
    shutil.copyfile(Path('/dev/shm/moe-native-wakeup-20261024/four-routes/control.jsonl'),stage/'original-four-control.jsonl')
    save(stage/'inputs.json',dict(files={str(path.relative_to(stage)):sha(path)
        for root in (stage/'inputs',original,stage/'original-routes') for path in root.rglob('*') if path.is_file()},
        four_prefix_sha256=sha(stage/'original-four-control.jsonl')))


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--action',required=True);a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve()
    if a.action=='prepare':prepare(repo,stage);return
    identity=load(stage/'identity.json')
    assert all(sha(repo/name)==digest for name,digest in identity['sources'].items())
    assert all(sha(Path(name))==digest for name,digest in {**identity['unchanged_objects'],**identity['libraries']}.items())
    subprocess.run(load(stage/'build-commands.json')[a.action],cwd=repo,check=True,timeout=55)
    if a.action=='link-shadow':
        save(stage/'runtime-identity.json',dict(**identity,files={str(path.relative_to(stage/'runtime')):sha(path)
            for path in (stage/'runtime').rglob('*') if path.is_file()}))


if __name__=='__main__':main()
