"""Bounded source/evidence bundle; reuse the already strict hash-before-write reader."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
from fixtures.live_ecmp.checkpoint import restore


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')


def create(a):
    repo=a.repository.resolve();s=a.stage.resolve();files={};assert not a.output.exists()
    for prefix in ('python','backend','fixtures/nack_grace','examples/scaleout_nack_grace'):
        for p in (repo/prefix).rglob('*'):
            if p.is_file() and p.suffix in ('.py','.cc','.hh','.json','.md'):
                files['source/'+str(p.relative_to(repo))]=p
    for rel in ('fixtures/live_ecmp/checkpoint.py','fixtures/selective_repeat/audit.py','AGENTS.md','deps.lock.json',
        'docs/development.md','docs/user_guide.md','docs/record_storage.md','docs/tasks/nack_grace_spec.md',
        'docs/tasks/current.md','tests/unit/nack_grace_test.cc','tests/unit/scaleout_nack_grace_test.py',
        'tests/unit/scaleout_selective_repeat_test.py'):
        files['source/'+rel]=repo/rel
    for directory in ('inputs','topology','fresh-topology','network','native-rejections','failed-source'):
        for p in (s/directory).rglob('*'):
            if p.is_file():files['evidence/'+str(p.relative_to(s))]=p
    for p in s.iterdir():
        if p.is_file() and p.suffix in ('.json','.py'):files['evidence/'+p.name]=p
    for p in s.glob('*-monitor/*'):
        if p.is_file() and (p.parent/'probe.json').exists():files['evidence/monitors/'+str(p.relative_to(s))]=p
    assert all(not p.is_symlink() and p.stat().st_size<=16*2**20 for p in files.values())
    entries={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in files.items()}
    assert len(entries)+1<=2048 and sum(x['bytes'] for x in entries.values())<128*2**20
    # Same bounded archive envelope as the existing reader; scope metadata
    # explicitly identifies this incomplete platform phase.
    manifest=dict(schema_version='moe-live-ecmp-checkpoint/1',files=entries,native_runtime_bundled=False,
        stage='nack_grace',platform_acceptance_complete=False)
    with tarfile.open(a.output,'x:gz',compresslevel=6) as ar:
        for name,p in files.items():
            data=p.read_bytes();assert hashlib.sha256(data).hexdigest()==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(data);ar.addfile(info,io.BytesIO(data))
        data=(json.dumps(manifest,indent=2)+'\n').encode();info=tarfile.TarInfo('manifest.json');info.size=len(data);ar.addfile(info,io.BytesIO(data))
    assert a.output.stat().st_size<32*2**20
    save(a.output.with_suffix('.receipt.json'),dict(sha256=sha(a.output),members=len(entries)+1,
        uncompressed_bytes=sum(x['bytes'] for x in entries.values())+len(data),compressed_bytes=a.output.stat().st_size,
        platform_acceptance_complete=False,native_runtime_bundled=False))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('create')
    for key in ('repository','stage','output'):c.add_argument('--'+key,type=Path,required=True)
    r=sub.add_parser('restore')
    for key in ('archive','output'):r.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();(create if a.action=='create' else restore)(a)

if __name__=='__main__':main()
