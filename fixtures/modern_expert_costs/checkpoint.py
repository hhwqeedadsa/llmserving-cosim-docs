"""Member-hashed portable acceptance: offline checks plus one declared network."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tarfile

from fixtures.modern_expert_costs.accept import load,save,sha,identity_check
from fixtures.modern_expert_costs.verify import compare

SCHEMA='modern-expert-costs-checkpoint/1'


def admit(archive,digest):
    assert archive.stat().st_size<=64*2**20 and sha(archive)==digest
    blobs={};total=0
    with tarfile.open(archive) as source:
        for item in source:
            name=PurePosixPath(item.name)
            assert item.isfile() and not name.is_absolute() and '..' not in name.parts
            assert str(name)==item.name and '\\' not in item.name and item.name not in blobs
            assert len(blobs)<4096 and item.size<=32*2**20
            total+=item.size;assert total<128*2**20
            raw=source.extractfile(item).read();assert len(raw)==item.size
            blobs[item.name]=(raw,stat.S_IMODE(item.mode))
    raw,_=blobs.pop('manifest.json');manifest=json.loads(raw)
    assert manifest['schema_version']==SCHEMA and set(blobs)==set(manifest['files'])
    for name,(raw,mode) in blobs.items():
        assert not any(str(p) in blobs for p in PurePosixPath(name).parents)
        assert mode in (0o644,0o755)
        assert manifest['files'][name]==dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),mode=mode)
    return blobs


def create(repo,stage,archive):
    ledger=load(stage/'execution.json')
    assert ledger['closed'] and ledger['state']=='completed' and ledger['network_attempts']==3
    assert load(stage/'equal-comparison.json')['passed'] and load(stage/'v1-baseline-replay.json')['passed']
    identity_check(repo,stage/'runtime',load(stage/'runtime-identity.json'))
    assert not archive.exists();files={}
    for directory in ('inputs','runtime','topology','fixed','equal','skew'):
        for path in (stage/directory).rglob('*'):
            if path.is_file() and not path.is_symlink():files['run/'+str(path.relative_to(stage))]=path
    for path in stage.iterdir():
        if path.is_file() and path.suffix in ('.json','.py'):files['run/'+path.name]=path
    for monitor in (stage/'monitor').iterdir():
        if (monitor/'probe.json').is_file():
            for path in monitor.iterdir():
                if path.is_file():files['run/'+str(path.relative_to(stage))]=path
    # Fixture auditors import other fixture utilities. Include Python sources,
    # never their historic result data or binaries.
    for directory in ('python/moe_ub_cosim','fixtures'):
        for path in (repo/directory).rglob('*.py'):
            files['source/'+str(path.relative_to(repo))]=path
    for name in ('deps.lock.json','tests/unit/modern_native_test.py','docs/tasks/modern_expert_costs_spec.md',
                 'fixtures/modern_expert_costs/README.md'):
        files['source/'+name]=repo/name
    for path in (repo/'examples/modern_deepep_native').iterdir():
        if path.is_file():files['source/'+str(path.relative_to(repo))]=path
    for name in load(stage/'runtime-identity.json')['sources']:files['source/'+name]=repo/name
    entries={name:dict(bytes=path.stat().st_size,sha256=sha(path),
        mode=0o755 if os.access(path,os.X_OK) else 0o644) for name,path in files.items()}
    assert len(entries)<4095 and sum(r['bytes'] for r in entries.values())<128*2**20
    with tarfile.open(archive,'x:gz',compresslevel=6) as target:
        for name,path in sorted(files.items()):
            raw=path.read_bytes();assert hashlib.sha256(raw).hexdigest()==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(raw);info.mode=entries[name]['mode']
            target.addfile(info,io.BytesIO(raw))
        raw=(json.dumps(dict(schema_version=SCHEMA,files=entries),indent=2)+'\n').encode()
        info=tarfile.TarInfo('manifest.json');info.size=len(raw);info.mode=0o644;target.addfile(info,io.BytesIO(raw))
    digest=sha(archive);admit(archive,digest)
    save(archive.with_suffix('.receipt.json'),dict(sha256=digest,members=len(entries)+1,
        compressed_bytes=archive.stat().st_size,uncompressed_bytes=sum(r['bytes'] for r in entries.values()),
        networks_already_recorded=3,additional_networks_on_restore=1))
    print('Sealed sources, inputs, actual callbacks and portable fixed runtime.')


def restore(archive,output):
    assert not output.exists();receipt=load(archive.with_suffix('.receipt.json'))
    blobs=admit(archive,receipt['sha256']);output.mkdir()
    for name,(raw,mode) in blobs.items():
        path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);path.chmod(mode)
    del blobs
    source,run,empty=output/'source',output/'run',output/'empty-cwd';empty.mkdir()
    identity=load(run/'runtime-identity.json');identity_check(source,run/'runtime',identity)
    env=os.environ|{'PYTHONPATH':str(source/'python')+':'+str(source),'PYTHONDONTWRITEBYTECODE':'1'}
    commands=[]
    def command(argv,network=False):
        row=dict(command=list(map(str,argv)),cwd=str(empty),network=network,state='started')
        commands.append(row);save(output/'commands.json',commands)
        p=subprocess.run(row['command'],cwd=empty,env=env,capture_output=True,text=True,timeout=45)
        row.update(state='terminal',exit_code=p.returncode,stdout=p.stdout,stderr=p.stderr)
        save(output/'commands.json',commands);assert p.returncode==0,row
    for label in ('fixed','equal','skew'):
        argv=[sys.executable,'-B','-m','fixtures.modern_expert_costs.verify','--run',run/label,
            '--output',output/(label+'-reverification.json')]
        if label=='skew':argv+=['--corruptions']
        command(argv)
        assert (output/(label+'-reverification.json')).read_bytes()==(run/(label+'-verification.json')).read_bytes()
    assert compare(run/'fixed',run/'equal',matrix_metadata=True)==load(run/'equal-comparison.json')
    # This is the fourth, preregistered network. Failure consumes the allowance.
    attempt=dict(state='started',new_networks=1,no_automatic_retry=True)
    save(output/'migration-attempt.json',attempt)
    try:
        migrated=output/'migrated-skew'
        command([sys.executable,'-B','-m','moe_ub_cosim','scaleout','modern-deepep-native',
            '--topology',run/'topology','--protocol',run/'inputs/rdma.json','--config',run/'inputs/skew.json',
            '--backend',run/'runtime/run-transaction-session','--output',migrated,'--callback-batches'],True)
        command([sys.executable,'-B','-m','fixtures.modern_expert_costs.verify','--run',migrated,
            '--output',output/'migrated-verification.json','--corruptions'])
        assert (output/'migrated-verification.json').read_bytes()==(run/'skew-verification.json').read_bytes()
        comparison=compare(run/'skew',migrated,library_roots=True)
        libraries=[Path(name) for name in load(migrated/'result.json')['loaded_libraries']
            if Path(name).name.startswith('libns3.')]
        assert len(libraries)==8 and all(path.parent==run/'runtime/lib' for path in libraries)
        assert all(sha(path)==identity['files']['lib/'+path.name] for path in libraries)
        identity_check(source,run/'runtime',identity)
        attempt.update(state='completed',comparison=comparison)
    except BaseException as error:
        attempt.update(state='failed_no_remaining_networks',error=repr(error));raise
    finally:save(output/'migration-attempt.json',attempt)
    save(output/'recovery.json',dict(passed=True,archive_sha256=receipt['sha256'],member_hashes_checked=True,
        copied_source=True,empty_cwd=True,offline_reports_byte_identical=3,
        migrated_verification_byte_identical=True,migration=comparison,actual_library_hashes_checked=8,
        additional_networks=1,new_builds=0,new_downloads=0,new_gpu_executions=0,commands=commands))
    print('Recomputed acceptance byte-for-byte and migrated one complete shared-network call sequence.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('create','restore'))
    for name in ('repository','stage','archive','output'):p.add_argument('--'+name,type=Path)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.stage.resolve(),a.archive.resolve())
    else:restore(a.archive.resolve(),a.output.resolve())
