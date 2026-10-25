"""Copied Python root/empty cwd and packaged runtime, fifth network only."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from fixtures.native_callback_batches.stage import load,save,sha


def main():
    p=argparse.ArgumentParser()
    for name in ('restored','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();root=a.restored.resolve();stage=a.stage.resolve()
    code=root/'source';evidence=root/'evidence';cwd=root/'empty-cwd'
    assert Path(__file__).resolve().is_relative_to(code)
    import moe_ub_cosim
    assert Path(moe_ub_cosim.__file__).resolve().is_relative_to(code/'python')
    env=dict(os.environ,PYTHONPATH=str(code/'python')+':'+str(code),PYTHONDONTWRITEBYTECODE='1');commands=[]
    def run(label,command):
        commands.append(dict(label=label,command=list(map(str,command)),cwd=str(cwd)))
        with (stage/(label+'.stdout')).open('w') as out,(stage/(label+'.stderr')).open('w') as err:
            subprocess.run(command,cwd=cwd,env=env,stdout=out,stderr=err,check=True,timeout=25)
    run('fresh-topology',[sys.executable,'-P','-B','-m','moe_ub_cosim','scaleout','topology',
        '--config',code/'examples/modern_deepep_native/topology.json','--output',root/'fresh-topology'])
    config=deepcopy(load(code/'examples/modern_deepep_native/input.json'))
    config['modern']['model']['hidden_size']=512
    config['modern']['calls']=[dict(name='fresh-cross-rank',tokens=[[[2,3]],[[0,1]]])]
    save(stage/'fresh-input.json',config)
    run('fresh-modern',[sys.executable,'-P','-B','-m','moe_ub_cosim','scaleout','modern-deepep-native',
        '--topology',root/'fresh-topology','--protocol',evidence/'inputs/synthetic-healthy-protocol.json',
        '--config',stage/'fresh-input.json','--backend',evidence/'runtime/run-transaction-session',
        '--output',stage/'fresh-smoke','--callback-batches'])
    from fixtures.native_callback_batches.verify import verify,delivery
    from fixtures.modern_native.audit import audit
    comparisons={}
    for case in ('default-healthy','batch-healthy','batch-fault','four-routes'):
        value=verify(evidence,case,captured_root=evidence/'runtime/lib')
        assert (json.dumps(value,indent=2)+'\n').encode()==(evidence/(case+'-verification.json')).read_bytes()
        save(stage/('fresh-'+case+'-verification.json'),value)
        comparisons[case]=sha(stage/('fresh-'+case+'-verification.json'))
    identity=load(evidence/'runtime-identity.json')
    assert all(sha(evidence/'runtime'/name)==digest for name,digest in identity['files'].items())
    save(stage/'recovery.json',dict(passed=True,commands=commands,fresh_modern_acceptance=audit(stage/'fresh-smoke'),
        fresh_delivery=delivery(stage/'fresh-smoke'),independent_actual_network_executions=1,
        four_complete_comparisons_byte_identical=comparisons,source_root=str(code),empty_cwd=str(cwd),
        fixed_runtime_and_eight_library_hashes_checked=True,representative_four_rank_call_complete=True,
        full_four_rank_physical_capture_complete=False,
        scope='same-host ABI-bound runtime/eight libraries and copied Python; no clean native build, GPU timing or thousand-GPU application'))


if __name__=='__main__':main()
