"""Source, supplemental evidence and immutable original evidence; no native bundling."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
from fixtures.live_ecmp.checkpoint import restore
from fixtures.nack_grace_edges.stage import load,save,sha


def create(a):
    repo=a.repository.resolve();s=a.stage.resolve();old=a.original.resolve();files={}
    assert not a.output.exists()
    for prefix in ('python','backend','fixtures/nack_grace','fixtures/nack_grace_edges','examples/scaleout_nack_grace'):
        for p in (repo/prefix).rglob('*'):
            if p.is_file() and p.suffix in ('.py','.hh','.cc','.md','.json'):files['source/'+str(p.relative_to(repo))]=p
    for rel in ('AGENTS.md','deps.lock.json','docs/development.md','docs/user_guide.md','docs/record_storage.md',
        'docs/tasks/current.md','docs/tasks/nack_grace_spec.md','docs/tasks/nack_grace_edges_spec.md',
        'fixtures/live_ecmp/checkpoint.py','fixtures/live_ecmp/audit.py','fixtures/selective_repeat/audit.py',
        'fixtures/transaction_selective_payload/audit.py',
        'tests/unit/nack_grace_test.cc','tests/unit/nack_grace_events_test.cc',
        'tests/unit/scaleout_nack_grace_test.py','tests/unit/scaleout_selective_repeat_test.py'):
        files['source/'+rel]=repo/rel
    for directory in ('inputs','topology','network'):
        for p in (s/directory).rglob('*'):
            if p.is_file():files['evidence/'+str(p.relative_to(s))]=p
    for p in s.iterdir():
        if p.is_file() and p.suffix in ('.json','.py'):files['evidence/'+p.name]=p
    for p in s.glob('*-monitor/*'):
        if p.is_file() and (p.parent/'probe.json').exists():files['evidence/monitors/'+str(p.relative_to(s))]=p
    for directory in ('inputs',):
        for p in (old/directory).rglob('*'):
            if p.is_file():files['baseline/'+str(p.relative_to(old))]=p
    for name in ('default-old','default-new','head-loss-idle-fixed','no-feedback','fresh-mixed'):
        files['baseline/network/'+name+'/result.json']=old/'network'/name/'result.json'
    for name in ('cases.json','build.json','build-v1.json','checks.json','native-rejections.json','inspection.json','closeout.json',
                 'evidence-final.tar.receipt.json'):
        files['baseline/'+name]=old/name
    assert all(not p.is_symlink() and p.stat().st_size<=16*2**20 for p in files.values())
    entries={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in files.items()}
    assert len(entries)+1<=2048 and sum(x['bytes'] for x in entries.values())<128*2**20
    manifest=dict(schema_version='moe-live-ecmp-checkpoint/1',files=entries,stage='nack_grace_edges',
        original_stage_network_attempts=6,new_stage_network_attempts=4,native_runtime_bundled=False)
    with tarfile.open(a.output,'x:gz',compresslevel=6) as ar:
        for name,p in files.items():
            data=p.read_bytes();assert hashlib.sha256(data).hexdigest()==entries[name]['sha256']
            member=tarfile.TarInfo(name);member.size=len(data);ar.addfile(member,io.BytesIO(data))
        data=(json.dumps(manifest,indent=2)+'\n').encode();member=tarfile.TarInfo('manifest.json');member.size=len(data);ar.addfile(member,io.BytesIO(data))
    assert a.output.stat().st_size<32*2**20
    save(a.output.with_suffix('.receipt.json'),dict(sha256=sha(a.output),members=len(entries)+1,
        uncompressed_bytes=sum(x['bytes'] for x in entries.values())+len(data),compressed_bytes=a.output.stat().st_size,
        scope='sources and raw old/new evidence; native programs/libraries are external hash-pinned inputs'))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('create')
    for k in ('repository','stage','original','output'):c.add_argument('--'+k,type=Path,required=True)
    r=sub.add_parser('restore')
    for k in ('archive','output'):r.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();(create if a.action=='create' else restore)(a)

if __name__=='__main__':main()
