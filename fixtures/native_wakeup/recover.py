"""Fresh source root, empty cwd, one independent small actual modern network."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from fixtures.native_wakeup.stage import load, save, sha


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--restored',type=Path,required=True)
    parser.add_argument('--stage',type=Path,required=True)
    parser.add_argument('--resume-admission',action='store_true')
    args=parser.parse_args();root=args.restored.resolve();stage=args.stage.resolve()
    import moe_ub_cosim
    assert Path(moe_ub_cosim.__file__).resolve().is_relative_to(root/'source/python')
    evidence=root/'evidence';code=root/'source';cwd=root/'empty-cwd'
    env=dict(os.environ,PYTHONPATH=str(code/'python')+':'+str(code),PYTHONDONTWRITEBYTECODE='1')
    commands=[]
    def run(label,command):
        commands.append(dict(label=label,command=list(map(str,command)),cwd=str(cwd)))
        with (stage/(label+'.stdout')).open('w') as stdout,(stage/(label+'.stderr')).open('w') as stderr:
            subprocess.run(command,cwd=cwd,env=env,stdout=stdout,stderr=stderr,check=True,timeout=25)
    topology=root/'fresh-topology'
    if not args.resume_admission:
        run('fresh-topology',[sys.executable,'-P','-B','-m','moe_ub_cosim','scaleout','topology',
            '--config',code/'examples/modern_deepep_native/topology.json','--output',topology])
    else:
        assert load(stage/'fresh-input-admission-rejection.json')['native_network_started'] is False
        assert sha(topology/'topology.json')==sha(evidence/'topology/topology.json')
        assert not (stage/'fresh-smoke').exists()
    fresh=deepcopy(load(code/'examples/modern_deepep_native/input.json'))
    fresh['modern']['model']['hidden_size']=512
    fresh['modern']['calls']=[dict(name='fresh-cross-rank',tokens=[[[2,3]],[[0,1]]])]
    config=stage/('fresh-input-corrected.json' if args.resume_admission else 'fresh-input.json')
    from moe_ub_cosim.modern_native_layout import ModernNativeLayout
    ModernNativeLayout(load(topology/'topology.json'),load(evidence/'inputs/synthetic-healthy-protocol.json'),fresh)
    assert not config.exists();save(config,fresh)
    run('fresh-modern-corrected' if args.resume_admission else 'fresh-modern',[sys.executable,'-P','-B','-m','moe_ub_cosim','scaleout','modern-deepep-native',
        '--topology',topology,'--protocol',evidence/'inputs/synthetic-healthy-protocol.json',
        '--config',config,'--backend',evidence/'runtime/run-transaction-session',
        '--output',stage/'fresh-smoke','--callback-wakeups'])
    from fixtures.modern_native.audit import audit
    from fixtures.native_wakeup.verify import verify,delivery
    accepted=audit(stage/'fresh-smoke');fresh_delivery=delivery(stage/'fresh-smoke')
    original_checks=load(evidence/'checks.json')['rechecked_cases']
    comparisons={}
    for case in ('default-healthy','wake-healthy','wake-fault'):
        value=verify(evidence,case,captured_library_root=evidence/'runtime/lib')
        raw=(json.dumps(value,indent=2)+'\n').encode()
        expected=(json.dumps(original_checks[case],indent=2)+'\n').encode()
        assert raw==expected,'fresh independent verification changed'
        save(stage/('fresh-'+case+'-verification.json'),value)
        import hashlib
        comparisons[case]=hashlib.sha256(raw).hexdigest()
    identity=load(evidence/'runtime-identity.json')
    assert all(sha(evidence/'runtime'/name)==value for name,value in identity['files'].items())
    save(stage/'recovery.json',dict(passed=True,fresh_python_root=str(code/'python'),empty_cwd=str(cwd),
        commands=commands,original_three_verifications_byte_identical=comparisons,
        fresh_modern_acceptance=accepted,fresh_delivery=fresh_delivery,network_executions=1,
        fixed_runtime_and_eight_library_hashes_checked=True,four_request_full_call_passed=False,
        prior_admission_rejection_preserved=args.resume_admission,
        scope='same-host ABI-bound runtime and copied Python source; no clean build or calibrated full inference'))


if __name__=='__main__':main()
