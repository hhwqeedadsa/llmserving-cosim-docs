"""Old/new production loaders agree on plans and reject damaged semantic tables."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import shutil
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'unit'))
from full_model_storage_test import cases, build
from moe_ub_cosim.generate import export_plan
from moe_ub_cosim.plan_io import write_plan_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--old-loader',type=Path,required=True)
    p.add_argument('--loader',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--execution-retention',action='store_true')
    a=p.parse_args();out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    commands=[];results={}
    def run(label,binary,directory,success,*flags):
        argv=[str(binary.resolve()),str(directory),*flags]
        start=datetime.now(timezone.utc).isoformat()
        r=subprocess.run(argv,capture_output=True,text=True,timeout=90)
        (out/(label+'.stdout.log')).write_text(r.stdout);(out/(label+'.stderr.log')).write_text(r.stderr)
        commands.append(dict(argv=argv,cwd=str(Path.cwd()),started_utc=start,exit_code=r.returncode))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        assert r.returncode==(0 if success else 1),(label,r.stderr)
        if success:
            result=json.loads(r.stdout.splitlines()[-1]);result.pop('load_seconds')
            return result
        assert 'Plan load probe failed' in r.stderr,(label,r.stderr)

    first=None;first_directory=None
    for name,bundle in cases():
        plan=build(bundle)
        if first is None:first=plan
        for compact in (False,True):
            label=name+('-compact' if compact else '-pretty');directory=export_plan(plan,out/label,compact=compact)
            if first_directory is None:first_directory=directory
            old=run(label+'-old',a.old_loader,directory,True)
            new=run(label+'-new',a.loader,directory,True)
            assert old==new,(label,old,new)
            if a.execution_retention:
                retained=run(label+'-retained',a.loader,directory,True,'--compare-retention')
                storage=retained.pop('retention')
                assert storage['execution'] and storage['complete_load_equal']
                assert storage['rank_node_json_records']==storage['child_graph_transfer_tables']==0
                assert retained==old
            results[label]=new
    changes={
        'node-duration':lambda p:p['rank_graphs'][0]['nodes'][0].update(duration_ns=999),
        'dependency':lambda p:next(n for n in p['rank_graphs'][0]['nodes'] if n['deps'])['deps'].clear(),
        'stage':lambda p:p['rank_graphs'][0]['model_stages'][0].update(duration_ns=999),
        'phase':lambda p:p['rank_graphs'][0]['layer_phases'][0]['terminal_nodes'].clear(),
        'missing-rank':lambda p:p['rank_graphs'].pop(),
        'extra-rank':lambda p:p['rank_graphs'].append(deepcopy(p['rank_graphs'][0])),
        'transfer-byte':lambda p:p['transfers'][0].update(payload_bytes=1),
        'transfer-order':lambda p:p['transfers'].reverse(),
        'extra-transfer':lambda p:p['transfers'].append(deepcopy(p['transfers'][0])),
        'missing-transfer':lambda p:p['transfers'].pop(),
        'child-source':lambda p:next(t for t in p['layers'][0]['transfers'] if t['sources'])['sources'].clear(),
        'child-lane':lambda p:p['layers'][0]['transfers'][0].update(lane=999),
    }
    for label,change in changes.items():
        directory=out/('bad-'+label);directory.mkdir()
        bad=deepcopy(first);change(bad);write_plan_json(bad,directory/'plan.json',compact=True)
        # Both loaders must reject semantic corruption before needing Chakra.
        run(label+'-old',a.old_loader,directory,False)
        run(label+'-new',a.loader,directory,False)
        assert 'composition mismatch' in (out/(label+'-new.stderr.log')).read_text() or 'CANN:' in (out/(label+'-new.stderr.log')).read_text()
        if a.execution_retention:
            # This path validates a rank's Chakra graph before discarding its
            # JSON nodes; supply the unchanged graph while corrupting semantics.
            for graph in first_directory.glob('layer.*.et'):
                (directory/graph.name).hardlink_to(graph)
            run(label+'-retained',a.loader,directory,False,'--execution-retention')
    damaged=out/'bad-chakra';shutil.copytree(first_directory,damaged)
    graph=next(damaged.glob('layer.*.et'))
    graph.write_bytes(graph.read_bytes()[:8])
    run('chakra-old',a.old_loader,damaged,False)
    run('chakra-new',a.loader,damaged,False)
    if a.execution_retention:run('chakra-retained',a.loader,damaged,False,'--execution-retention')
    (out/'summary.json').write_text(json.dumps(dict(passed=True,valid=results,rejected=list(changes),
        truncated_chakra_rejected=True,
        execution_retention_compared=a.execution_retention),indent=2)+'\n')
    print('PASS: eight old/new loads and twelve semantic corruption rejections')


if __name__=='__main__':main()
