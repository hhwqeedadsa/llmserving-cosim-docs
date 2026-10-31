"""Prepare an inactive capacity proposal and exercise the public Python-only check.

No backend invocation is allowed here. This does not approve a capacity change
or establish native admission, network completion, or a successful fault test.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--backend',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();root=a.root.resolve();out=a.output.resolve()
    out.mkdir(parents=True,exist_ok=False)
    repo=Path(__file__).resolve().parents[2];backend=a.backend.resolve()
    load=lambda path:json.loads(path.read_bytes())
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    inventory=lambda directory:{str(path.relative_to(directory)):sha(path)
                                for path in directory.rglob('*') if path.is_file()}
    original=root/'inputs';before=inventory(original)
    retained_sha=sha(root/'normal/result.json');commands=[]

    def cli(name,args,expected=0):
        command=[sys.executable,'-B',*map(str,args)]
        result=subprocess.run(command,cwd=repo,capture_output=True,text=True,timeout=100)
        (out/(name+'-stdout.log')).write_text(result.stdout)
        (out/(name+'-stderr.log')).write_text(result.stderr)
        commands.append(dict(name=name,command=command,exit_code=result.returncode))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        assert (result.returncode==0) == (expected==0),(name,result.stderr)
        return result

    help_result=cli('help',['-m','moe_ub_cosim','scaleout','deepep-run','--help'])
    assert '--validate-only' in help_result.stdout
    proposed=out/'proposed-inputs'
    cli('prepare',['examples/legacy_native/prepare.py','--reference-events',100000,'--output',proposed])
    after=inventory(proposed)
    assert set(before)==set(after)
    changed=[name for name in before if before[name]!=after[name]]
    assert changed==['transport.json'],changed
    old=load(original/'transport.json');new=load(proposed/'transport.json')
    assert old['dcqcn_reference']['max_events']==10000
    expected=deepcopy(old);expected['dcqcn_reference']['max_events']=100000
    assert expected==new

    def arguments(inputs,case,destination):
        return ['-m','moe_ub_cosim','scaleout','deepep-run','--validate-only',
                '--plan',inputs/'plan','--binding',inputs/'binding.json',
                '--topology',inputs/'topology','--protocol',inputs/(case+'.json'),
                '--transport',inputs/'transport.json','--backend',backend,'--output',destination]

    rejected=out/'original-capacity-rejected'
    result=cli('original-capacity',arguments(original,'normal',rejected),expected=1)
    assert 'mandatory source sends=32464' in result.stderr
    assert not (rejected/'validation.json').exists()
    assert not (rejected/'result.json').exists() and not (rejected/'execution-monitor').exists()
    summaries={}
    for case in ('normal','fault'):
        destination=out/(case+'-validation')
        cli(case,arguments(proposed,case,destination))
        summary=load(destination/'validation.json');receipt=load(destination/'inputs.json')
        assert summary['passed'] and summary['validation_only']
        assert not summary['native_admission_executed'] and not summary['native_network_executed']
        for field,value in dict(endpoints=128,ep_size=32,experts=256,experts_per_rank=8,
                logical_operations=16448,logical_events=66144,carrier_streams=15872,
                carrier_work=31792,minimum_reference_source_events=32464,
                configured_reference_events=100000,minimum_posting_span=2,
                per_call_tokens=[4],per_call_data_active_ranks=[4]).items():
            assert summary[field]==value,(case,field)
        assert set(path.name for path in destination.iterdir())=={
            'inputs.json','plan-check.json','execution.json','validation.json'}
        assert receipt['validation_only'] and not receipt['native_network_executed']
        assert 'command' not in receipt and receipt['planned_native_command'][0]==str(backend)
        for name,expected_sha in receipt['hashes'].items():assert sha(Path(name))==expected_sha
        summaries[case]=summary
    assert (out/'normal-validation/execution.json').read_bytes()==(out/'fault-validation/execution.json').read_bytes()
    assert before==inventory(original) and retained_sha==sha(root/'normal/result.json')
    assert not (root/'fault').exists()
    evidence=dict(passed=True,capacity_change_approved=False,proposal_activated=False,
        original_reference_events=10000,proposed_reference_events=100000,
        changed_input_files=changed,original_inputs_unchanged=True,retained_failure_unchanged=True,
        native_admission_executed=False,new_network_executions=0,
        complete_legacy_execution_accepted=False,validation=summaries,
        original_input_sha256=before,proposed_input_sha256=after,
        retained_result_sha256=retained_sha,backend_sha256=sha(backend),
        source_sha256={name:sha(repo/name) for name in (
            'python/moe_ub_cosim/legacy_native.py','python/moe_ub_cosim/cli.py',
            'tests/unit/legacy_native_test.py','tests/integration/check_legacy_validate_only.py')},
        scope='Python/source-plan checks only; proposed inputs are inactive pending explicit confirmation')
    (out/'verification.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps(dict(passed=True,proposed_inputs_inactive=True,new_network_executions=0)))


if __name__=='__main__':main()
