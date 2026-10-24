"""Read hash-fixed prior research archive; never executes a historical driver."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile


def load(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x): p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','baseline-archive','output'): p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args(); repo=a.repository.resolve(); assert not a.output.exists()
    platform=subprocess.check_output(['git','rev-parse','cf7dd46'],cwd=repo,text=True).strip()
    subprocess.run(['git','diff','--exit-code',platform,'--','python','backend','deps.lock.json','.gitmodules'],cwd=repo,check=True)
    expected=load(repo/'experiments/causal_fault/results/20261024/closeout.json')['checkpoint']
    assert sha(a.baseline_archive)==expected['sha256'] and a.baseline_archive.stat().st_size<24*2**20
    with tarfile.open(a.baseline_archive,'r:gz') as ar:
        members=ar.getmembers(); assert len(members)<=2048
        names=set(); total=0
        for m in members:
            path=PurePosixPath(m.name)
            assert m.isfile() and not path.is_absolute() and '..' not in path.parts and str(path)==m.name and '\\' not in m.name
            assert m.name not in names and m.name not in ('','.') and 0<=m.size<=16*2**20
            names.add(m.name); total+=m.size
        assert total<96*2**20
        manifest=json.load(ar.extractfile('manifest.json')); assert set(manifest['files'])==names-{'manifest.json'}
        for name,entry in manifest['files'].items():
            data=ar.extractfile(name).read(); assert len(data)==entry['bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256']
        def read(name): return ar.extractfile(name).read()
        out=a.output; out.mkdir(); obs=out/'observed'; truth=out/'truth'; baseline=out/'baseline'
        for d in (obs,truth,baseline): d.mkdir()
        for filename in ('g0.w0.json','g0.w1.json','topology.json','selective-plan.json','healthy-protocol.json'):
            (obs/filename).write_bytes(read('inputs/observed/'+filename))
        for name in manifest['files']:
            if name.startswith('inputs/truth/'):
                (truth/Path(name).name).write_bytes(read(name))
        for new,old in (('saved-healthy','healthy-selective'),('saved-fault','failed-selective')):
            target=baseline/new; target.mkdir()
            (target/'result.json').write_bytes(read('network/'+old+'/result.json'))
            for filename in ('work.json','mapping.json'):
                (target/filename).write_bytes(read('planned/'+old+'/'+filename))
            proto='healthy-protocol.json' if new=='saved-healthy' else 'failed-protocol.json'
            (obs/(new+'-protocol.json')).write_bytes(read('inputs/observed/'+proto))
        (baseline/'topology.json').write_bytes(read('topology/topology.json'))
        (baseline/'runtime.json').write_bytes(read('runtime.json'))
        # Preserve the public original route identities independently of the
        # machine on which the previous archive was made.
        for name in manifest['files']:
            if name.startswith('source/data/moe-trace-small/'):
                target=out/'source-inputs'/name[len('source/'):];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(read(name))
    base=load(obs/'healthy-protocol.json')
    configs={'healthy':base}
    for name,time,delay in (('live-delayed',4000,12000),('live-instant',4000,0),('static-degraded',0,0)):
        proto=deepcopy(base);proto['local_failover']=dict(mode='live_ecmp/1')
        proto['link_failures']=dict(mode='switch_links/1',events=[dict(src=8,dst=10,time_ns=time,route_update_delay_ns=delay)])
        configs[name]=proto
    for name,proto in configs.items(): save(obs/(name+'-protocol.json'),proto)
    assert (baseline/'saved-healthy/work.json').read_bytes()==(baseline/'saved-fault/work.json').read_bytes()
    save(obs/'index.json',dict(schema_version='local-fault-baselines/1',platform_commit=platform,
        waves=['g0.w0.json','g0.w1.json'],plan='selective-plan.json',
        new_cases=list(configs),saved_cases=['saved-healthy','saved-fault'],
        baseline_archive_sha256=expected['sha256'],files={p.name:sha(p) for p in sorted(obs.iterdir())},
        scope='four fixed MMLU requests, two layers/two steps legacy contribution-copy and declared compute; not modern inference/calibration/holdout'))
    save(out/'provenance.json',dict(baseline_archive_sha256=expected['sha256'],baseline_members_verified=len(members),
        files={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()}))
    print(json.dumps(dict(passed=True,baseline_members_verified=len(members),actual_networks=0)))


if __name__=='__main__':main()
