"""Bounded archive and separate restore stages; no dependency build/download."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[2]
def digest(data):return hashlib.sha256(data).hexdigest()
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--stage',choices=['pack','restore'],required=True);a=p.parse_args()
    run=a.runtime.resolve();out=run/'checkpoint';archive=out/'checkpoint.tar.gz'
    if a.stage=='pack':
        out.mkdir();entries={}
        def add(path,name):
            assert name not in entries and not path.is_symlink()
            entries[name]=(path.read_bytes(),path.stat().st_mode & 0o777)
        for name in ['before','after','edges']:
            for f in sorted((run/name).rglob('*')):
                if f.is_file():add(f,'run/'+str(f.relative_to(run)))
        for f in sorted((run/'build/bin').iterdir()):add(f,'run/build/bin/'+f.name)
        for f in sorted(run.glob('*.json')):add(f,'run/'+f.name)
        add(run/'audit-initial.py','run/audit-initial.py')
        for f in sorted((run/'build').glob('*.json')):add(f,'run/build/'+f.name)
        for folder in sorted(run.glob('*-monitor')):
            for f in sorted(folder.iterdir()):
                if f.is_file():add(f,'run/'+str(f.relative_to(run)))
        for folder in sorted(run.glob('regression-*')):
            f=folder/'verification.json'
            if f.exists():add(f,'run/'+str(f.relative_to(run)))
        for f in sorted((ROOT/'python/moe_ub_cosim').glob('*.py')):
            add(f,'source/python/moe_ub_cosim/'+f.name)
            data=f.read_bytes() if f.name!='scaleout_transactions.py' else subprocess.check_output(
                ['git','show','9038e44:python/moe_ub_cosim/scaleout_transactions.py'],cwd=ROOT)
            entries['source-before/python/moe_ub_cosim/'+f.name]=(data,0o644)
        for folder in ['backend','fixtures/transaction_effect_order','examples/scaleout_rdma',
                       'examples/scaleout_rdma_streams','examples/scaleout_rdma_transactions']:
            for f in sorted((ROOT/folder).rglob('*')):
                if f.is_file() and f.suffix in ['.py','.cc','.hh','.json','.md']:
                    add(f,'source/'+str(f.relative_to(ROOT)))
        add(ROOT/'deps.lock.json','source/deps.lock.json')
        old=Path('/dev/shm/moe-persistent-recovery-20261024/updated-entries/bin/ns3.42-moe-rdma-transaction-probe')
        add(old,'runtimes/before/'+old.name)
        libs=sorted(Path('/dev/shm/moe-fault-20261021/native/output/lib').glob('libns3*.so'))
        assert len(libs)==8
        for f in libs:add(f,'lib/'+f.name)
        total=sum(len(b) for b,_ in entries.values());assert total<64*2**20,total
        manifest={name:dict(bytes=len(b),sha256=digest(b),mode=mode) for name,(b,mode) in entries.items()}
        payload=json.dumps(manifest,indent=2,sort_keys=True).encode()+b'\n'
        with tarfile.open(archive,'w:gz',compresslevel=6) as tar:
            for name,(b,mode) in sorted({**entries,'manifest.json':(payload,0o644)}.items()):
                info=tarfile.TarInfo(name);info.size=len(b);info.mode=mode;tar.addfile(info,io.BytesIO(b))
        assert archive.stat().st_size<24*2**20
        (out/'pack.json').write_text(json.dumps(dict(members=len(entries)+1,plain_bytes=total+len(payload),
            archive_bytes=archive.stat().st_size,sha256=digest(archive.read_bytes()),
            baseline_python_validator='9038e44; other Python modules unchanged by this fix'),indent=2)+'\n')
        print('packed',len(entries)+1,'members',archive.stat().st_size,'bytes')
    else:
        restored=out/'restored';restored.mkdir()
        with tarfile.open(archive,'r:gz') as tar:
            manifest=json.load(tar.extractfile('manifest.json'));members=tar.getmembers()
            assert set(m.name for m in members)==set(manifest)|{'manifest.json'}
            assert sum(m.size for m in members)<64*2**20
            for m in members:
                name=Path(m.name);assert m.isfile() and not name.is_absolute() and '..' not in name.parts
                b=tar.extractfile(m).read()
                if m.name!='manifest.json':
                    assert len(b)==manifest[m.name]['bytes'] and digest(b)==manifest[m.name]['sha256']
                target=restored/name;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(b);target.chmod(m.mode)
        assert all(digest((restored/n).read_bytes())==r['sha256'] for n,r in manifest.items())
        (out/'restore.json').write_text(json.dumps(dict(passed=True,members=len(members),
            all_payload_sha256_verified=True,restored=str(restored)),indent=2)+'\n')
        print('restored and verified',len(members),'members')
if __name__=='__main__':main()
