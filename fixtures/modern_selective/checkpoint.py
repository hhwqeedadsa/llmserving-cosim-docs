"""Hash-admitted functional evidence; restoration performs no network execution."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import tarfile
from fixtures.context_transactions.checkpoint import admit,SCHEMA


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def create(repo,stage,output):
    assert json.loads((stage/'verification.json').read_text())['passed'] and not output.exists()
    files={}
    for path in sorted(stage.rglob('*')):
        rel=path.relative_to(stage)
        if not path.is_file() or rel.parts[0] in ('fresh-source','restored','checkpoint.tar.gz'):continue
        if any(part.endswith('-monitor') for part in rel.parts) and not (stage/rel.parts[0]/'probe.json').exists():continue
        files['evidence/'+str(rel)]=path
    runtime=Path(json.loads((stage/'source.json').read_text())['runtime'])
    identity=json.loads((stage/'runtime-identity.json').read_text())
    for name,digest in identity['files'].items():
        path=runtime/name;assert sha(path)==digest;files['evidence/runtime/'+name]=path
    for path in (repo/'python/moe_ub_cosim').rglob('*.py'):
        files['source/'+str(path.relative_to(repo))]=path
    # Include only the transitive fixture imports used by these offline tools.
    pending=list((repo/'fixtures/modern_selective').glob('*.py'));seen=set()
    while pending:
        path=pending.pop()
        if path in seen:continue
        seen.add(path);files['source/'+str(path.relative_to(repo))]=path
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.ImportFrom) and node.module and node.module.startswith('fixtures.'):
                dep=repo/(node.module.replace('.','/')+'.py');assert dep.is_file();pending.append(dep)
    for name,digest in identity['sources'].items():
        path=repo/name;assert sha(path)==digest;files['source/'+name]=path
    for name in ('deps.lock.json','docs/tasks/modern_selective_bridge_spec.md','AGENTS.md','fixtures/modern_selective/README.md',
                 'tests/unit/modern_native_test.py','examples/modern_deepep_native/input.json',
                 'examples/modern_deepep_native/input-selective.json','examples/modern_deepep_native/topology.json',
                 'examples/modern_deepep_native/rdma.json','examples/modern_deepep_native/README.md'):
        files['source/'+name]=repo/name
    entries={name:dict(bytes=path.stat().st_size,sha256=sha(path)) for name,path in files.items()}
    assert len(entries)<2048 and sum(e['bytes'] for e in entries.values())<128*2**20
    assert all(not path.is_symlink() and path.stat().st_size<=16*2**20 for path in files.values())
    manifest=dict(schema_version=SCHEMA,files=entries,scope='modern selective functional acceptance; same-host ABI-bound runtime; no clean build or GPU calibration')
    with tarfile.open(output,'x:gz',compresslevel=6) as archive:
        for name,path in files.items():
            data=path.read_bytes();assert hashlib.sha256(data).hexdigest()==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644
            if name in ('evidence/runtime/run-transaction-session','evidence/runtime/transaction-session'):info.mode=0o755
            archive.addfile(info,io.BytesIO(data))
        data=(json.dumps(manifest,indent=2)+'\n').encode();info=tarfile.TarInfo('manifest.json');info.size=len(data)
        archive.addfile(info,io.BytesIO(data))
    admit(output)
    save(output.with_suffix('.receipt.json'),dict(sha256=sha(output),members=len(entries)+1,
        uncompressed_bytes=sum(e['bytes'] for e in entries.values())+len(data),compressed_bytes=output.stat().st_size))


def restore(archive,output):
    assert not output.exists();content,manifest=admit(archive);output.mkdir()
    for name,data in content.items():
        p=output/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    for name in ('run-transaction-session','transaction-session'):(output/'evidence/runtime'/name).chmod(0o755)
    (output/'empty-cwd').mkdir()
    save(output/'restoration.json',dict(passed=True,members=len(content),all_hashes_verified_before_writes=True,
        archive_sha256=sha(archive),additional_network_executions=0,scope=manifest['scope']))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('create')
    for name in ('repository','stage','output'):c.add_argument('--'+name,type=Path,required=True)
    r=sub.add_parser('restore')
    for name in ('archive','output'):r.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.stage.resolve(),a.output.resolve())
    else:restore(a.archive.resolve(),a.output.resolve())


if __name__=='__main__':main()
