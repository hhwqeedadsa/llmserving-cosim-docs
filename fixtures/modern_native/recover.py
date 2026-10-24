"""Only the fourth planned network; old successful networks are read-only."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from fixtures.modern_native.audit import audit
from fixtures.modern_native.stage import load,save,sha


def main():
    p=argparse.ArgumentParser()
    for key in ('restored','stage'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();s=a.stage.resolve();root=a.restored.resolve();source=root/'source';evidence=root/'evidence';cwd=root/'empty-cwd'
    import moe_ub_cosim
    assert Path(__file__).resolve().is_relative_to(source) and Path(moe_ub_cosim.__file__).resolve().is_relative_to(source)
    assert not any(cwd.iterdir());env=dict(os.environ,PYTHONPATH=str(source/'python')+':'+str(source),PYTHONDONTWRITEBYTECODE='1');commands=[]
    def invoke(label,args):
        args=list(map(str,args))
        with (s/(label+'.stdout')).open('w') as out,(s/(label+'.stderr')).open('w') as err:
            r=subprocess.run(args,cwd=cwd,env=env,stdout=out,stderr=err,timeout=55)
        commands.append(dict(label=label,command=args,cwd=str(cwd),exit_code=r.returncode));save(s/'recovery-commands.json',commands)
        assert r.returncode==0,label
    def cli(label,*args):invoke(label,[sys.executable,'-P','-B','-m','moe_ub_cosim','scaleout',*args])
    invoke('restored-verification',[sys.executable,'-P','-B','-m','fixtures.modern_native.verification','--stage',evidence,'--output',s/'restored-verification.json'])
    assert (s/'restored-verification.json').read_bytes()==(evidence/'verification.json').read_bytes()
    provenance=load(evidence/'route-provenance.json');tokens=[]
    for row in provenance['original_files']:
        path=root/'source-data'/Path(row['path']).name;assert sha(path)==row['sha256'];tokens.append(load(path)[1]['3'])
    assert tokens==load(evidence/'inputs/four-routes.json')['modern']['calls'][0]['tokens']
    cfg=load(source/'examples/modern_deepep_native/input.json');cfg['modern']['calls']=cfg['modern']['calls'][1:];cfg['costs']['expert_ns']=500
    save(s/'fresh-input.json',cfg)
    cli('fresh-topology','topology','--config',source/'examples/modern_deepep_native/topology.json','--output',s/'fresh-topology')
    cli('fresh-smoke','modern-deepep-native','--topology',s/'fresh-topology','--protocol',source/'examples/modern_deepep_native/rdma.json',
        '--config',s/'fresh-input.json','--backend',evidence/'runtime/run-transaction-session','--output',s/'fresh-smoke')
    result=load(s/'fresh-smoke/result.json');report=audit(s/'fresh-smoke')
    libraries=[Path(p) for p in result['loaded_libraries'] if Path(p).name.startswith('libns3')]
    assert len(libraries)==8 and all(p.parent==evidence/'runtime/lib' and sha(p)==sha(s/'runtime/lib'/p.name) for p in libraries)
    assert sha(evidence/'runtime/transaction-session')==sha(s/'runtime/transaction-session')
    save(s/'recovery.json',dict(passed=True,restored_source_root=str(source),cwd=str(cwd),offline_verification_byte_equal=True,
        four_original_routing_files_rechecked=True,new_network_executions=1,old_network_reexecutions=0,
        actual_bundled_ns3_libraries=[str(p) for p in libraries],smoke=report,
        required_four_rank_case_still_incomplete=True,scope='same-host ABI-bound migration; full stage acceptance remains incomplete'))


if __name__=='__main__':main()
