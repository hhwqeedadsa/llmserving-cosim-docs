"""Seal fixed evidence and verify a copied bundle offline; never run a network."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

from fixtures.staged_publication.verify import load,save,sha,verify,corruptions

ROOT=Path(__file__).resolve().parents[2]


def offline(root):
    stage=root/'records/validated';checks={}
    for case,config in (('main','work.json'),('credit','credit.json'),('moved','work.json')):
        events=[json.loads(line) for line in (stage/case/'control.jsonl').read_text().splitlines()]
        checks[case]=verify(load(stage/case/'result.json'),load(stage/'inputs'/config),load(stage/'topology/topology.json'),
            load(stage/'inputs/rdma.json'),events)
    main=load(stage/'main/result.json');moved=load(stage/'moved/result.json')
    for value in (main,moved):
        value['loaded_libraries']=sorted(Path(p).name if Path(p).name.startswith('libns3.') else p for p in value['loaded_libraries'])
    assert main==moved
    legacy=load(stage/'legacy/result.json');explicit=load(stage/'explicit/result.json')
    explicit['transactions'].pop('submission')
    for row in explicit['transactions']['operations']:row.pop('prepared_ns')
    assert explicit==legacy
    identity=load(stage/'runtime-identity.json')
    assert all(sha(root/'runtime'/n)==h for n,h in identity.items())
    events=[json.loads(line) for line in (stage/'main/control.jsonl').read_text().splitlines()]
    bad=corruptions(main,load(stage/'inputs/work.json'),load(stage/'topology/topology.json'),load(stage/'inputs/rdma.json'),events)
    return dict(passed=True,checks=checks,legacy_equal=True,moved_normalized_result_equal=True,
                corruptions_rejected=bad,runtime_files=len(identity),network_executed=False)


def seal(stage):
    output=stage/'sealed';output.mkdir();records=output/'records';records.mkdir()
    def copy_file(src,dst):
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
    src=stage/'validated'
    for name in ('inputs','topology','main','credit','tail','source-conflict','native-invalid','legacy','explicit','moved'):
        shutil.copytree(src/name,records/'validated'/name)
    for path in src.glob('*.json'):copy_file(path,records/'validated'/path.name)
    for path in src.glob('*.log'):copy_file(path,records/'validated'/path.name)
    for name in ('commands.json','topology.stderr.log','topology.stdout.log','syntax-commands.json'):
        if (stage/name).exists():copy_file(stage/name,records/'initial-failure'/name)
    for path in (stage/'inputs').glob('*'):copy_file(path,records/'initial-failure/inputs'/path.name)
    # Retain the pre-correction checker and exact failed monitors separately.
    copy_file(stage/'validated/source/fixtures/staged_publication/verify.py',records/'initial-failure/verify-before-rounding-fix.py')
    for monitor in stage.glob('*/probe.json'):
        shutil.copytree(monitor.parent,records/'monitors'/monitor.parent.name)
    for name in ('provenance.json','backend.json','compiler-options.json','ldd.txt'):
        copy_file(stage/'build'/name,records/'build'/name)
    shutil.copytree(stage/'build/logs',records/'build/logs')
    shutil.copytree(src/'runtime',output/'runtime')
    provenance=load(stage/'build/provenance.json')
    assert all(sha(ROOT/n)==h for n,h in provenance['sources'].items())
    for name in provenance['sources']:copy_file(ROOT/name,output/name)
    for directory in ('python/moe_ub_cosim','fixtures/staged_publication','examples/scaleout_staged_publication'):
        shutil.copytree(ROOT/directory,output/directory,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('deps.lock.json','AGENTS.md','docs/development.md','docs/tasks/staged_publication_spec.md',
                 'docs/user_guide.md','docs/architecture.md','docs/record_storage.md','backend/README.md',
                 'tests/unit/staged_publication_test.py','tests/unit/session_wakeups_test.cc'):
        copy_file(ROOT/name,output/name)
    upstream=load(ROOT/'docs/reviews/evidence/hybrid_publication_20261027.json')['upstream']
    for row in upstream:
        path=ROOT/'data/research'/row['cache']/'sources'/row['path'];data=path.read_bytes()
        assert len(data)==row['bytes'] and sha(path)==row['sha256']
        assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==row['git_blob_sha']
        copy_file(path,output/'sources/upstream'/row['cache']/row['path'])
    native='third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
    timing=[]
    for name in ('src/core/model/nstime.h','src/network/utils/data-rate.cc'):
        copy_file(ROOT/native/name,output/'sources/native'/name)
        timing.append(dict(path=name,sha256=sha(ROOT/native/name)))
    basis=dict(passed=True,upstream=upstream,native_rounding_sources=timing,
        declared_scope='DB-class logical ordered-ready and prefix max; not other handlers, MMIO counts or GPU costs',
        workload='handwritten functional input, not a real serving request or real route timing',
        source_capture='registered_window_at_publish/1',source_release='transaction_completed/1',
        hardware_calibrated=False,full_hybrid_executed=False,innovation_established=False)
    save(output/'source-basis.json',basis)
    expected=offline(output);save(stage/'sealed-verification.json',expected)
    manifest={str(p.relative_to(output)):sha(p) for p in sorted(output.rglob('*')) if p.is_file()}
    save(stage/'bundle-manifest.json',manifest)
    archive=stage/'staged-publication.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name in manifest:tar.add(output/name,arcname=name,recursive=False)
    restored=stage/'recovered';restored.mkdir()
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers();assert {m.name for m in members}==set(manifest)
        for member in members:
            assert member.isfile() and not Path(member.name).is_absolute() and '..' not in Path(member.name).parts
            copy_file_stream=tar.extractfile(member);target=restored/member.name;target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('wb') as handle:shutil.copyfileobj(copy_file_stream,handle)
    assert all(sha(restored/name)==h for name,h in manifest.items())
    cwd=stage/'offline-empty-cwd';cwd.mkdir()
    command=[sys.executable,'-B','-m','fixtures.staged_publication.seal','--offline',str(restored),'--output',str(stage/'recovered-verification.json')]
    env=dict(os.environ,PYTHONPATH=str(restored)+':'+str(restored/'python'),PYTHONDONTWRITEBYTECODE='1')
    result=subprocess.run(command,cwd=cwd,env=env,capture_output=True,text=True,timeout=30)
    save(stage/'recovery-command.json',dict(command=command,cwd=str(cwd),exit_code=result.returncode,
        stdout=result.stdout,stderr=result.stderr))
    assert result.returncode==0 and (stage/'sealed-verification.json').read_bytes()==(stage/'recovered-verification.json').read_bytes()
    receipt=dict(passed=True,archive_sha256=sha(archive),archive_bytes=archive.stat().st_size,
        members=len(manifest),member_bytes=sum((output/n).stat().st_size for n in manifest),
        all_member_hashes_equal=True,offline_verification_byte_equal=True,recovery_networks=0,recovery_builds=0)
    save(stage/'archive.json',receipt)
    save(ROOT/'fixtures/staged_publication/archive.json',receipt)
    save(ROOT/'fixtures/staged_publication/verification.json',load(src/'verification.json'))
    save(ROOT/'fixtures/staged_publication/source_basis.json',basis)


def main():
    parser=argparse.ArgumentParser(description=__doc__);group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--stage',type=Path);group.add_argument('--offline',type=Path)
    parser.add_argument('--output',type=Path);a=parser.parse_args()
    if a.offline:
        assert a.output is not None and not a.output.exists();save(a.output,offline(a.offline.resolve()))
    else:seal(a.stage.resolve())


if __name__=='__main__':main()
