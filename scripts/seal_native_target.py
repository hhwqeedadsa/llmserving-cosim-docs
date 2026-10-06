"""Seal a completed M16 target for the existing public slice archive importer.

Run under the cumulative host_probe budget. Archives are verified before the
registered expanded graph/record directories are reclaimed. No simulation.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

from moe_ub_cosim.user_io import read,save


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('work','graph','prepared','output-dir'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--graph-alias',type=Path,action='append',default=[])
    a=p.parse_args();root=Path(__file__).resolve().parents[1];work=a.work.resolve();out=a.output_dir.resolve()
    out.mkdir(parents=True,exist_ok=False);raw=work/'raw';summary=read(work/'result.json');scope=read(work/'scope.json')
    report=read(raw/'execution/result.json');assert summary['passed'] and report['normal_exit'] and report['network_drain']['idle']
    assert scope['blocks']==61 and scope['requests']==1024 and scope['active_ranks']==128 and not scope['resource_prefix_only']
    assert report['record_storage']['fields']['node_execution']['records']==9526154
    shutil.copyfile(raw/'execution/result.json',raw/'preserved-execution-summary.json')
    shutil.copyfile(work/'scope.json',raw/'scope.json')
    archive_tool=root/'scripts/archive_v04_graph.py'
    commands=[['--graph',str(a.graph.resolve()),'--alias',str(work/'shared/graph'),'--archive',str(out/'graph.tar.gz')],
        ['--directory',str(raw/'execution'),'--kind','execution','--archive',str(out/'execution.tar.gz')]]
    for alias in a.graph_alias:commands[0].extend(['--alias',str(alias.resolve())])
    for i,args in enumerate(commands):
        command=[sys.executable,str(archive_tool),*args,'--remove-verified']
        with (out/f'archive-{i}.stdout.log').open('x') as stdout,(out/f'archive-{i}.stderr.log').open('x') as stderr:
            r=subprocess.run(command,cwd=root,stdout=stdout,stderr=stderr)
        save(out/f'archive-{i}.command.json',dict(argv=command,cwd=str(root),exit_code=r.returncode))
        assert r.returncode==0,'Archive failed; preserve existing files and inspect its log'
    imported=dict(schema_version='moe-ub-slice-import/1',run_directory=str(raw),shared_directory=str(work/'shared'),
        system=str(work/'actual-inputs/system.json'),network=str(work/'actual-inputs/network'),observation=str(work/'observation.json'),
        graph_archive=dict(path='graph.tar.gz',inventory='graph.tar.inventory.json'),
        execution_archive=dict(path='execution.tar.gz',inventory='execution.tar.inventory.json'),
        max_simulation_ns=1000000000,source_evidence={
            'native-target-summary.json':str(work/'result.json'),'native-input-binding.json':str(work/'network-input.json'),
            'prepared.json':str(a.prepared.resolve()),'build-result.json':str(a.graph.resolve().parent/'result.json'),
            'target-config.json':str(root/'fixtures/m16_native_target/config.json')})
    save(out/'import.json',imported)
    budget=read(root/'examples/first_round/sample.json')['host']
    budget.update(rss_bytes=8*2**30,address_bytes=12*2**30,artifact_bytes=32*2**30,wall_seconds=1800,
        artifact_roots=[str(root/s) for s in ['build/v04-platform','runs/v04-platform','build/m10-resource-efficiency',
            'runs/v03-resource-efficiency','build/m10-research-slice','runs/v03-research-slice']])
    sample=dict(schema_version='moe-ub-sample/1',host=budget,observation=None,
        cache_directory=str(out/'materialized'),export_policy=str(root/'fixtures/m16_native_target/export-policy.json'))
    save(out/'sample.json',sample)
    print('PASS verified target archives and explicit public import/sample configurations; no second target simulation')


if __name__=='__main__':main()
