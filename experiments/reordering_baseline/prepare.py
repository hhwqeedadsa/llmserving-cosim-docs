"""Strict prior research archive admission; freeze inputs without starting a network."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path,PurePosixPath
import subprocess
import tarfile


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','archive','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();assert not a.output.exists()
    platform=subprocess.check_output(['git','rev-parse','fa6f001'],cwd=repo,text=True).strip()
    subprocess.run(['git','diff','--exit-code',platform,'--','python','backend','deps.lock.json','.gitmodules'],cwd=repo,check=True)
    expected='954dfd8106dcc467e10e3d527aa3269166a6cbc8bd6808c1ee2a4b565df2d05b'
    assert sha(a.archive)==expected and a.archive.stat().st_size<32*2**20
    with tarfile.open(a.archive,'r:gz') as ar:
        ms=ar.getmembers();assert len(ms)<=2048;names=set();size=0
        for m in ms:
            path=PurePosixPath(m.name);assert m.isfile() and not path.is_absolute() and '..' not in path.parts
            assert str(path)==m.name and '\\' not in m.name and m.name not in names and 0<=m.size<=16*2**20
            names.add(m.name);size+=m.size
        assert size<128*2**20
        manifest=json.load(ar.extractfile('manifest.json'));assert set(manifest['files'])==names-{'manifest.json'}
        for name,e in manifest['files'].items():
            b=ar.extractfile(name).read();assert len(b)==e['bytes'] and hashlib.sha256(b).hexdigest()==e['sha256']
        out=a.output;out.mkdir();obs=out/'observed';obs.mkdir();baseline=out/'baseline';baseline.mkdir()
        def copy(name,to):to.parent.mkdir(parents=True,exist_ok=True);to.write_bytes(ar.extractfile(name).read())
        for filename in ('g0.w0.json','g0.w1.json','topology.json','selective-plan.json'):
            copy('evidence/inputs/observed/'+filename,obs/filename)
        copy('evidence/topology/topology.json',baseline/'topology.json')
        for name in ('healthy','live-delayed','live-instant'):
            copy('evidence/inputs/observed/'+name+'-protocol.json',obs/(name+'-protocol.json'))
            for filename in ('work.json','mapping.json'):copy('evidence/planned/'+name+'/'+filename,baseline/name/filename)
            copy('evidence/network/'+name+'/result.json',baseline/name/'result.json')
        for name in manifest['files']:
            for prefix in ('evidence/inputs/truth/','evidence/inputs/source-inputs/'):
                if name.startswith(prefix):copy(name,out/name[len('evidence/inputs/'):])
    original=load(obs/'selective-plan.json');changed=deepcopy(original)
    changed['transport_recovery']['reordering']=dict(mode='nack_grace/1',wait_ns=16000,max_events=30000)
    save(obs/'grace-plan.json',changed)
    save(obs/'index.json',dict(schema_version='reordering-baseline/1',platform_commit=platform,waves=['g0.w0.json','g0.w1.json'],
        plan='grace-plan.json',cases=['healthy','live-delayed','live-instant'],files={p.name:sha(p) for p in sorted(obs.iterdir())},
        scope='four fixed MMLU requests, two layers/two steps legacy contribution-copy and declared compute; no modern calibration or holdout'))
    save(out/'provenance.json',dict(archive_sha256=expected,members_verified=len(ms),
        files={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()}))
    print('prior archive verified and three fixed inputs prepared; no network execution')

if __name__=='__main__':main()
