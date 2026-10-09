"""Researcher-only dependency witnesses from public internal-facts exports.

A witness follows one latest predecessor, reporting ties and missing resource
ownership. Transfer intervals include admission/service/propagation; they are
not attributed queue delay or a claim about avoidable business time.
"""
import argparse
from collections import Counter
from pathlib import Path
import sys

from moe_ub_cosim.research_storage import stored_rows
from .compare import rows
from .workflow import digest, host, read, save


def witness(nodes, deliveries, key):
    path, seen = [], set()
    anchor = None
    while key in nodes:
        if (key,anchor) in seen:
            raise ValueError('Cyclic dependency witness')
        if len(path) >= 10000:
            raise ValueError('Witness exceeds declared 10000-step bound')
        seen.add((key,anchor))
        node = nodes[key]
        moment = node['end_ns'] if anchor is None else anchor
        if path and moment > path[-1]['anchor_ns']:
            raise ValueError('Dependency witness goes forward in time')
        deps = node['dependencies']
        ready = max((d['end_ns'] for d in deps),default=0)
        latest = [d for d in deps if d['end_ns']==ready]
        step = {k:node[k] for k in ('graph_id','rank_id','layer_id','node_id','kind','name',
            'resource_id','start_ns','end_ns','dependency_ready_ns','ready_to_start_ns','event','transfer_id')}
        step.update(anchor_ns=moment,anchor_is_submission=anchor is not None,
            latest_dependency_ties=latest,resource_wait_owner_observed=False)
        path.append(step)
        t = deliveries.get((key[0],node['transfer_id']))
        field = ('all_payload_ready_ns' if node['kind']=='RECV' or node['event']=='data_ready' else
                 'local_read_complete_ns' if node['event']=='local_read_complete' else None)
        if anchor is None and t is not None and field is not None:
            event_time = t[field]
            step['delivery_event'] = dict(field=field,time_ns=event_time,
                submitted_ns=t['submitted_ns'],local_read_complete_ns=t['local_read_complete_ns'],
                business_ready_ns=t['business_ready_ns'],application_metadata_ready_ns=t['application_metadata_ready_ns'],
                all_payload_ready_ns=t['all_payload_ready_ns'],protocol_retired_ns=t['protocol_retired_ns'],
                src_rank=t['src_rank'],dst_rank=t['dst_rank'],delivery=t['delivery'],
                kind=t['kind'],message_role=t['message_role'],business_payload_bytes=t['business_payload_bytes'])
            # If the callback arrived before the node could start, its wait is
            # already hidden by entry/dependencies and is not this branch.
            if event_time == moment and event_time > node['start_ns']:
                submit_key = key[0],t['src_rank'],t['send_node_id']
                if submit_key not in nodes:
                    step['boundary'] = 'sender outside selected facts'
                    break
                if nodes[submit_key]['start_ns'] != t['submitted_ns']:
                    raise ValueError('Actual SEND start differs from transfer submission')
                step['follow'] = 'actual transfer callback to sender submission, excluding later ACK retirement'
                key,anchor = submit_key,t['submitted_ns']
                continue
        if not latest:
            step['boundary'] = 'no declared predecessor'
            break
        selected = min(latest,key=lambda d:d['node_id'])
        step['follow'] = 'one latest direct predecessor; all tied identities retained'
        key,anchor = (key[0],key[1],selected['node_id']),None
        if key not in nodes:
            step['boundary'] = 'predecessor outside selected facts'
            step['outside_predecessor'] = dict(rank_id=key[1],node_id=key[2],end_ns=selected['end_ns'])
    return path


def inspect(facts, analysis, output):
    facts,analysis,output=map(Path,(facts,analysis,output))
    if output.exists():raise ValueError('Facts inspection output must be new')
    report=read(facts/'facts.json')
    if not report['passed'] or report['access_scope']!='internal_truth_not_observed':
        raise ValueError('Expected a completed internal-facts export')
    nodes={(n['graph_id'],n['rank_id'],n['node_id']):n for n in stored_rows(facts/'truth','nodes.jsonl')}
    deliveries={(t['graph_id'],t['transfer_id']):t for t in stored_rows(facts/'truth','deliveries.jsonl')}
    if len(nodes)!=report['selected_nodes'] or len(deliveries)!=report['selected_transfers']:
        raise ValueError('Facts coverage differs from sealed export')
    ranking=rows(analysis/'ranks.jsonl')
    targets=[]
    for lid in report['selection']['layer_ids']:
        candidates=[r for r in ranking if r['layer_id']==lid and r['kind']=='combine']
        if not candidates:raise ValueError('Missing Combine analysis')
        last=max(r['end_ns'] for r in candidates)
        hot=max(r['workload']['routed_inputs'] for r in candidates)
        for r in candidates:
            reasons=[]
            if r['end_ns']==last:reasons.append('last Combine finish')
            if r['workload']['routed_inputs']==hot:reasons.append('largest routed input count')
            if not reasons:continue
            keys=[key for key,n in nodes.items() if n['rank_id']==r['rank_id'] and n['layer_id']==lid
                and n['name']=='combine-operator-complete']
            if len(keys)!=1 or nodes[keys[0]]['end_ns']!=r['end_ns']:
                raise ValueError('Operation endpoint does not match actual completion node')
            path=witness(nodes,deliveries,keys[0])
            targets.append(dict(layer_id=lid,rank_id=r['rank_id'],selection_reasons=reasons,
                workload=r['workload'],combine_start_ns=r['start_ns'],combine_end_ns=r['end_ns'],
                steps=len(path),witness=path))
    save(output,dict(schema_version='issue-screening-dependency-witnesses/1',
        scope='researcher internal checks; not measurement input or complete critical-path attribution',
        facts_manifest_sha256=digest(facts/'facts.json'),analysis_summary_sha256=digest(analysis/'summary.json'),
        program_sha256=digest(Path(__file__)),targets=targets,
        caveats=['one latest dependency branch; ties retained',
            'ready_to_start does not identify the resource owner',
            'transfer callback interval is not exclusively switch queue waiting',
            'unselected predecessor nodes remain explicit boundaries',
            'overlapping paths or intervals must not be added as avoidable time']))
    print('dependency witnesses',[(t['layer_id'],t['rank_id'],t['steps']) for t in targets])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--facts',type=Path,required=True)
    p.add_argument('--analysis',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args()
    if a.worker:inspect(a.facts,a.analysis,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        report=probe([sys.executable,'-m','experiments.scaleup_issue_screening.inspect_facts','--worker',
            '--facts',str(a.facts.resolve()),'--analysis',str(a.analysis.resolve()),'--output',str(a.output.resolve())],
            str(a.output.resolve())+'-resources',**host(extra_roots=[a.facts,a.analysis,a.output.resolve().parent]))
        if report['status']!='completed':raise SystemExit('Facts inspection failed: '+report['status'])


if __name__=='__main__':main()
