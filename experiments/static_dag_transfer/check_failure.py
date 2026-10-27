"""Reject evidence tampering; no network or native runtime execution."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import tempfile

from experiments.static_dag_transfer.analyze_failure import analyze, save


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();assert not a.output.exists()
    baseline=analyze(a.stage)
    names=['reference/baseline/result.json','baseline/result.json','reference/baseline/modern-execution.json',
           'reference/baseline/modern-plan.json','program.json','execution.json',
           'reference/baseline/control.jsonl','baseline/control.jsonl']
    blobs={n:(a.stage/n).read_bytes() for n in names}
    cases=[('false_success','baseline/result.json',lambda v:v.update(passed=True)),
           ('missing_limit','baseline/result.json',lambda v:v['session'].update(decisions=255)),
           ('changed_packet_time','baseline/result.json',lambda v:v['transactions']['transport']['source_packets'][24].__setitem__(0,6147)),
           ('changed_payload','baseline/result.json',lambda v:v['transactions']['transport']['source_payload_packets'][24].update(hex='00')),
           ('missing_semantic_gate','reference/baseline/modern-plan.json',lambda v:next(e for e in v['events'] if e['id']=='m0.dispatch.expert_count.s0.d1.issued').update(deps=[])),
           ('unrecorded_extra_network','execution.json',lambda v:v.update(network_executions=2))]
    rejected=[]
    with tempfile.TemporaryDirectory() as t:
        root=Path(t)
        for name,raw in blobs.items():
            target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        assert analyze(root)==baseline
        for label,name,mutate in cases:
            value=json.loads(blobs[name]);mutate(value);save(root/name,value)
            try:analyze(root)
            except (AssertionError,ValueError,KeyError,StopIteration):rejected.append(label)
            else:raise AssertionError('tampered evidence accepted: '+label)
            (root/name).write_bytes(blobs[name])
    save(a.output,dict(passed=True,rejected=rejected,unchanged_input_reanalysis_equal=True,new_networks=0))
    print('Six tampering classes rejected')


if __name__=='__main__':main()
