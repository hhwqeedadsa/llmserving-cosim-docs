"""Build one standalone native API probe against unchanged pinned libraries."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from moe_ub_cosim.host_probe import probe,disk_usage

ROOT=Path(__file__).resolve().parents[2]
NS3=ROOT/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,sort_keys=True,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','libraries','includes'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--resume-compile-fix',action='store_true',help='Only preserve/recompile the recorded pre-execution callback-name collision; no state transition rerun')
    a=p.parse_args();s=a.stage.resolve()
    if not a.resume_compile_fix:s.mkdir(exist_ok=False)
    libs=sorted(a.libraries.resolve().glob('libns3*.so'));assert len(libs)==8
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=NS3,text=True).strip()=='3964b1d41cb5f7b51004c316b0eae1a3713c2498'
    assert subprocess.run(['git','diff','--quiet','HEAD','--'],cwd=NS3).returncode==0
    files=[ROOT/'backend/rdma/fabric.cc',ROOT/'backend/rdma/persistent_streams.cc',
        *(NS3/('src/point-to-point/model/'+name) for name in ('qbb-net-device.cc','switch-node.cc','switch-mmu.cc','switch-node.h')),
        *(ROOT/'experiments/link_down_contract'/name for name in ('probe.cc','build_run.py','audit.py','design.md'))]
    identity=dict(platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        native_commit='3964b1d41cb5f7b51004c316b0eae1a3713c2498',sources={str(q.relative_to(ROOT)):sha(q) for q in files},libraries={str(q):sha(q) for q in libs},network_run_budget=0,native_state_transition_budget=1)
    records=[]
    if a.resume_compile_fix:
        previous=json.loads((s/'identity.json').read_text());records=json.loads((s/'execution.json').read_text())
        assert len(records)==1 and records[0]['name']=='build' and records[0]['status']=='child_failed'
        assert not (s/'probe').exists() and not (s/'result.json').exists() and not (s/'transition-monitor').exists()
        assert 'reference to ‘Drop’ is ambiguous' in (s/'build-monitor/stderr.log').read_text()
        changed={name for name,digest in previous['sources'].items() if digest!=identity['sources'][name]}
        assert changed=={'experiments/link_down_contract/probe.cc','experiments/link_down_contract/build_run.py'}
        for rel in changed:assert sha(s/'original'/Path(rel).name)==previous['sources'][rel]
        save(s/'correction.json',dict(reason='probe callback name collides with ns3::Drop enum; rename QueueDrop before any execution',original_failed_compile_preserved=True,changed_sources=sorted(changed),network_or_state_transition_reruns=0,new_identity=identity))
    else:save(s/'identity.json',identity)
    def run(name,argv):
        report=probe(argv,s/(name+'-monitor'),rss_bytes=2**30,address_bytes=2**31,artifact_bytes=16*2**20,wall_seconds=60,artifact_roots=[s],min_free_bytes=8*2**30)
        records.append(dict(name=name,status=report['status'],exit_code=report['child_exit_code'],wall_seconds=report['wall_seconds']));save(s/'execution.json',records)
        assert report['status']=='completed' and not report['remaining_live_group_pids'],name
        assert sum(r['wall_seconds'] for r in records)<120 and max(disk_usage(s).values())<64*2**20
    command=['/usr/bin/c++','-std=c++20','-O0','-DNDEBUG','-Wall','-Wextra','-I'+str(a.includes.resolve()),
        '-I'+str(ROOT/'third_party/LLMServingSim/astra-sim/extern/helper'),str(ROOT/'experiments/link_down_contract/probe.cc'),
        '-o',str(s/'probe'),'-L'+str(a.libraries.resolve()),'-Wl,-rpath,'+str(a.libraries.resolve()),'-Wl,--no-as-needed',*map(str,libs)]
    run('build-corrected' if a.resume_compile_fix else 'build',command)
    run('transition',[str(s/'probe'),str(s/'result.json')])
    run('audit',[sys.executable,'-B',str(ROOT/'experiments/link_down_contract/audit.py'),'--result',str(s/'result.json'),'--output',str(s/'audit.json')])
    for rel,digest in identity['sources'].items():assert sha(ROOT/rel)==digest
    for name,digest in identity['libraries'].items():assert sha(Path(name))==digest
    save(s/'unchanged.json',dict(sources=len(files),libraries=8,all_hashes_unchanged=True,probe_sha256=sha(s/'probe')))
    print((s/'audit.json').read_text())


if __name__=='__main__':main()
