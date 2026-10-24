"""Re-audit strengthened registration identities and reject evidence tampering."""
import argparse
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import fixtures.native_wakeup.verify as oracle
from fixtures.native_wakeup.stage import load, save


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',type=Path,required=True)
    args=parser.parse_args();stage=args.stage.resolve()
    rechecked={case:oracle.verify(stage,case) for case in ('default-healthy','wake-healthy','wake-fault')}
    directory=stage/'wake-healthy'
    result=load(directory/'result.json');modern=load(directory/'modern-execution.json')
    edits={
        'wrong_finish_time':('result.json',lambda r:r.update(finish_ns=r['finish_ns']+1)),
        'lost_native_callback':('result.json',lambda r:r['session']['events'].pop()),
        'wrong_registration_decision':('result.json',lambda r:r['session']['registrations'][0].update(decision_id=255)),
        'changed_physical_packet':('result.json',lambda r:r['packet_capture']['events'][0].__setitem__(0,r['packet_capture']['events'][0][0]+1)),
        'changed_modern_time':('modern-execution.json',lambda r:r['events'][0].update(time_ns=1)),
        'changed_native_submission':('modern-execution.json',lambda r:r['native_operations'][0].update(value=99)),
    }
    rejected=[]
    for name,(file,edit) in edits.items():
        original=result if file=='result.json' else modern
        changed=deepcopy(original);edit(changed);assert changed!=original
        target=(directory/file).resolve()
        def read(path):
            return deepcopy(changed) if Path(path).resolve()==target else load(path)
        with patch.object(oracle,'load',side_effect=read):
            try:oracle.verify(stage,'wake-healthy')
            except AssertionError:rejected.append(name)
            else:raise AssertionError('evidence tamper accepted: '+name)
    save(stage/'checks.json',dict(passed=True,network_executed=False,
        strengthened_registration_check=True,rechecked_cases=rechecked,rejected_evidence_tampering=rejected,
        control=load(stage/'control-verification.json'),four_request_acceptance_complete=False))


if __name__=='__main__':main()
