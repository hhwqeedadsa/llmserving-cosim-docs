"""Fresh-root public example; --execute-once consumes the sixth network slot."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')


def main():
    p=argparse.ArgumentParser()
    for name in ('restored','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--execute-once',action='store_true');a=p.parse_args();root=a.restored.resolve();out=a.output.resolve();src=root/'source';ev=root/'evidence'
    env=dict(os.environ,PYTHONPATH=str(src/'python'),PYTHONDONTWRITEBYTECODE='1')
    if a.execute_once:
        out.mkdir();(out/'cwd').mkdir();commands=[]
        def run(label,cmd):
            with (out/(label+'.stdout.log')).open('w') as stdout,(out/(label+'.stderr.log')).open('w') as stderr:
                done=subprocess.run(list(map(str,cmd)),cwd=out/'cwd',env=env,stdout=stdout,stderr=stderr,timeout=60)
            commands.append(dict(label=label,command=list(map(str,cmd)),cwd=str(out/'cwd'),pythonpath=env['PYTHONPATH'],exit_code=done.returncode));save(out/'commands.json',commands)
            assert done.returncode==0,label
        cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
        run('generate',[sys.executable,'-B',src/'examples/scaleout_rdma_streams/ep_groups.py','--gpus','8','--ep','4','--rounds','2','--payload-bytes','768','--local-ns','100','--output',out/'work.json'])
        assert (out/'work.json').read_bytes()==(ev/'inputs/8-work.json').read_bytes()
        run('topology',cli+['topology','--config',ev/'inputs/8-topology.json','--output',out/'topology'])
        run('network',cli+['rdma-stream-probe','--topology',out/'topology','--protocol',ev/'inputs/8-protocol.json','--config',out/'work.json',
            '--backend',root/'runtime/bin/stream-probe','--output',out/'network'])
        run('audit',[sys.executable,'-B',src/'fixtures/persistent_scale/audit.py','--result',out/'network/result.json','--work',out/'work.json','--output',out/'audit.json'])
        for label,n in [('medium',128),('target',1024)]:
            run(label+'-offline',[sys.executable,'-B',src/'fixtures/persistent_scale/audit.py','--result',ev/'network'/label/'result.json',
                '--work',ev/'inputs'/(str(n)+'-work.json'),'--output',out/(label+'-audit.json')])
    fresh=load(out/'network/result.json');original=load(ev/'network/small/result.json')
    hashes={p.name:sha(p) for p in (root/'runtime/lib').iterdir()};assert len(hashes)==8
    def normalize(r):
        r=dict(r);paths=[]
        for path in r['loaded_libraries']:
            name=Path(path).name
            if name.startswith('libns3'):
                assert sha(Path(path))==hashes[name];paths.append(name+':'+hashes[name])
            else:paths.append(path)
        r['loaded_libraries']=paths;return r
    assert normalize(fresh)==normalize(original)
    assert all(Path(p).resolve().parent==root/'runtime/lib' for p in fresh['loaded_libraries'] if Path(p).name.startswith('libns3'))
    assert (out/'audit.json').read_bytes()==(ev/'network/small/audit.json').read_bytes()
    for label in ('medium','target'):assert (out/(label+'-audit.json')).read_bytes()==(ev/'network'/label/'audit.json').read_bytes()
    save(out/'replay.json',dict(passed=True,new_source_root=True,empty_cwd=True,public_generator_byte_equal=True,
        small_complete_result_equal_except_verified_ns3_paths=True,other_system_libraries_compared_exactly=True,
        three_offline_audits_byte_equal=True,runtime_eight_libraries_from_package=True,network_executions_total=6,
        commands=load(out/'commands.json'),scope='same Linux host incremental native runtime; full target program audited offline, no target network rerun'))


if __name__=='__main__':main()
