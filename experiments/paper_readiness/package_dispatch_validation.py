"""Package a bounded local review/checkpoint; no simulation, installation or publication."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[2]
def load(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('task','output','checkpoint'):parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--monitors',required=True,nargs='+',type=Path)
    args=parser.parse_args();task,out,checkpoint=(p.resolve() for p in (args.task,args.output,args.checkpoint))
    assert not out.exists() and not checkpoint.exists() and shutil.disk_usage(out.parent).free>=8*1024**3+32*1024**2
    verify=load(task/'metrics-checked/verification.json');assert verify['passed']
    packets=load(task/'checked/verification.json');assert packets['passed'] and packets['rounds']==72
    monitors=[load(path/'probe.json') for path in args.monitors]
    assert all(m['status']=='completed' and not m['remaining_live_group_pids'] for m in monitors)
    assert sum(m['wall_seconds'] for m in monitors)<300
    sources=['prepare_dispatch_validation.py','run_dispatch_validation.py','analyze_dispatch_network.py',
        'check_dispatch_network.py','analyze_dispatch_validation.py','check_dispatch_validation_metrics.py',
        'package_dispatch_validation.py','dispatch_validation_protocol.md','README.md']
    out.mkdir()
    for name in ('report.html','verification.json','metrics.json'):shutil.copyfile(task/'metrics-checked'/name,out/name)
    shutil.copyfile(task/'checked/verification.json',out/'packet-verification.json')
    shutil.copyfile(task/'observed/index.json',out/'index.json')
    shutil.copyfile(task/'analysis/summary.json',out/'summary.json')
    shutil.copyfile(task/'analysis/rounds.json',out/'rounds.json')
    shutil.copyfile(ROOT/'experiments/paper_readiness/dispatch_validation_protocol.md',out/'protocol.md')
    maintenance=dict(platform_base_commit=load(task/'run/execution.json')['platform_base_commit'],
        package_start_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        monitors=monitors,execution=load(task/'run/execution.json'),
        package_command=['python',__file__,'--task',str(task),'--output',str(out),'--checkpoint',str(checkpoint),
            '--monitors',*[str(p) for p in args.monitors]],
        sources_sha256={name:sha(ROOT/'experiments/paper_readiness'/name) for name in sources},
        metric_implementation_written_after_network_execution=True,prospective_policy_claimed=False,
        original_network_repeated=False,analysis_repeated=True,new_dependencies=False,
        legacy_checker_note='Legacy-window branch retained; legacy run not reverified because its recorded mutable backend path now has a different hash. Original committed evidence is retained.',
        scope='18 declared data-only conditions; no independent heldout requests/hardware/modern library or paper-completion claim')
    save(out/'maintenance.json',maintenance)
    files={f'raw/{path.relative_to(task)}':path for path in task.rglob('*') if path.is_file()}
    for directory in args.monitors:
        for path in directory.rglob('*'):
            if path.is_file():files[f'monitors/{directory.name}/{path.relative_to(directory)}']=path
    for name in sources:files['source/'+name]=ROOT/'experiments/paper_readiness'/name
    files['source/current_task.md']=ROOT/'docs/tasks/current.md';files['source/deps.lock.json']=ROOT/'deps.lock.json'
    manifest=load(task/'truth/manifest.json')
    for row in manifest['files']:
        path=ROOT/row['file']['path'];assert sha(path)==row['file']['sha256']
        files['repository/'+row['file']['path']]=path
    for path,identity in packets['native_libraries_sha256'].items():
        native=Path(path);assert sha(native)==identity;files['native/'+native.name]=native
    first_case=packets['cases'][0]['case'];command=load(task/'run'/first_case/'inputs.json')['command'];backend=Path(command[0])
    assert sha(backend)==load(task/'run/execution.json')['backend_sha256'];files['native/'+backend.name]=backend
    for path in out.iterdir():files['review/'+path.name]=path
    identities={name:dict(bytes=path.stat().st_size,sha256=sha(path)) for name,path in sorted(files.items())}
    assert sum(v['bytes'] for v in identities.values())<=96*1024**2
    checkpoint.mkdir();archive=checkpoint/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name,path in sorted(files.items()):tar.add(path,arcname=name,recursive=False)
    assert archive.stat().st_size<=32*1024**2
    with tarfile.open(archive,'r:gz') as tar:
        assert set(tar.getnames())==set(identities)
        for member in tar:
            assert member.isfile();raw=tar.extractfile(member).read()
            assert len(raw)==identities[member.name]['bytes'] and hashlib.sha256(raw).hexdigest()==identities[member.name]['sha256']
    save(checkpoint/'members.json',identities)
    save(out/'archive.json',dict(path=str(archive),sha256=sha(archive),files=len(files),bytes=archive.stat().st_size,
        all_member_hashes_verified=True,storage='local_persistent_disk_not_remote_backup',
        original_requests_included=True,native_binary_and_fixed_ns3_libraries_included=True,
        native_restore='Native files retain original RPATH; restore deliberately using saved original input paths or rebuild pinned sources; not a standalone runtime image'))
    class Links(HTMLParser):
        def handle_starttag(self,tag,attrs):
            if tag=='a':
                for key,value in attrs:
                    if key=='href':assert (out/value).is_file(),value
    Links().feed((out/'report.html').read_text())
    assert shutil.disk_usage(out).free>=8*1024**3
    print(json.dumps(dict(passed=True,files=len(files),archive_bytes=archive.stat().st_size,output=str(out))))


if __name__=='__main__':main()
