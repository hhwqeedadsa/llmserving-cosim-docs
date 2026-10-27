"""Explain the retained comparison failure; never change inputs or rerun networks."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from experiments.compact_network_choice.prepare import load, save, sha
from experiments.static_dag_transfer.diagnose_event_graph import semantic_identity, replace_operation_keys


def canonical_after(node):
    node = deepcopy(node)
    after = node['after']
    assert len({(p['operation'],p['event']) for p in after}) == len(after)
    node['after'] = sorted(after,key=lambda p:(p['operation'],p['event']))
    return node


def diagnose(stage, repo):
    root = stage/'truth/batch8'; comparison = load(root/'propagation/comparison.json')
    assert not comparison['equivalent']
    assert {k for k,v in comparison['checks'].items() if not v} == {'graph_work_and_after','productive_commits'}
    baseline = load(root/'baseline/modern/modern-execution.json')
    detailed = load(root/'propagation/modern/modern-execution.json')
    lookup = {semantic_identity(t):int(i) for i,t in baseline['native_tasks'].items()}
    mapping = {int(i):lookup[semantic_identity(t)] for i,t in detailed['native_tasks'].items()}
    expected = {r['id']:r for r in baseline['native_operations']}; mismatches = []
    for node in replace_operation_keys(detailed['native_operations'],mapping):
        node['id'] = mapping[node['id']]; old = expected[node['id']]
        assert canonical_after(node) == canonical_after(old)
        if node != old:
            assert {k for k in old.keys()|node.keys() if old.get(k)!=node.get(k)} == {'after'}
            mismatches.append(dict(work=node['id'],task=baseline['native_tasks'][str(node['id'])],
                baseline_after=old['after'],renamed_after=node['after']))
    assert len(mismatches)==4
    def commits(path,mapped):
        result=[]
        for line in path.read_text().splitlines():
            row=json.loads(line)
            if row['direction']!='frontend':continue
            m=row['message']
            if not m['submissions'] and not m['finish']:continue
            nodes=deepcopy(m['submissions'])
            if mapped:
                nodes=replace_operation_keys(nodes,mapping)
                for node in nodes:node['id']=mapping[node['id']]
            result.append(dict(time_ns=m['now_ns'],nodes=[canonical_after(n) for n in nodes],finish=m['finish']))
        return result
    assert commits(root/'propagation/modern/control.jsonl',True)==commits(root/'propagation/generic/control.jsonl',False)
    source = repo/'backend/rdma/transaction_session.cc'
    lines=source.read_text().splitlines()
    matches=[dict(line=i+1,text=line.strip()) for i,line in enumerate(lines)
             if 'ready=ready && Occurred(d.at("operation"),d.at("event"))' in line]
    assert len(matches)==1
    return dict(passed=True,outcome='only_order_of_conjunctive_after_list_changed_under_identity_renaming',
        original_comparison_failure_preserved=True,changed_work=4,mismatches=mismatches,
        equal_after_set_and_all_other_fields=True,equal_productive_commits_after_set_normalization=True,
        native_gate_source=dict(path=str(source.relative_to(repo)),sha256=sha(source),excerpt=matches),
        application_packets_queues_callbacks_and_memory_already_equal=True,new_networks=0,
        new_graph_or_predictor_changes=0,strict_gate_not_reopened=True)


def main():
    p=argparse.ArgumentParser()
    for name in ('stage','repository'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();save(a.stage/'diagnosis.json',diagnose(a.stage,a.repository))


if __name__=='__main__':main()
