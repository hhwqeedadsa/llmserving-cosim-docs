"""Reject changed packet/callback/control facts without rerunning networks."""
import argparse
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import fixtures.session_host_timing.verify as oracle
from fixtures.session_host_timing.stage import load,save


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);a=p.parse_args();stage=a.stage.resolve()
    directory=stage/'timed-healthy';result=load(directory/'result.json');modern=load(directory/'modern-execution.json')
    edits={
        'finish':('result.json',lambda v:v.update(finish_ns=v['finish_ns']+1)),
        'physical_packet':('result.json',lambda v:v['packet_capture']['events'][0].__setitem__(0,v['packet_capture']['events'][0][0]+1)),
        'callback_loss':('result.json',lambda v:v['session']['events'].pop()),
        'registration_decision':('result.json',lambda v:v['session']['registrations'][0].update(decision_id=255)),
        'modern_time':('modern-execution.json',lambda v:v['events'][0].update(time_ns=99999)),
        'native_operation':('modern-execution.json',lambda v:v['native_operations'][0].update(duration_ns=99999)),
    }
    rejected=[]
    for name,(file,edit) in edits.items():
        changed=deepcopy(result if file=='result.json' else modern);edit(changed);target=(directory/file).resolve()
        def read(path):return deepcopy(changed) if Path(path).resolve()==target else load(path)
        with patch.object(oracle,'load',side_effect=read):
            try:oracle.compare(stage,'timed-healthy')
            except AssertionError:rejected.append(name)
            else:raise AssertionError('changed evidence accepted: '+name)
    save(stage/'checks.json',dict(passed=True,network_executed=False,rejected=rejected))


if __name__=='__main__':main()
