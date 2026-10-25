"""Copied Python root/empty cwd and one independent small timed actual network."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from fixtures.session_host_timing.stage import load,save,sha


def main():
    p=argparse.ArgumentParser()
    for name in ('restored','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();root=a.restored.resolve();stage=a.stage.resolve()
    assert Path(__file__).resolve().is_relative_to(root/'source')
    import moe_ub_cosim
    assert Path(moe_ub_cosim.__file__).resolve().is_relative_to(root/'source/python')
    code=root/'source';evidence=root/'evidence';cwd=root/'empty-cwd';commands=[]
    env=dict(os.environ,PYTHONPATH=str(code/'python')+':'+str(code),PYTHONDONTWRITEBYTECODE='1')
    def run(label,command):
        commands.append(dict(label=label,command=list(map(str,command)),cwd=str(cwd)))
        with (stage/(label+'.stdout')).open('w') as stdout,(stage/(label+'.stderr')).open('w') as stderr:
            subprocess.run(command,cwd=cwd,env=env,stdout=stdout,stderr=stderr,check=True,timeout=25)
    run('fresh-topology',[sys.executable,'-P','-B','-m','moe_ub_cosim','scaleout','topology',
        '--config',code/'examples/modern_deepep_native/topology.json','--output',root/'fresh-topology'])
    config=deepcopy(load(code/'examples/modern_deepep_native/input.json'))
    config['modern']['model']['hidden_size']=512
    config['modern']['calls']=[dict(name='fresh-cross-rank',tokens=[[[2,3]],[[0,1]]])]
    from moe_ub_cosim.modern_native_layout import ModernNativeLayout
    ModernNativeLayout(load(root/'fresh-topology/topology.json'),load(evidence/'inputs/synthetic-healthy-protocol.json'),config)
    save(stage/'fresh-input.json',config)
    run('fresh-modern',[sys.executable,'-P','-B','-m','moe_ub_cosim','scaleout','modern-deepep-native',
        '--topology',root/'fresh-topology','--protocol',evidence/'inputs/synthetic-healthy-protocol.json',
        '--config',stage/'fresh-input.json','--backend',evidence/'runtime/run-transaction-session',
        '--output',stage/'fresh-smoke','--callback-wakeups','--host-timing'])
    run('fresh-public-summary',[sys.executable,'-P','-B','-m','moe_ub_cosim','scaleout','host-timing-check',
        '--run',stage/'fresh-smoke','--output',stage/'fresh-public-summary.json'])
    from fixtures.session_host_timing.verify import compare,summarize
    from fixtures.modern_native.audit import audit
    comparisons={}
    for case,name in (('default-healthy','default-equivalence'),('timed-healthy','timed-equivalence')):
        value=compare(evidence,case,captured_root=evidence/'runtime/lib')
        raw=(json.dumps(value,indent=2)+'\n').encode()
        assert raw==(evidence/(name+'.json')).read_bytes()
        save(stage/('fresh-'+name+'.json'),value);comparisons[case]=sha(stage/('fresh-'+name+'.json'))
    partial=summarize(evidence/'four-routes')
    assert (json.dumps(partial,indent=2)+'\n').encode()==(evidence/'four-host-summary.json').read_bytes()
    accepted=audit(stage/'fresh-smoke');trace=summarize(stage/'fresh-smoke')
    assert trace['service_complete'] and load(stage/'fresh-public-summary.json')['all_traces_complete']
    identity=load(evidence/'runtime-identity.json')
    assert all(sha(evidence/'runtime'/name)==digest for name,digest in identity['files'].items())
    save(stage/'recovery.json',dict(passed=True,commands=commands,fresh_source_root=str(code),empty_cwd=str(cwd),
        independent_actual_network_executions=1,fresh_modern_acceptance=accepted,
        complete_comparisons_byte_identical=comparisons,failure_summary_byte_identical=True,
        fixed_runtime_and_eight_library_hashes_checked=True,representative_four_rank_call_complete=False,
        scope='same-host ABI-bound runtime and copied Python source; fresh small call does not replace original four-rank acceptance'))


if __name__=='__main__':main()
