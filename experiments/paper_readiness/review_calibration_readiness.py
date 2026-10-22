"""Independently review recorded read-only inspection and build its report."""
import argparse
import csv
import hashlib
from html.parser import HTMLParser
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import tarfile
from urllib.parse import unquote,urlsplit

ROOT=Path(__file__).resolve().parents[2]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('run','output','checkpoint'):parser.add_argument('--'+name,type=Path,required=True)
    a=parser.parse_args();run,out,target=(getattr(a,n).resolve() for n in ('run','output','checkpoint'))
    assert not out.exists() and not target.exists() and shutil.disk_usage(target.parent).free>=8*2**30+8*2**20
    result=json.loads((run/'summary.json').read_text());commands=json.loads((run/'commands.json').read_text())
    assert result['state']=='read_only_inspection_completed' and len(commands)==9
    def output(command):
        for key in ('stdout','stderr'):
            assert re.fullmatch(r'\d+\.'+key,command[key]),'recorded log path must be local and bounded'
        return (run/command['stdout']).read_text()+(run/command['stderr']).read_text()
    gpu_command=commands[0]
    assert gpu_command['argv'][1].startswith('--query-gpu=index,name,compute_cap,memory.total,driver_version')
    rows=list(csv.reader(io.StringIO(output(gpu_command))))
    assert len(rows)==len(result['gpus'])==6
    for raw,gpu in zip(rows,result['gpus']):
        assert [s.strip() for s in raw]==[str(gpu['index']),gpu['name'],gpu['compute_capability'],str(gpu['memory_mib']),gpu['driver_version']]
        assert gpu['compute_capability']=='8.0' and gpu['memory_mib']==81920
    assert sum('PCIe' in g['name'] for g in result['gpus'])==4
    assert sum('SXM4' in g['name'] for g in result['gpus'])==2
    topo=output(commands[1]);matrix=result['gpu_nic_relationships']
    assert len(matrix)==10 and set(matrix)==set(next(iter(matrix.values())))
    for row,values in matrix.items():
        assert values[row]=='X'
        for peer,value in values.items():assert value==matrix[peer][row]
        matches=[line for line in topo.splitlines() if line.startswith(row+'\t')]
        assert len(matches)==1 and matches[0].split()[1:11]==list(values.values())
    assert not result['gpu_nvlink_pair_observed']
    assert not any(value.startswith('NV') for row in matrix.values() for value in row.values())
    assert commands[2]['argv']==['ibv_devinfo','-l'] and '0 HCAs found:' in output(commands[2])
    assert result['verbs_hcas']==0 and result['uverbs_nodes']==0
    assert len(result['sysfs_rdma_devices'])==4
    for device in result['sysfs_rdma_devices']:
        for port in device['ports']:
            assert port['state']=='1: DOWN' and port['phys_state']=='3: Disabled'
            assert not port['active'] and port['active_rate_observed'] is None
    assert commands[3]['argv']==['nvcc','--version'] and commands[3]['state']=='not_found_in_PATH'
    help_command=commands[6];help_text=output(help_command)
    assert help_command['argv']==['ib_write_bw','--help'] and help_command['exitcode']==1
    assert help_text.startswith('Usage:') and '--use_cuda' not in help_text
    assert result['tools']['ib_write_bw']['help_advertises_use_cuda'] is False
    assert len(result['python_environments'])==2
    for command,python in zip(commands[7:],result['python_environments']):
        assert command['argv'][1]=='-c' and 'from importlib.metadata import' in command['argv'][2]
        assert json.loads(output(command))==python['metadata']
        assert all(v is None for v in python['metadata'].values())
    assert sum(g['state']=='failed' for g in result['gates'])==5
    assert not result['modern_network_calibration_ready'] and not result['any_benchmark_or_traffic_executed']
    source=ROOT/'data/research/deepep_modern_20261022/sources/README.md'
    assert sha(source)==result['modern_requirements']['readme_sha256']
    for item in result['modern_requirements']['excerpts']:
        assert source.read_text().splitlines()[item['first_line']-1]==item['text']
    out.mkdir()
    shutil.copy2(run/'summary.json',out/'summary.json')
    doc=ROOT/'docs/reviews/现代通信校准条件与论文验证路线_20261022.md'
    spec=importlib.util.spec_from_file_location('renderer',ROOT/'docs/review_artifacts/paper_readiness_20261022/build_review.py')
    renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer);renderer.HERE=out
    renderer.render(doc,out/'report.html','现代通信校准条件与论文验证路线')
    html,count=re.subn(r'<nav>.*?</nav>','<nav><a href="summary.json">环境摘要</a> · <a href="verification.json">独立核对</a> · <a href="maintenance.json">执行记录</a></nav>',(out/'report.html').read_text(),count=1)
    assert count==1;(out/'report.html').write_text(html)
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.paths=[]
        def handle_starttag(self,tag,attrs):
            for key,value in attrs:
                if key in ('href','src'):
                    u=urlsplit(value)
                    if not u.scheme and u.path:self.paths.append((out/unquote(u.path)).resolve())
    links=Links();links.feed(html)
    for path in links.paths:
        if path.name not in ('verification.json','maintenance.json'):assert path.exists(),path
    verification=dict(passed=True,recorded_commands_checked=len(commands),gpu_csv_rows_checked=len(rows),
        topology_relationships_checked=100,sysfs_snapshot_ports_checked=4,python_metadata_environments=2,
        sysfs_is_recorded_snapshot_not_current_live_device_access=True,
        complete_perftest_help_exit_one_preserved=True,source_requirements_excerpts_checked=4,
        html_links=len(links.paths),browser_visual_acceptance=False,
        input_summary_sha256=sha(run/'summary.json'),input_commands_sha256=sha(run/'commands.json'),
        script_sha256={p:sha(ROOT/p) for p in ('experiments/paper_readiness/inspect_calibration_environment.py',
            'experiments/paper_readiness/review_calibration_readiness.py')},
        no_hardware_benchmark_evidence=True,network_execution=False,goal_complete=False)
    save(out/'verification.json',verification)
    files={}
    for path in run.rglob('*'):
        if path.is_file():files['saved_run/'+str(path.relative_to(run))]=path
    for path in out.rglob('*'):
        if path.is_file():files[str(path.relative_to(ROOT))]=path
    for path in (doc,ROOT/'experiments/paper_readiness/inspect_calibration_environment.py',Path(__file__).resolve(),source):
        files[str(path.relative_to(ROOT))]=path
    folder=source.parents[1]
    for path in folder.glob('*.json'):files[str(path.relative_to(ROOT))]=path
    assert sum(p.stat().st_size for p in files.values())<=8*2**20
    target.mkdir();archive=target/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz') as t:
        for name,path in files.items():t.add(path,arcname=name,recursive=False)
    with tarfile.open(archive) as t:
        for name,path in files.items():assert hashlib.sha256(t.extractfile(name).read()).hexdigest()==sha(path)
    save(out/'archive.json',dict(path=str(archive.relative_to(ROOT)),sha256=sha(archive),bytes=archive.stat().st_size,
        verified_files=len(files),files={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in files.items()}))
    print(json.dumps(dict(passed=True,recorded_commands=9,topology_cells=100,checkpoint_bytes=archive.stat().st_size)))


if __name__=='__main__':main()
