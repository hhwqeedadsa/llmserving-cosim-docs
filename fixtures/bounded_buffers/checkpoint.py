"""Hash source/runtime/evidence, restore safely, and replay public bounded buffers."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[2]
PLAIN_LIMIT = 64 * 2**20
ARCHIVE_LIMIT = 32 * 2**20
EVIDENCE_DIRS = (
    'functional', 'functional-monitor', 'nack', 'nack-monitor',
    'corrected-tail', 'corrected-tail-monitor', 'corrected-nack', 'corrected-nack-monitor',
    'stream-regression', 'stream-regression-monitor', 'transaction-regression', 'transaction-regression-monitor',
    'session-regression', 'session-regression-monitor', 'prefix-regression', 'prefix-regression-monitor',
    'public-example', 'public-example-monitor', 'public-topology', 'topology-monitor',
    'public-topology-monitor', 'unit-monitor', 'related-unit-monitor', 'deps-monitor',
    'audit-monitor', 'extended-audit-monitor',
)


def require(ok, why):
    if not ok:
        raise ValueError(why)


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def save(path, value):
    with path.open('x') as f:
        f.write(json.dumps(value, indent=2, sort_keys=True)+'\n')


def safe_name(name):
    p = PurePosixPath(name)
    require(not p.is_absolute() and '..' not in p.parts and str(p) == name and
            p.parts[0] in ('source', 'runtime', 'evidence'), 'unsafe member: '+name)


def archive(a):
    stage, out = a.stage.resolve(), a.output.resolve()
    require(not out.exists(), 'archive output already exists')
    entries = {}
    def add(path, name):
        safe_name(name)
        require(path.is_file() and not path.is_symlink() and name not in entries, 'missing/linked/duplicate input: '+str(path))
        entries[name] = path
    def tree(folder, prefix):
        require(folder.is_dir(), 'missing directory: '+str(folder))
        for p in sorted(folder.rglob('*')):
            if '__pycache__' in p.parts or p.suffix == '.pyc':
                continue
            require(not p.is_symlink(), 'linked source/evidence: '+str(p))
            if p.is_file():
                add(p, prefix+'/'+p.relative_to(folder).as_posix())
    for folder in ('python/moe_ub_cosim', 'backend', 'examples/scaleout_rdma', 'examples/scaleout_rdma_transactions'):
        tree(ROOT/folder, 'source/'+folder)
    for rel in ('AGENTS.md', 'README.md', 'deps.lock.json', 'docs/development.md', 'docs/architecture.md',
                'docs/model_validation.md', 'docs/record_storage.md', 'docs/tasks/bounded_copied_buffers_spec.md',
                'docs/reviews/有界复制缓冲与实际ACK回收_20261024.md',
                'fixtures/bounded_buffers/build.py', 'fixtures/bounded_buffers/checkpoint.py',
                'fixtures/bounded_buffers/README.md', 'fixtures/bounded_buffers/verification.json',
                'tests/unit/bounded_buffers_test.py', 'tests/integration/check_bounded_buffers.py',
                'tests/integration/audit_bounded_buffers.py', 'tests/integration/audit_session_control_alarms.py'):
        add(ROOT/rel, 'source/'+rel)
    # All acceptance evidence, including the unmatched fault and failed assertion.
    for folder in EVIDENCE_DIRS:
        if folder == 'public-topology-monitor' and not (stage/folder).exists():
            continue
        tree(stage/folder, 'evidence/'+folder)
    for rel in ('independent-audit.json', 'independent-audit-extended.json', 'fault-injection-correction.json',
                'check-before-data-fault-node-fix.py', 'build/identity.json', 'build/build-records.json'):
        add(stage/rel, 'evidence/'+rel)
    for folder in sorted((stage/'build').iterdir()):
        if folder.is_dir() and folder.name.startswith(('compile-', 'link-')):
            tree(folder, 'evidence/build/'+folder.name)
    for name in ('transaction-probe', 'transaction-session'):
        file = 'ns3.42-moe-rdma-'+name
        add(stage/'build/bin'/file, 'runtime/bin/'+file)
    identity = json.loads((stage/'build/identity.json').read_text())
    libs = sorted(a.libraries.resolve().glob('libns3*.so'))
    require(len(libs) == 8, 'exactly eight fixed libraries required')
    for p in libs:
        require(sha(p) == identity['protected'][str(p)], 'fixed library identity changed')
        add(p, 'runtime/lib/'+p.name)
    manifest = {n:dict(bytes=p.stat().st_size, sha256=sha(p), mode=p.stat().st_mode & 0o777)
                for n,p in sorted(entries.items())}
    raw = (json.dumps(manifest, indent=2, sort_keys=True)+'\n').encode()
    plain = len(raw)+sum(r['bytes'] for r in manifest.values())
    require(plain < PLAIN_LIMIT, 'plain archive exceeds 64MiB; retain originals')
    out.mkdir()
    bundle = out/'checkpoint.tar.gz'
    with tarfile.open(bundle, 'w:gz', compresslevel=6) as tf:
        info = tarfile.TarInfo('manifest.json'); info.size=len(raw); info.mode=0o644
        tf.addfile(info, io.BytesIO(raw))
        for name,p in sorted(entries.items()):
            tf.add(p, arcname=name, recursive=False)
    require(bundle.stat().st_size < ARCHIVE_LIMIT, 'archive exceeds 32MiB')
    with tarfile.open(bundle, 'r:gz') as tf:
        names=set()
        for m in tf:
            require(m.isfile() and m.name not in names, 'nonfile/duplicate member')
            names.add(m.name)
            if m.name == 'manifest.json':
                require(tf.extractfile(m).read() == raw, 'manifest mismatch')
            else:
                r=manifest[m.name]
                require(m.size==r['bytes'] and m.mode==r['mode'], 'member metadata mismatch')
                with tf.extractfile(m) as f:
                    require(hashlib.file_digest(f,'sha256').hexdigest()==r['sha256'], 'member content mismatch')
        require(names == set(manifest)|{'manifest.json'}, 'member set mismatch')
    require(all(sha(p)==manifest[n]['sha256'] for n,p in entries.items()), 'inputs changed during archive')
    (out/'manifest.json').write_bytes(raw)
    receipt=dict(passed=True, sha256=sha(bundle), archive_bytes=bundle.stat().st_size, plain_bytes=plain,
                 members=len(manifest)+1, ns3_libraries=8, transaction_binaries=2, all_members_verified=True,
                 scope='prebuilt Linux runtime with pinned ns-3; host ABI/Python/loader still required')
    save(out/'archive.json',receipt); print(json.dumps(receipt))


def recover(a):
    bundle,dest=a.archive.resolve(),a.destination.resolve()
    receipt=json.loads(a.receipt.read_text())
    require(bundle.stat().st_size==receipt['archive_bytes']<ARCHIVE_LIMIT and sha(bundle)==receipt['sha256'], 'archive size/identity mismatch')
    require(not dest.exists(), 'destination already exists')
    with tarfile.open(bundle,'r:gz') as tf:
        members=tf.getmembers()
        require(len(members)==receipt['members'] and len({m.name for m in members})==len(members) and all(m.isfile() for m in members), 'duplicate/nonfile/missing member')
        require(sum(m.size for m in members)==receipt['plain_bytes']<PLAIN_LIMIT, 'restore exceeds budget')
        require(tf.getmember('manifest.json').size<4*2**20, 'oversize manifest')
        manifest=json.load(tf.extractfile('manifest.json'))
        require(set(manifest)|{'manifest.json'}=={m.name for m in members}, 'manifest members differ')
        for name in manifest:
            safe_name(name)
        dest.mkdir()
        for m in members:
            target=dest/m.name
            if m.name!='manifest.json':
                r=manifest[m.name]
                require(m.size==r['bytes'] and m.mode==r['mode'] and 0<=m.mode<=0o777, 'member metadata mismatch')
            target.parent.mkdir(parents=True,exist_ok=True)
            with tf.extractfile(m) as inp,target.open('xb') as out:
                shutil.copyfileobj(inp,out,length=1024**2)
            target.chmod(m.mode)
            if m.name!='manifest.json':
                require(sha(target)==r['sha256'], 'restored content mismatch')
    require(all(sha(dest/n)==r['sha256'] for n,r in manifest.items()), 'restored members changed')
    save(dest/'restore.json',dict(passed=True,members=len(members),all_members_sha256_verified=True))
    print(json.dumps(dict(restored=str(dest),members=len(members))))


def replay(a):
    restored,out=a.restored.resolve(),a.output.resolve()
    source,evidence=restored/'source',restored/'evidence'
    lib=restored/'runtime/lib'
    require(not out.exists(),'replay output already exists');out.mkdir();cwd=out/'empty-cwd';cwd.mkdir()
    env=dict(os.environ,PYTHONPATH=str(source/'python'),PYTHONDONTWRITEBYTECODE='1',LD_LIBRARY_PATH=str(lib))
    env.pop('LD_PRELOAD',None)
    def run(command,label):
        start=time.monotonic()
        with (out/(label+'.stdout')).open('xb') as stdout,(out/(label+'.stderr')).open('xb') as stderr:
            p=subprocess.run(command,cwd=cwd,env=env,stdout=stdout,stderr=stderr,timeout=40)
        save(out/(label+'.command.json'),dict(command=command,cwd=str(cwd),exitcode=p.returncode,
             wall_seconds=time.monotonic()-start,pythonpath=env['PYTHONPATH'],ld_library_path=str(lib)))
        require(p.returncode==0,'replay failed: '+label)
    py=sys.executable;cli=[py,'-B','-m','moe_ub_cosim','scaleout']
    run(cli+['topology','--config',str(source/'examples/scaleout_rdma/small-clos-100g.json'),'--output',str(out/'topology')],'topology')
    require(sha(out/'topology/topology.json')==sha(evidence/'public-topology/topology.json'),'public topology changed')
    expected=json.loads((evidence/'public-example/result.json').read_text())
    def normalized(r):
        libs=[Path(n) for n in r['loaded_libraries'] if Path(n).name.startswith('libns3')]
        require(len(libs)==8 and {p.resolve() for p in libs}=={p.resolve() for p in lib.glob('libns3*.so')}, 'runtime escaped package libraries')
        r['loaded_libraries']=[Path(n).name if Path(n).name.startswith('libns3') else n for n in r['loaded_libraries']]
        return r
    expected['loaded_libraries']=[Path(n).name if Path(n).name.startswith('libns3') else n for n in expected['loaded_libraries']]
    for mode in ('probe','session'):
        run(cli+['rdma-transaction-'+mode,'--topology',str(out/'topology'),
            '--protocol',str(source/'examples/scaleout_rdma/rdma.json'),
            '--config',str(source/'examples/scaleout_rdma_transactions/bounded-buffers.json'),
            '--backend',str(restored/'runtime/bin'/('ns3.42-moe-rdma-transaction-'+mode)),
            '--output',str(out/mode)],'public-'+mode)
        actual=normalized(json.loads((out/mode/'result.json').read_text()))
        if mode=='session':
            require('session' in actual,'session entry did not run');actual.pop('session')
        require(actual==expected,'full result changed beyond library paths/session envelope: '+mode)
    run([py,'-B',str(source/'tests/integration/audit_bounded_buffers.py'),
         '--functional',str(evidence/'functional'),'--additional-functional',str(evidence/'nack'),
         '--additional-functional',str(evidence/'corrected-tail'),'--additional-functional',str(evidence/'corrected-nack'),
         '--output',str(out/'independent-audit-extended.json')],'offline-audit')
    require((out/'independent-audit-extended.json').read_bytes()==(evidence/'independent-audit-extended.json').read_bytes(),'independent audit bytes changed')
    manifest=json.loads((restored/'manifest.json').read_text())
    require(all(sha(restored/n)==r['sha256'] for n,r in manifest.items()),'packaged member changed during replay')
    report=dict(passed=True,network_replays=2,offline_audit_recomputations=1,packaged_ns3_libraries=8,
                full_probe_equal_except_library_directories=True,full_session_equal_except_session_envelope_and_library_directories=True,
                offline_audit_byte_equal=True,all_members_hash_unchanged=True,source=str(source),cwd=str(cwd),
                scope='prebuilt runtime replay; host Linux/Python/loader/ABI still required')
    save(out/'recovery.json',report);print(json.dumps(report))


def main():
    p=argparse.ArgumentParser(description=__doc__);commands=p.add_subparsers(dest='command',required=True)
    pack=commands.add_parser('archive')
    for name in ('stage','libraries','output'):pack.add_argument('--'+name,type=Path,required=True)
    restore=commands.add_parser('recover')
    for name in ('archive','receipt','destination'):restore.add_argument('--'+name,type=Path,required=True)
    run=commands.add_parser('replay')
    for name in ('restored','output'):run.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();dict(archive=archive,recover=recover,replay=replay)[a.command](a)


if __name__=='__main__':main()
