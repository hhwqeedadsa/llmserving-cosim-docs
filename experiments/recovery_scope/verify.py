"""Reject altered actual inputs/graphs/prefixes without changing saved runs."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import tempfile

from analyze import reconstruct,source_status
from dependencies import expanded
from prepare import files,load,save,sha

def rejected(name,action,records):
    try:action()
    except (AssertionError,KeyError,ValueError):records.append(dict(case=name,rejected=True))
    else:raise AssertionError('corruption accepted: '+name)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--frozen',type=Path,required=True)
    p.add_argument('--analysis',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    frozen=load(a.frozen);analysis=load(a.analysis);assert analysis['passed'];records=[]
    for condition,record in frozen['prefixes'].items():
        prefix=load(Path(record['path']));assert sha(Path(record['path']))==record['sha256']
        status=source_status(prefix,18000);assert status==analysis['source_only_status'][condition]
        for kind in ('message','rank','future','sequence','receive_is_not_source_confirmation','duplicate_post'):
            bad=deepcopy(prefix);first=bad['observations'][0]
            if kind=='message':first['message']=2147483647
            elif kind=='rank':first['rank']=127
            elif kind=='future':first['time_ns']=bad['deadline_ns']+1
            elif kind=='sequence':bad['observed_sequence']=-1
            elif kind=='receive_is_not_source_confirmation':first['kind']='message_recv_complete'
            else:bad['observations'].append(dict(first,sequence=bad['observed_sequence']+1,time_ns=bad['deadline_ns']))
            rejected(condition+'/'+kind,lambda:source_status(bad,18000),records)
    assert not analysis['source_only_status']['normal']['overdue'] and len(analysis['source_only_status']['local']['overdue'])==3
    original=Path(frozen['records']['guarded-local']['path']);c=load(original/'input.json')
    for kind in ('source_manifest_hash','declared_compute','graph_message_bytes','missing_gate_member'):
        with tempfile.TemporaryDirectory() as tmp:
            shadow=Path(tmp);cc=deepcopy(c);(shadow/'routes').symlink_to(original/'routes',target_is_directory=True)
            graph=load(original/'graphs/g0.w0/wave.json')
            if kind=='source_manifest_hash':cc['route_replay']['manifest']['sha256']='0'*64
            elif kind=='declared_compute':cc['compute']['expert_ns_per_contribution']+=1
            elif kind=='graph_message_bytes':graph['messages'][0]['bytes']+=64
            else:graph['phase_completion']['gates'][0]['members'].pop()
            (shadow/'input.json').write_text(json.dumps(cc))
            (shadow/'graphs/g0.w0').mkdir(parents=True);(shadow/'graphs/g0.w0/wave.json').write_text(json.dumps(graph))
            (shadow/'graphs/g0.w1').symlink_to(original/'graphs/g0.w1',target_is_directory=True)
            (shadow/'result.json').symlink_to(original/'result.json')
            if kind=='missing_gate_member':rejected(kind,lambda:expanded([graph]),records)
            else:rejected(kind,lambda:reconstruct(shadow,0),records)
    for record in frozen['records'].values():assert files(Path(record['path']))==record['sha256']
    graph=load(original/'graphs/g0.w0/wave.json');rows=load(original/'whole_call_report.json')['source_ack_guard']['decisions']
    local=next(d for d in rows if d['reason']=='source_timeout')
    assert local['time_ns']==analysis['trigger_ns'] and local['overdue_messages']==analysis['source_only_status']['local']['overdue']
    assert all(q['blocked_by_send_dependency'] and q['blocked_by_recv_dependency'] for q in analysis['per_request'])
    assert analysis['first_stage']['first_expert_issued_before_trigger']==analysis['first_stage']['first_expert_completed_before_trigger']==0
    save(a.output,dict(passed=True,rejections=records,negative_cases=len(records),read_only_networks_unchanged=5,
        source_predicate_matches_actual_local_decision=True,new_network_executions=0,verifier_sha256=sha(Path(__file__))))
    print(json.dumps(dict(passed=True,rejected=len(records),old_networks_unchanged=5,new_networks=0)))

if __name__=='__main__':main()
