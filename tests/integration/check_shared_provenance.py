"""Compare preserved and shared-ledger backends on the same bounded real UB inputs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def read(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, x): p.write_text(json.dumps(x, indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-build',type=Path,required=True)
    parser.add_argument('--build-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    a=parser.parse_args();root=Path(__file__).resolve().parents[2]
    out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    commands=[];builds={'before':a.baseline_build.resolve(),'after':a.build_dir.resolve()}
    for label,build in builds.items():
        argv=[sys.executable,str(root/'tests/integration/check_host_preflight.py'),
              '--build-dir',str(build),'--output-dir',str(out/label)]
        start=datetime.now(timezone.utc).isoformat()
        p=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=1300)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(p,stream))
        commands.append(dict(argv=argv,cwd=str(root),started_utc=start,ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=p.returncode))
        save(out/'commands.json',commands)
        assert p.returncode==0,(label,p.stderr)
    compared={};new_storage=[]
    for case in ('one','two','ten','ten-source-off'):
        old=out/'before'/case/'run';new=out/'after'/case/'run'
        assert read(old/'service_result.json')==read(new/'service_result.json')
        reports=[]
        for label,path in (('before',old),('after',new)):
            result=read(path/'execution/result.json');libs=result.pop('loaded_libraries')
            ns3=[Path(p).resolve() for p in libs if Path(p).name.startswith('libns3')]
            assert len(ns3)==10 and all(p.is_relative_to(builds[label]/'output/lib') for p in ns3)
            result['loaded_library_names']=[Path(p).name for p in libs];reports.append(result)
        assert reports[0]==reports[1]  # Only the independently checked build paths differ.
        # Compiler output paths and Python bytecode timestamps are build metadata.
        # Compare compiler records after replacing only the run directory; graph
        # JSON/protobuf and schema sources still require exact byte equality.
        compiler_before={str(p.relative_to(old)):json.loads(p.read_text().replace(str(old),'RUN'))
                         for p in (old/'graphs').rglob('compiler.json')}
        compiler_after={str(p.relative_to(new)):json.loads(p.read_text().replace(str(new),'RUN'))
                        for p in (new/'graphs').rglob('compiler.json')}
        assert compiler_before==compiler_after
        # Native traces, endpoint/port observations, queues and source records
        # must remain byte-for-byte identical.
        hashes={}
        for directory in ('graphs','execution/network/runlog','execution/ports'):
            prior={str(p.relative_to(old)):sha(p) for p in (old/directory).rglob('*')
                   if p.is_file() and p.name not in ('resource-audit.json','compiler.json') and p.suffix!='.pyc'}
            current={str(p.relative_to(new)):sha(p) for p in (new/directory).rglob('*')
                     if p.is_file() and p.name not in ('resource-audit.json','compiler.json') and p.suffix!='.pyc'}
            assert prior==current,(case,directory,[k for k in prior if prior[k]!=current.get(k)][:4])
            hashes.update(current)
        # Existing per-port resource fields retain their logical semantics.
        for p in (old/'execution/ports').glob('*/resource-audit.json'):
            before=read(p);after=read(new/p.relative_to(old))
            assert all(after[k]==v for k,v in before.items())
        before=read(out/'before/summary.json')['cases'][case]
        after=read(out/'after/summary.json')['cases'][case]
        storage=after['provenance_storage'];new_storage.append(storage)
        assert storage['storage']=='shared_run_ledger' and storage['descriptor_records']==2400
        assert before['provenance_storage']['storage']=='historical_per_port_ledgers'
        assert storage['descriptor_records']*2*storage['port_observers']==before['provenance_storage']['descriptor_records']
        assert storage['packet_records']*storage['port_observers']==before['provenance_storage']['packet_records']
        compared[case]=dict(identical_artifact_files=len(hashes),artifact_sha256=hashes,
            before_storage=before['provenance_storage'],after_storage=storage,
            before_profile=before['profile'],after_profile=after['profile'],finished_ns=after['finished_ns'])
    assert len({x['source_subscriptions'] for x in new_storage})==1
    assert all(x['created_notifications']==2400 for x in new_storage)
    sources=['backend/port_observation.hh','backend/port_observation.cc','backend/cosim_service.cc',
             'tests/integration/check_host_preflight.py','tests/integration/check_shared_provenance.py']
    save(out/'summary.json',dict(passed=True,cases=compared,target_scale_executed=False,
        source_hashes={p:sha(root/p) for p in sources},
        backend_hashes={k:sha(v/'output/cosim/ns3.44-cosim-service') for k,v in builds.items()}))
    print('PASS: shared physical records, one source callback per packet, unchanged logical records, graphs, UB timing and per-port outputs')


if __name__=='__main__':main()
