"""Select bounded network candidates and inspect their archived business gates.

Internal researcher analysis only. Queue ranking is frozen before this reader
consults transfer identities or completion dependencies.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
import sys

from .consumer_review import Archive, completion_conditions
from .network_screen import RULE, rank_key
from .organization import zipped_rows
from .workflow import digest, host, read, save


def select(summaries, output):
    values = [read(p) for p in summaries]
    if sorted(v['case_id'] for v in values) != sorted(RULE['sources']):
        raise ValueError('Selection requires all four declared normal sources')
    if any(v['rule'] != RULE or not v['complete_declared_coverage'] for v in values):
        raise ValueError('Screening rule or coverage differs')
    selected = []
    for q in RULE['candidate_queues']:
        rows = sorted([r for v in values for r in v['eligible_top_by_queue'][q]], key=rank_key)
        if not rows:
            continue
        first = rows[0]
        same = next((e for e in selected if e['case_id']==first['case_id'] and
            (e['switch_id'],e['port_id'])==(first['switch_id'],first['port_id']) and
            e['start_ns']<first['end_ns'] and first['start_ns']<e['end_ns']),None)
        if same:
            same['also_selected_queue'] = first
        else:
            selected.append(dict(candidate_id='N'+str(len(selected)+1),**first))
    result=dict(schema_version='issue-network-candidate-selection/1',rule=RULE,candidates=selected,
        sources={v['case_id']:dict(summary_sha256=digest(p),archive_sha256=v['archive_sha256'],
            ports=v['observed_ports'],groups=v['event_groups'],complete_order_sha256=v['events_sha256'])
                 for p,v in zip(summaries,values)},
        selection_precedes_business_review=True,program_sha256=digest(__file__),
        rejected='Other ranked events remain in each complete compressed ordering; only one representative per queue class is deepened.')
    save(output,result)
    print(json.dumps(selected))


def reference(selection, screening, output):
    choice=read(selection);summary=read(screening/'summary.json')
    if (choice['sources'][summary['case_id']]['summary_sha256']!=digest(screening/'summary.json') or
            digest(screening/'events.jsonl.gz')!=summary['events_sha256']):
        raise ValueError('Auxiliary screening identity differs')
    selected={}
    with gzip.open(screening/'events.jsonl.gz','rt') as stream:
        for line in stream:
            row=json.loads(line)
            for c in choice['candidates']:
                if c['candidate_id'] in selected:continue
                if ((row['switch_id'],row['port_id'],row['queue'])==
                        (c['switch_id'],c['port_id'],c['queue']) and any(
                            x in row['overlapping_operations'] for x in c['overlapping_operations'])):
                    selected[c['candidate_id']]=dict(candidate_id=c['candidate_id'],**row)
    if set(selected)!={c['candidate_id'] for c in choice['candidates']}:
        raise ValueError('No matching normal reference for a selected outlet/phase')
    save(output,dict(choice,candidates=list(selected.values()),discovery_candidates=choice['candidates'],
        discovery_selection_sha256=digest(selection),reference_selection_program_sha256=digest(__file__),
        reference_scope='Same directed ports and layer/operation in an existing smaller normal case; tests a shared mechanism, not the discovery-case causal effect',
        reason='Reuse an existing compact slice within the fixed cumulative disk budget; do not regenerate or alter business inputs'))


def recurrence(selection, summaries, output):
    """Same outlets/semantic operation across normal inputs; no new selection."""
    choice=read(selection);rows=[]
    for summary_path in summaries:
        summary=read(summary_path);case=summary['case_id'];root=summary_path.parent
        if choice['sources'][case]['archive_sha256']!=summary['archive_sha256'] or \
                digest(root/'events.jsonl.gz')!=summary['events_sha256']:
            raise ValueError('Recurrence source changed')
        found={}
        with gzip.open(root/'events.jsonl.gz','rt') as stream:
            for line in stream:
                event=json.loads(line)
                for c in choice['candidates']:
                    key=c['candidate_id']
                    if key in found:continue
                    if ((event['switch_id'],event['port_id'],event['queue'])==
                        (c['switch_id'],c['port_id'],c['queue']) and any(
                            o in event['overlapping_operations'] for o in c['overlapping_operations'])):
                        found[key]=event
        rows.extend(dict(candidate_id=c['candidate_id'],case_id=case,
            highest_matching_event=found.get(c['candidate_id']),source_sha256=digest(summary_path))
            for c in choice['candidates'])
    save(output,dict(schema_version='issue-network-recurrence/1',selection_sha256=digest(selection),
        rows=rows,program_sha256=digest(__file__),
        limits=['Existing ranked groups matched by outlet, queue and semantic operation, not equal wall time.',
            'A null event means no matching positive group within declared coverage; it is not an unobserved-device zero.',
            'Nested request sets and longer-prefix execution are related references, not independent replication.',
            'Aggregate recurrence is not packet membership or causal business impact.']))


def facts(archive, selection, analysis, output):
    source=Archive(archive);choice=read(selection);baseline=read(analysis/'summary.json')
    if output.exists():raise ValueError('Facts output must be new')
    candidates=[c for c in choice['candidates'] if c['case_id']==baseline['case_id']]
    if not candidates or choice['sources'][baseline['case_id']]['archive_sha256']!=source.index['archive_sha256']:
        raise ValueError('Candidate/source binding differs')
    names=['run/inputs/network/backend.json','run/raw/graphs/g0/plan.json',
           'run/raw/execution/result.json','run/raw/execution/records/endpoint_ops-g0.jsonl.gz',
           'run/raw/execution/records/node_execution-g0.jsonl.gz',
           'run/raw/execution/records/planned_transfers-g0.jsonl.gz',
           'run/raw/execution/records/transfers-g0.jsonl.gz',
           'run/raw/execution/records/local_deliveries-g0.jsonl.gz']
    # Network configuration precedes execution records in these sealed archives.
    network=None;report=None;plan=None;times=defaultdict(dict);planned={};actual={};endpoints={}
    ranks={r for o in baseline['operations'] if o['layer_id'] in (7,8) for r in o['last_finishing_ranks']}
    targets=[]
    for name,data in source.members(names):
        if name.endswith('/backend.json'):
            network=json.loads(data)
            for c in candidates:
                links=[dict(link=l,side=s,other=t) for l in network['topology']['links']
                    for s,t in (('src','dst'),('dst','src')) if (l[s],l[s+'_port'])==(c['switch_id'],c['port_id'])]
                if len(links)!=1:raise ValueError('Selected directed port is not uniquely connected')
                x=links[0];peer=x['link'][x['other']]
                # Device mapping is checked against the result when available.
                ranks.add(peer)
                targets.append(dict(candidate=c,peer_node=peer,link=x['link']))
        elif name.endswith('/result.json'):
            report=json.loads(data)
            mapping=dict(zip(report['rank_to_ub_node'],report['rank_ids']))
            for t in targets:
                if t['peer_node'] not in mapping:raise ValueError('Selected outlet is not an active receiver; use a transit-path review')
                if mapping[t['peer_node']]!=t['peer_node']:
                    raise ValueError('This archived endpoint review requires the checked identity rank/node mapping')
                t['receiver_rank']=mapping[t['peer_node']];ranks.add(t['receiver_rank'])
        elif name.endswith('/plan.json'):
            p=json.loads(data)
            plan={g['rank_id']:g for g in p['rank_graphs'] if g['rank_id'] in ranks}
            graph_id=p['graph_id'];del p
        else:
            for row in zipped_rows(data):
                if '/node_execution-' in name:
                    if row['rank_id'] in ranks:times[row['rank_id']][row['node_id']]=row
                elif '/endpoint_ops-' in name:endpoints[row['rank_id'],row['local_record_id']]=row
                elif '/planned_transfers-' in name:
                    if row['src_rank'] in ranks or row['dst_rank'] in ranks:planned[row['transfer_id']]=row
                else:
                    if row['src_rank'] in ranks or row['dst_rank'] in ranks:actual[row['transfer_id']]=row
        del data
    if network is None or report is None or plan is None or not report['normal_exit']:
        raise ValueError('Missing validated execution/plan')
    output.mkdir(parents=True)
    reviews=[]
    for op in baseline['operations']:
        if op['layer_id'] not in (7,8):continue
        relevant=set(op['last_finishing_ranks'])
        relevant.update(t['receiver_rank'] for t in targets)
        for rank in sorted(relevant):
            g=plan[rank];nodes={n['node_id']:n for n in g['nodes']};ts=times[rank]
            if set(nodes)!=set(ts):raise ValueError('Selected rank execution coverage mismatch')
            finishes=[n for n in nodes.values() if n.get('layer_id')==op['layer_id'] and
                      n['name']==op['kind']+'-operator-complete']
            if len(finishes)!=1:raise ValueError('Missing unique operation finish')
            finish=finishes[0]
            gates=completion_conditions(nodes,ts,planned,actual,finish['node_id'],op['layer_id'],kind=op['kind'])
            # All same-layer phase nodes, including non-latest branches, remain
            # in the compact facts archive for independent completion checking.
            following=[dict(stage=s['stage'],layer_id=s['layer_id'],**ts[s['node_id']])
                for s in g['model_stages'] if s['node_id'] is not None and s['layer_id']==op['layer_id']]
            phase=next(p for p in g['layer_phases'] if p['layer_id']==op['layer_id'])
            expert=ts[phase['phase_nodes']['expert']]
            reviews.append(dict(operation_id=op['operation_id'],kind=op['kind'],layer_id=op['layer_id'],
                rank_id=rank,is_actual_last=rank in op['last_finishing_ranks'],
                all_actual_last=op['last_finishing_ranks'],completion=gates,model_stages=following,
                expert_compute=expert,node_names=dict(Counter(
                    n['name'] for n in nodes.values() if n.get('layer_id')==op['layer_id']))))
    with gzip.open(output/'selected-facts.json.gz','wt',compresslevel=6) as stream:
        json.dump(dict(graph_id=graph_id,rank_graphs=plan,node_execution=dict(times),
                       planned_transfers=planned,actual_transfers=actual),stream,separators=(',',':'))
    result=dict(schema_version='issue-network-business-facts/1',archive_sha256=source.index['archive_sha256'],
        archive_members=source.used,selection_sha256=digest(selection),analysis_sha256=digest(analysis/'summary.json'),
        program_sha256=digest(__file__),completion_program_sha256=digest(Path(__file__).with_name('consumer_review.py')),targets=targets,operations=reviews,
        selected_ranks=sorted(ranks),facts_sha256=digest(output/'selected-facts.json.gz'),
        simulation_end_ns=report['simulation_end_ns'],service_finished_ns=report['service_finished_ns'],
        graph_completions=report['graph_completions'],network_drain=report['network_drain'],
        actual_packet_membership_verified=False,
        limits=['Aggregate co-occurrence is not a per-packet path/wait or a causal business loss',
                'Dispatch latest gates are traversed; full same-layer nodes retained for all-gate follow-up'])
    save(output/'summary.json',result)
    print(json.dumps(dict(targets=targets,selected_ranks=sorted(ranks),operations=len(reviews))))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('select','reference','recurrence','facts'))
    p.add_argument('--summaries',type=Path,nargs='+');p.add_argument('--archive',type=Path)
    p.add_argument('--selection',type=Path);p.add_argument('--analysis',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.action=='select':select(a.summaries,a.output)
    elif a.action=='reference':reference(a.selection,a.analysis,a.output)
    elif a.action=='recurrence':recurrence(a.selection,a.summaries,a.output)
    elif a.worker:facts(a.archive,a.selection,a.analysis,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],
            str(a.output.resolve())+'-resources',**host(extra_roots=[a.output]))
        if r['status']!='completed':raise SystemExit('Network facts failed: '+r['status'])


if __name__=='__main__':main()
