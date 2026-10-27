"""Source-bound inputs and accepted O3 runtime; no test or measurement drivers."""
from copy import deepcopy
from pathlib import Path
import shutil

from experiments.ecmp_baseline_audit.model import load, save, sha
from experiments.ecmp_baseline_audit.checkpoint import admit

CONDITIONS = ('single','dual','quad')
ARCHIVE_SHA = '90de588ca3de8b98eb474147e8b5e04a2475112c53ed2755aa53b0df40f6c03b'
RUNTIME_SHA = 'e0a6015eb54f6cd9f5ca77cc9c2ff22c9fe1389d52e58fc55ca5b58627d43ac6'


def runtime_check(stage, repo=None):
    identity = load(stage/'runtime-identity.json')
    assert sha(stage/'runtime-identity.json') == RUNTIME_SHA
    assert len(identity['files']) == 10 and len(identity['sources']) == 45
    assert all(sha(stage/'runtime'/name) == digest for name,digest in identity['files'].items())
    if repo is not None: assert all(sha(repo/name) == digest for name,digest in identity['sources'].items())


def prepare(repo, stage):
    files = admit(repo/'runs/ecmp_baseline_audit_20261027/checkpoint.tar.gz',ARCHIVE_SHA)
    (stage/'observed').mkdir()
    (stage/'truth').mkdir()
    copied = {}
    for name,raw in files.items():
        destination = None
        if name in ('run/observed/source.json','run/observed/intake.json','run/observed/baseline-topology-config.json'):
            destination = name.removeprefix('run/')
        for batch in (4,8):
            if name in (f'run/observed/batch{batch}/input.json',f'run/observed/batch{batch}/plan.json'):
                destination = name.removeprefix('run/')
        if name.startswith('run/truth/routes/'):
            destination = name.removeprefix('run/')
        if destination:
            path = stage/destination;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            copied[destination] = sha(path)
    del files
    intake = {r['name']:r for r in load(stage/'observed/intake.json')['files']}
    source = load(stage/'observed/source.json')
    assert len(source['selected']) == 8
    for row in source['selected']:
        assert intake[row['name']]['eligible_offline'] and intake[row['name']]['origin_directory_user_confirmed']
        assert sha(stage/'truth/routes'/row['name']) == intake[row['name']]['sha256'] == row['sha256']
        assert load(stage/'truth/routes'/row['name'])[1]['3'] == [row['choices']]
    for name,cores,rate in zip(CONDITIONS,(1,2,4),(100,50,25)):
        cfg = deepcopy(load(stage/'observed/baseline-topology-config.json'))
        cfg['fabric'].update(cores_per_group=cores,agg_uplinks=cores,core_wiring='full_mesh')
        cfg['links']['agg_core']['rate_bps'] = rate*10**9
        assert cores*cfg['links']['agg_core']['rate_bps'] == 100*10**9
        protocol = load(repo/'examples/modern_deepep_native/rdma.json')
        protocol['payload_bytes'] = 2000
        ecn = next(iter(protocol['ecn_by_rate'].values()))
        protocol['ecn_by_rate'] = {str(r*10**9):deepcopy(ecn) for r in (100,50,25)}
        assert protocol['seed'] == 11 and ecn == dict(kmin_bytes=32768,kmax_bytes=131072,pmax=1.0)
        save(stage/f'observed/{name}-topology-config.json',cfg)
        save(stage/f'observed/{name}-protocol.json',protocol)
    accepted = repo/'runs/optimized_host_cost_20261027/stage'
    assert sha(accepted/'runtime-identity.json') == RUNTIME_SHA
    shutil.copytree(accepted/'runtime',stage/'runtime')
    shutil.copyfile(accepted/'runtime-identity.json',stage/'runtime-identity.json')
    runtime_check(stage,repo)
    save(stage/'preparation.json',dict(passed=True,input_archive_sha256=ARCHIVE_SHA,copied=copied,
        runtime_identity_sha256=RUNTIME_SHA,platform_commit='9a8ce2c',new_builds=0,new_networks=0))


if __name__ == '__main__':
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('--repository',type=Path,required=True)
    p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args();prepare(a.repository.resolve(),a.stage.resolve())
