"""Bounded acceptance packaging; recovery uses packaged code, bytes and libraries."""
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile

def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def oracle_mutations(repo,root):
    sys.path.insert(0,str(repo/'tests/integration'))
    from check_remote_transaction_workflow import audit
    from copy import deepcopy
    result=read(root/'accept/high/result.json');config=read(repo/'examples/scaleout_rdma_transactions/sparse-windows.json')
    rejected=[]
    for kind in ('page-byte','high-wire-address','missing-initial-bytes'):
        changed=deepcopy(result);spec=deepcopy(config)
        if kind=='page-byte':
            page=changed['transactions']['windows'][0]['pages'][0]
            page['hex']=('ff' if page['hex'][:2]!='ff' else '00')+page['hex'][2:]
        elif kind=='high-wire-address':
            packet=changed['transactions']['transport']['source_payload_packets'][0]
            data=bytearray.fromhex(packet['hex']);data[16]^=1;packet['hex']=data.hex()
        else:spec['windows'][0]['segments']=[]
        try:audit(changed,spec,64)
        except AssertionError:rejected.append(kind)
        else:raise AssertionError('oracle accepted '+kind)
    summary=dict(passed=True,mutations_rejected=rejected,new_network_executions=0)
    save(root/'oracle-mutations.json',summary);print(json.dumps(summary))

def legacy(repo,root):
    dest=root/'legacy';dest.mkdir();commands=[]
    env=dict(os.environ,PYTHONPATH=str(repo/'python'),PYTHONDONTWRITEBYTECODE='1')
    for name in ('work','local-stages'):
        for flavor,binary in [('old',Path('/dev/shm/moe-transaction-session-20261023/bin/ns3.42-moe-rdma-transaction-probe')),
                              ('new',root/'bin/ns3.42-moe-rdma-transaction-probe')]:
            output=dest/(name+'-'+flavor)
            command=[sys.executable,'-m','moe_ub_cosim','scaleout','rdma-transaction-probe','--topology',root/'accept/topology',
                     '--protocol',root/'accept/protocol.json','--config',repo/'examples/scaleout_rdma_transactions'/(name+'.json'),
                     '--backend',binary,'--output',output]
            with (dest/(output.name+'.stdout')).open('w') as stdout,(dest/(output.name+'.stderr')).open('w') as stderr:
                p=subprocess.run(list(map(str,command)),cwd=dest,env=env,stdout=stdout,stderr=stderr,timeout=20)
            commands.append(dict(command=list(map(str,command)),cwd=str(dest),exitcode=p.returncode))
            save(dest/'commands.json',commands);assert p.returncode==0
        assert (dest/(name+'-old/result.json')).read_bytes()==(dest/(name+'-new/result.json')).read_bytes()
    summary=dict(passed=True,commands=4,v1_v2_results_byte_identical=True)
    save(dest/'verification.json',summary);print(json.dumps(summary))

def archive(repo,root):
    dest=root/'checkpoint';dest.mkdir();items={}
    for p in root.rglob('*'):
        if not p.is_file() or p.is_symlink():continue
        relative=p.relative_to(root)
        if relative.parts[0] in ('checkpoint','recovery'):continue
        if relative.parts[0].endswith('-monitor') and not (root/relative.parts[0]/'probe.json').is_file():continue
        items['raw/'+str(relative)]=p
    for directory in ('python','backend','tests','fixtures/sparse_windows'):
        for p in (repo/directory).rglob('*'):
            if directory=='fixtures/sparse_windows' and p.name in ('archive.json','recovery.json','closeout.json'):continue
            if p.is_file() and '__pycache__' not in p.parts:items['source/'+str(p.relative_to(repo))]=p
    for name in ('AGENTS.md','deps.lock.json','docs/tasks/transaction_sparse_windows_spec.md',
                 'docs/architecture.md','docs/model_validation.md','docs/record_storage.md','docs/user_guide.md',
                 'examples/scaleout_rdma/small-clos-100g.json','examples/scaleout_rdma/rdma.json',
                 'examples/scaleout_rdma_transactions/sparse-windows.json','examples/scaleout_rdma_transactions/README.md'):
        items['source/'+name]=repo/name
    for name in ('probe','session'):items['runtime/bin/'+name]=root/'bin'/('ns3.42-moe-rdma-transaction-'+name)
    libs={}
    for name,digest in read(root/'old-runtime.json').items():
        p=Path(name);assert sha(p)==digest
        if p.suffix=='.so':libs[p.name]=p.resolve()
    assert len(libs)==8
    items.update({'runtime/lib/'+name:p for name,p in libs.items()})
    manifest={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in sorted(items.items())}
    size=sum(v['bytes'] for v in manifest.values());assert size<=96*2**20 and len(manifest)<=2000
    path=dest/'checkpoint.tar.gz'
    with tarfile.open(path,'w:gz',dereference=True) as tf:
        for name,p in sorted(items.items()):tf.add(p,arcname=name,recursive=False)
    assert path.stat().st_size<=32*2**20
    with tarfile.open(path,'r:gz') as tf:
        assert len(tf.getmembers())==len(manifest)
        for member in tf:
            assert member.isfile();data=tf.extractfile(member).read()
            assert len(data)==manifest[member.name]['bytes'] and hashlib.sha256(data).hexdigest()==manifest[member.name]['sha256']
    save(dest/'members.json',manifest)
    summary=dict(passed=True,members=len(manifest),member_bytes=size,compressed_bytes=path.stat().st_size,
                 archive_sha256=sha(path),native_binaries=2,native_libraries=8,storage='tmpfs_volatile',persistent_backup=False)
    save(dest/'archive.json',summary);save(repo/'fixtures/sparse_windows/archive.json',summary);print(json.dumps(summary))

def recover(repo,root):
    dest=root/'recovery';dest.mkdir();path=root/'checkpoint/checkpoint.tar.gz'
    manifest=read(root/'checkpoint/members.json');assert sha(path)==read(root/'checkpoint/archive.json')['archive_sha256']
    with tarfile.open(path,'r:gz') as tf:
        assert len(tf.getmembers())==len(manifest)
        for member in tf:
            name=PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts
            data=tf.extractfile(member).read();assert hashlib.sha256(data).hexdigest()==manifest[member.name]['sha256']
            if member.name.startswith(('runtime/','source/python/','source/examples/')) or member.name in (
                    'source/tests/integration/check_remote_transaction_workflow.py','raw/accept/protocol.json',
                    'raw/accept/high/result.json','raw/accept/high-online/result.json'):
                p=dest/member.name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
                if member.name.startswith('runtime/bin/'):p.chmod(0o755)
    source=dest/'source';cwd=dest/'empty-cwd';cwd.mkdir();new=dest/'new';new.mkdir()
    env=dict(os.environ,PYTHONPATH=str(source/'python'),PYTHONDONTWRITEBYTECODE='1',LD_LIBRARY_PATH=str(dest/'runtime/lib'))
    cli=[sys.executable,'-m','moe_ub_cosim','scaleout'];commands=[]
    for name,command in [
        ('topology',cli+['topology','--config',source/'examples/scaleout_rdma/small-clos-100g.json','--output',new/'topology']),
        *[(flavor,cli+['rdma-transaction-'+flavor,'--topology',new/'topology','--protocol',dest/'raw/accept/protocol.json',
                       '--config',source/'examples/scaleout_rdma_transactions/sparse-windows.json',
                       '--backend',dest/'runtime/bin'/flavor,'--output',new/flavor]) for flavor in ('probe','session')]]:
        with (new/(name+'.stdout')).open('w') as stdout,(new/(name+'.stderr')).open('w') as stderr:
            p=subprocess.run(list(map(str,command)),cwd=cwd,env=env,stdout=stdout,stderr=stderr,timeout=20)
        commands.append(dict(command=list(map(str,command)),cwd=str(cwd),exitcode=p.returncode,pythonpath=env['PYTHONPATH'],library_path=env['LD_LIBRARY_PATH']))
        save(new/'commands.json',commands);assert p.returncode==0,name
    spec=importlib.util.spec_from_file_location('sparse_oracle',source/'tests/integration/check_remote_transaction_workflow.py')
    oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)
    config=read(source/'examples/scaleout_rdma_transactions/sparse-windows.json');summaries=[]
    for flavor,original_name in [('probe','high'),('session','high-online')]:
        result=read(new/flavor/'result.json');expected=read(dest/'raw/accept'/original_name/'result.json')
        audit=oracle.audit(result,config,64)
        native=[Path(p) for p in result['loaded_libraries'] if Path(p).name.startswith('libns3')]
        assert len(native)==8 and all(p.parent==dest/'runtime/lib' for p in native)
        assert {Path(p).name for p in result.pop('loaded_libraries')}=={Path(p).name for p in expected.pop('loaded_libraries')}
        assert result==expected
        summaries.append(dict(name=flavor,all_fields_except_library_paths_equal=True,independent_byte_replay=audit))
    summary=dict(passed=True,archive_members_verified=len(manifest),new_source_root=True,new_cwd=True,
                 restored_native_libraries=8,public_commands=3,fresh_native_executions=2,results=summaries,
                 host_system_libraries_still_required=True,modern_deepep_connected=False,hardware_calibrated=False)
    save(root/'recovery.json',summary);save(repo/'fixtures/sparse_windows/recovery.json',summary);print(json.dumps(summary))

def final(repo,root):
    manifest=read(root/'checkpoint/members.json');sources=0
    for name,row in manifest.items():
        if name.startswith('source/'):
            p=repo/name[len('source/'):];assert sha(p)==row['sha256'],name
            if p.suffix=='.py':ast.parse(p.read_text())
            sources+=1
    before=read(root/'old-runtime.json');assert all(sha(Path(p))==digest for p,digest in before.items())
    assert 'sparse memory independent assertions 6483' in (root/'build-run-sparse_remote_memory_test/stdout.log').read_text()
    assert read(root/'build-run-remote_memory_test/stdout.log')['checks']==6758
    assert 'Ran 16 tests' in (root/'unit-monitor/stderr.log').read_text()
    assert 'PASS: checked 13 dependency checkouts' in (root/'deps-monitor/stdout.log').read_text()
    probes=[read(p) for p in root.glob('*/probe.json')]
    assert probes and all(p['status']=='completed' and p['child_exit_code']==0 and not p['remaining_live_group_pids'] for p in probes)
    seconds=sum(p['wall_seconds'] for p in probes);assert seconds<480
    size=sum(p.stat().st_size for p in root.rglob('*') if p.is_file());assert size<192*2**20
    assert shutil.disk_usage(root).free>=8*2**30
    for file in ('accept/verification.json','session-regression/verification.json','local-io-regression/verification.json',
                 'legacy/verification.json','oracle-mutations.json','recovery.json'):assert read(root/file)['passed']
    links=0
    for name in ('fixtures/sparse_windows/README.md','docs/tasks/transaction_sparse_windows_spec.md',
                 'examples/scaleout_rdma_transactions/README.md'):
        p=repo/name
        for target in re.findall(r'\]\(([^)]+)\)',p.read_text()):
            if target.startswith(('http:','https:','#')):continue
            dest=(p.parent/target.split('#')[0]).resolve()
            if dest!=(repo/'fixtures/sparse_windows/closeout.json').resolve():assert dest.exists(),(name,target)
            links+=1
    summary=dict(passed=True,source_snapshot_files=sources,old_runtime_hashes_unchanged=len(before),
                 monitored_seconds_before_final=seconds,root_bytes_before_final=size,cumulative_bytes_limit=192*2**20,
                 local_links_checked=links,storage='tmpfs_volatile',persistent_backup=False,
                 modern_deepep_connected=False,hardware_calibrated=False,paper_contribution_established=False)
    save(root/'closeout.json',summary);save(repo/'fixtures/sparse_windows/closeout.json',summary);print(json.dumps(summary))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=('legacy','oracle_mutations','archive','recover','final'))
    p.add_argument('--repo',type=Path,required=True);p.add_argument('--root',type=Path,required=True)
    a=p.parse_args();globals()[a.phase](a.repo.resolve(),a.root.resolve())
