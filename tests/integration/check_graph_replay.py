"""Real UB service with disposable graphs matches preserved executions exactly."""
import argparse
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.graph_storage import replay
from moe_ub_cosim.plan_io import compact_plan_chunks
from moe_ub_cosim.record_storage import iter_result,iter_port_records

def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline-dir',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    args=p.parse_args();out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    root=Path.cwd();base=args.baseline_dir.resolve();commands=[];results={}
    limits=dict(schema_version='moe-ub-graph-storage/1',max_recipe_bytes=8*2**20,
        max_archive_bytes=64*2**20,max_active_graph_bytes=64*2**20)
    save(out/'policy.json',limits)
    cases=[('lifecycle-storage',n) for n in ['closed-service-reference','closed-service-window','closed-service-empty']]
    cases+=[('lifecycle-stress',n) for n in ['rounds-8','rounds-32']]
    for suite,name in cases:
        old=base/suite/'work'/name;new=out/name
        old_commands=read(old.parent/'commands.json')
        command=next(c['argv'] for c in old_commands if c['argv'][c['argv'].index('--output-dir')+1]==str(old))
        command=list(command);command[command.index('--output-dir')+1]=str(new)
        command+=['--graph-storage',str(out/'policy.json')]
        started=datetime.now(timezone.utc).isoformat()
        with (out/(name+'.stdout.log')).open('x') as stdout,(out/(name+'.stderr.log')).open('x') as stderr:
            run=subprocess.run(command,cwd=root,stdout=stdout,stderr=stderr,timeout=600)
        commands.append(dict(argv=command,cwd=str(root),started_utc=started,
            ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=run.returncode));save(out/'commands.json',commands)
        assert run.returncode==0,(name,(out/(name+'.stderr.log')).read_text()[-3000:])
        assert read(new/'service_result.json')==read(old/'service_result.json')
        reports=[read(path/'execution/result.json') for path in [old,new]]
        for field in reports[0]['record_storage']['fields']:
            assert list(iter_result(old/'execution',field,reports[0]))==list(iter_result(new/'execution',field,reports[1])),(name,field)
        for report in reports:
            report.pop('record_storage');report['loaded_libraries']=sorted(Path(v).name for v in report['loaded_libraries'])
        assert reports[0]==reports[1],name
        port_rows=0
        for manifest in sorted((old/'execution/ports').glob('*/record-storage.json')):
            directory=manifest.parent;other=new/'execution/ports'/directory.name
            fields=read(manifest)['fields']
            for field in fields:
                rows=list(iter_port_records(old/'execution',directory,field))
                assert rows==list(iter_port_records(new/'execution',other,field)),(name,field)
                port_rows+=len(rows)
        graph_files=0
        for archive in sorted((new/'graph-replay').glob('g*/archive.json')):
            graph=archive.parent.name;metadata=read(archive);old_graph=old/'graphs'/graph
            expected=read(old_graph/'plan.json');digest=hashlib.sha256()
            for part in compact_plan_chunks(expected):digest.update(part.encode())
            digest.update(b'\n')
            assert metadata['generation']['hashes']['plan.json']==digest.hexdigest()
            old_hashes=read(old_graph/'generation.json')['hashes']
            for filename,value in old_hashes.items():
                if filename!='plan.json':assert metadata['generation']['hashes'][filename]==value
            assert read(archive.parent/'cleanup.json')['files']==metadata['files']
            assert not (new/'graphs'/graph).exists();graph_files+=len(metadata['files'])
        storage=read(new/'graph-storage.json')
        assert not storage['active_graphs'] and not list((new/'graphs').iterdir())
        assert len(storage['retired_graphs'])==len(read(old/'service_result.json')['waves'])
        if storage['retired_graphs']:
            last=storage['retired_graphs'][-1]
            replay(new/'graph-replay'/last/'archive.json',out/(name+'-replay'))
        results[name]=dict(storage=storage,logical_port_rows=port_rows,removed_owned_files=graph_files,
                           exact_plan_and_chakra_hashes=True,request_and_backend_results_identical=True)
    assert results['rounds-8']['storage']['peak_active_graphs']==results['rounds-32']['storage']['peak_active_graphs']==1
    save(out/'summary.json',dict(passed=True,cases=results,target_scale_executed=False,research_export_verified=False))
    print('PASS: disposable graphs, source recipes and exact replay across five real UB cases')


if __name__=='__main__':main()
