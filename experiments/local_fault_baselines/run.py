"""Four preregistered new conditions; saved results are inputs, not reruns."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')


def normalize(result,libraries):
    result=deepcopy(result);seen=set()
    for i,path in enumerate(result['loaded_libraries']):
        real=str(Path(path).resolve())
        if real in libraries:
            assert sha(Path(path))==libraries[real];result['loaded_libraries'][i]=real;seen.add(real)
    assert seen==set(libraries)
    return result


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage','backend','build-identity'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();backend=a.backend.resolve()
    assert not (s/'commands.json').exists()
    obs=s/'inputs/observed';index=load(obs/'index.json');build=load(a.build_identity)
    subprocess.run(['git','diff','--exit-code',index['platform_commit'],'--','python','backend','deps.lock.json','.gitmodules'],cwd=repo,check=True)
    for name,digest in index['files'].items():assert sha(obs/name)==digest
    for name,digest in load(s/'inputs/provenance.json')['files'].items():assert sha(s/'inputs'/name)==digest
    assert sha(backend)==build['binaries']['stream-probe']
    for name,digest in build['sources'].items():assert sha(repo/name)==digest
    libs=build['libraries'];assert len(libs)==8 and all(sha(Path(k))==v for k,v in libs.items())
    assert libs==load(s/'inputs/baseline/runtime.json')['libraries']
    save(s/'runtime.json',dict(backend=str(backend),sha256=sha(backend),libraries=libs,build_identity=build,
        platform_commit=index['platform_commit'],scope='existing native program and eight fixed external libraries, no fixture driver'))
    (s/'planned').mkdir();(s/'network').mkdir();commands=[]
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    def run(label,tail,network=False):
        assert sum(c['network'] for c in commands)<4 or not network
        monitors=[load(p) for p in s.rglob('probe.json') if not p.is_relative_to(s/'restored')]
        assert all(m['status']=='completed' and not m['remaining_live_group_pids'] for m in monitors)
        assert sum(m['wall_seconds'] for m in monitors)+70<720
        assert sum(p.stat().st_size for p in s.rglob('*') if p.is_file())<128*2**20
        assert shutil.disk_usage(s).free>8*2**30
        argv=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(s/(label+'-monitor')),
            '--wall-seconds','60','--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(128*2**20),
            '--artifact-root',str(s),'--min-free-bytes',str(8*2**30),'--',*map(str,cli+tail)]
        row=dict(label=label,command=argv,cwd=str(repo),network=network,status='started');commands.append(row);save(s/'commands.json',commands)
        done=subprocess.run(argv,cwd=repo,timeout=65);row.update(exit_code=done.returncode,status='terminal');save(s/'commands.json',commands)
        assert done.returncode==0,label+': stop without retry'
    run('topology',['topology','--config',obs/'topology.json','--output',s/'topology'])
    assert (s/'topology/topology.json').read_bytes()==(s/'inputs/baseline/topology.json').read_bytes()
    # Validate all protocol/plan combinations before any actual network.
    for name in index['new_cases']:
        run(name+'-plan',['rdma-causal-plan','--config',obs/index['plan'],'--topology',s/'topology',
            '--protocol',obs/(name+'-protocol.json'),'--output',s/'planned'/name])
        assert (s/'planned'/name/'work.json').read_bytes()==(s/'inputs/baseline/saved-healthy/work.json').read_bytes()
        before=load(s/'inputs/baseline/saved-healthy/mapping.json');after=load(s/'planned'/name/'mapping.json')
        before.pop('protocol_sha256');after.pop('protocol_sha256');assert before==after
    for name in index['new_cases']:
        run(name+'-network',['rdma-stream-probe','--topology',s/'topology','--protocol',obs/(name+'-protocol.json'),
            '--config',s/'planned'/name/'work.json','--backend',backend,'--output',s/'network'/name],True)
        result=load(s/'network'/name/'result.json')
        assert result['passed'] and result['packet_capture']['complete'] and not result['packet_capture']['omitted_events']
        if name=='healthy':
            assert normalize(result,libs)==normalize(load(s/'inputs/baseline/saved-healthy/result.json'),libs), 'actual application default compatibility failed'
            save(s/'healthy-compatibility.json',dict(passed=True,all_fields_equal=True,
                normalized_only='path spelling of eight hash-identical native libraries',scope='saved original/native current full application reference'))
    assert sha(backend)==build['binaries']['stream-probe'] and all(sha(Path(k))==v for k,v in libs.items())
    save(s/'run.json',dict(passed=True,new_actual_networks=4,saved_inputs=2,phase_closed=True,successful_network_reruns=0,
        all_work_byte_identical=True,all_mapping_equal_except_declared_protocol_hash=True,healthy_full_result_compatible=True))


if __name__=='__main__':main()
