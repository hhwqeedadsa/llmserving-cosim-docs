"""Small offline information check, with separate preparation, judgment and evaluation.

The judgment only bounds possible incoming application operations. It does not
infer criticality, packet paths, removable delay or a causal business loss.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
import sys

from .workflow import digest, host, read, save


RULE = {
    'question': 'Which incoming application operations can have packets waiting at this device-facing outlet/window?',
    'bounds': 'positive declared src/dst payload; source operation start < window end; destination operation end > window start',
    'conditions': ['endpoint_workload_topology', 'plus_public_queue'],
    'scope': 'all windows of the two selected outlets in the sealed passive capture range',
    'primary': 'previously selected peak windows; all other windows are related-case checks',
    'not_inferred': ['actual path', 'application presence from a queue alone', 'critical dependency', 'business loss'],
}


def rows(path):
    with gzip.open(path, 'rt') as stream:
        for line in stream:
            yield json.loads(line)


def prepare(case, packets, output):
    from moe_ub_cosim.record_storage import iter_result
    if output.exists():
        raise ValueError('Information output must be new')
    selection = read(case/'selection.json')
    if selection['intervention'] is not None or not read(case/'equivalence.json')['passed']:
        raise ValueError('Use a verified passive baseline')
    if read(case/'equivalence.json')['replay_manifest_sha256'] != digest(case/'run/manifest.json'):
        raise ValueError('Equivalence seal differs')
    ps = read(packets/'summary.json')
    if ps['packets_sha256'] != digest(packets/'packets.jsonl.gz'):
        raise ValueError('Packet evidence differs')
    if ps['manifest_sha256'] != digest(case/'run/manifest.json'):
        raise ValueError('Packet evidence belongs to another run')
    obs = read(case/'observation.json')
    if any(c['offset_ns'] or c['drift_ppb'] for c in obs['clocks']):
        raise ValueError('This finite check requires unchanged aligned clocks')
    ex = case/'run/raw/execution'; report = read(ex/'result.json')
    raw = {(r['rank_id'], r['local_record_id']): r for r in iter_result(ex, 'endpoint_ops', report)}
    endpoints = []; meanings = {}; times = {}
    for i, op in enumerate(iter_result(ex, 'operation_registry', report)):
        oid = f'o{i:03d}'; meanings[op['operation_id']] = oid
        for m in op['endpoint_records']:
            row = raw.pop((m['rank_id'], m['local_record_id']))
            item = dict(operation=oid, rank=m['rank_id'], start_ns=row['start_local_ns'], end_ns=row['end_local_ns'])
            endpoints.append(item); times[op['operation_id'], m['rank_id']] = item
    if raw:
        raise ValueError('Endpoint coverage mismatch')
    planned = {r['transfer_id']: r for r in iter_result(ex, 'planned_transfers', report)}
    totals = Counter(); checked = 0
    for actual in iter_result(ex, 'transfers', report):
        t = planned[actual['transfer_id']]; op = t['operation_id']
        # Preparation validates the time-bound semantics; actual times stay private.
        if (times[op, t['src_rank']]['start_ns'] > actual['send_start_ns'] or
                times[op, t['dst_rank']]['end_ns'] < actual['data_ready_ns']):
            raise ValueError('Declared endpoint bounds do not cover this template')
        checked += 1
    for t in planned.values():
        if t['src_rank'] != t['dst_rank']:
            totals[meanings[t['operation_id']], t['src_rank'], t['dst_rank']] += t['payload_bytes']
    flows = [dict(operation=k[0], src=k[1], dst=k[2], payload_bytes=v) for k, v in sorted(totals.items())]
    network = read(case/'run/inputs/network/backend.json'); peers = {}
    node_rank = dict(zip(report['rank_to_ub_node'], report['rank_ids']))
    targets = {(c['switch_id'], c['port_id']): c for c in selection['discovery']['candidates']}
    for link in network['topology']['links']:
        for a, b in (('src', 'dst'), ('dst', 'src')):
            key = link[a], link[a+'_port']
            if key in targets:
                peers[key] = node_rank[link[b]]
    public = read(case/'run/observed/public_config.json')
    storage = read(case/'run/observed/storage.json')
    if storage['window_encoding'] != 'nonzero_with_declared_zero_windows':
        raise ValueError('Omitted windows must be explicitly declared zero')
    configs = {(p['switch_id'], p['port_id']): p for p in public['ports']}
    windows = {}; labels = []
    for r in rows(case/'run/observed/records/queue_windows.csv.gz'):
        key = r['switch_id'], r['port_id'], r['local_window_start_ns']
        if key[:2] in targets:
            windows[key] = r
    for key, c in sorted(targets.items()):
        conf = configs[key]; period = conf['period_ns']
        if selection['detail_start_ns'] % period or selection['detail_end_ns'] % period:
            raise ValueError('Capture boundaries must align with public windows')
        for start in range(selection['detail_start_ns'], selection['detail_end_ns'], period):
            r = windows.get((*key, start), {})
            if start < conf['start_local_ns'] or start+period > conf['end_local_ns']:
                raise ValueError('Sample outside public coverage')
            labels.append(dict(sample=f's{len(labels):04d}', switch_id=key[0], port_id=key[1],
                receiver=peers[key], start_ns=start, end_ns=start+period,
                voq_byte_ns=r.get('voq_waiting_byte_ns', 0), fifo_byte_ns=r.get('fifo_waiting_byte_ns', 0)))
    # No candidate identity, semantic answer, raw path, intervention or dependency enters input.
    public_input = dict(schema_version='issue-network-information-input/1', rule=RULE,
        time_resolution_ns=public['clock_resolution_ns'], endpoints=endpoints, flows=flows, windows=labels)
    output.mkdir(parents=True); save(output/'input.json', public_input)
    packets_by_port = defaultdict(list)
    for p in rows(packets/'packets.jsonl.gz'):
        packets_by_port[p['switch_id'], p['port_id']].append(p)
    truth = []
    for w in labels:
        actual = set(); fifo = set(); partial = set(); unavailable = 0; protocol_fifo = 0
        for p in packets_by_port[w['switch_id'], w['port_id']]:
            if 'fifo_enqueue_ns' not in p:
                raise ValueError('Incomplete FIFO evidence')
            fifo_overlap = min(w['end_ns'], p['fifo_dequeue_ns']) > max(w['start_ns'], p['fifo_enqueue_ns'])
            if 'operation_id' not in p:
                protocol_fifo += fifo_overlap
                continue
            oid = meanings[p['operation_id']]
            if fifo_overlap:
                fifo.add(oid)
            if 'upstream' in p:
                if min(w['end_ns'], p['fifo_enqueue_ns']) > max(w['start_ns'], p['upstream']['arrival_ns']):
                    partial.add(oid)
            elif w['start_ns'] <= p['fifo_enqueue_ns'] < w['end_ns']:
                unavailable += 1
        actual = fifo | partial
        c = targets[w['switch_id'], w['port_id']]
        truth.append(dict(sample=w['sample'], known_waiting_operations=sorted(actual),
            complete_fifo_operations=sorted(fifo), partial_arrival_to_fifo_operations=sorted(partial),
            app_packets_enqueued_without_upstream=unavailable, protocol_packets_with_fifo_wait=protocol_fifo,
            primary=w['start_ns'] == c['peak_window_start_ns'], candidate=c['candidate_id']))
    save(output/'truth.json', dict(input_sha256=digest(output/'input.json'), samples=truth,
        operation_meanings=meanings, bound_checked_network_transfers=checked,
        preparation_sources={str(p):digest(p) for p in (case/'selection.json', case/'run/manifest.json', packets/'summary.json')},
        program_sha256=digest(__file__),
        boundary='FIFO truth complete; arrival-to-FIFO truth covers captured upstream only. Missing is not zero. Protocol packets are not application operations.'))


def prepare_mapping(case, input_path, truth_path, prediction, output):
    """Export one ideal relation only, after the baseline judgment is saved."""
    if output.exists():
        raise ValueError('Mapping output must be new')
    data, truth, baseline = read(input_path), read(truth_path), read(prediction)
    identity = digest(input_path)
    if truth['input_sha256'] != identity or baseline['input_sha256'] != identity:
        raise ValueError('Mapping source differs from saved baseline')
    source = str(case/'run/manifest.json')
    if truth['preparation_sources'].get(source) != digest(case/'run/manifest.json'):
        raise ValueError('Mapping case differs from preparation')
    start = min(w['start_ns'] for w in data['windows']); end = max(w['end_ns'] for w in data['windows'])
    ports = {(w['switch_id'],w['port_id']) for w in data['windows']}; relation = set()
    for row in rows(case/'run/truth/records/message_attribution_windows.csv.gz'):
        if ((row['switch_id'],row['port_id']) in ports and row['application_bytes'] > 0
                and start <= row['local_window_start_ns'] < row['local_window_end_ns'] <= end):
            relation.add((truth['operation_meanings'][row['operation_id']],row['switch_id'],row['port_id'],
                          row['local_window_start_ns'],row['local_window_end_ns']))
    save(output, dict(schema_version='issue-network-ideal-relation/1',input_sha256=identity,
        start_ns=start,end_ns=end,records=[dict(zip(('operation','switch_id','port_id','start_ns','end_ns'),r))
                                        for r in sorted(relation)]))


def judge_input(data, mapping=None):
    if set(data) != {'schema_version', 'rule', 'time_resolution_ns', 'endpoints', 'flows', 'windows'}:
        raise ValueError('Unexpected restricted input field')
    if data['schema_version'] != 'issue-network-information-input/1' or data['rule'] != RULE:
        raise ValueError('Unknown information input rule')
    endpoints = {}
    for row in data['endpoints']:
        if set(row) != {'operation','rank','start_ns','end_ns'} or row['start_ns'] > row['end_ns']:
            raise ValueError('Invalid endpoint input')
        key = row['operation'], row['rank']
        if key in endpoints:
            raise ValueError('Duplicate endpoint')
        endpoints[key] = row
    flows = defaultdict(list)
    for f in data['flows']:
        if set(f) != {'operation','src','dst','payload_bytes'}:
            raise ValueError('Unexpected workload field')
        if f['payload_bytes'] > 0:
            flows[f['dst']].append(f)
    result = []
    last_tx_window = {}
    if mapping is not None:
        if set(mapping) != {'schema_version','input_sha256','start_ns','end_ns','records'}:
            raise ValueError('Unexpected ideal relation field')
        if (mapping['schema_version'] != 'issue-network-ideal-relation/1' or
                mapping['start_ns'] != min(w['start_ns'] for w in data['windows']) or
                mapping['end_ns'] != max(w['end_ns'] for w in data['windows'])):
            raise ValueError('Ideal relation changes coverage')
        for row in mapping['records']:
            if set(row) != {'operation','switch_id','port_id','start_ns','end_ns'}:
                raise ValueError('Unexpected ideal relation record')
            if not mapping['start_ns'] <= row['start_ns'] < row['end_ns'] <= mapping['end_ns']:
                raise ValueError('Ideal relation outside coverage')
            key = row['operation'],row['switch_id'],row['port_id']
            last_tx_window[key] = max(last_tx_window.get(key,0),row['end_ns'])
    for w in data['windows']:
        if set(w) != {'sample','switch_id','port_id','receiver','start_ns','end_ns','voq_byte_ns','fifo_byte_ns'}:
            raise ValueError('Unexpected window field')
        possible = set()
        for f in flows[w['receiver']]:
            a = endpoints[f['operation'], f['src']]['start_ns']
            b = endpoints[f['operation'], f['dst']]['end_ns']
            if a < w['end_ns'] and b > w['start_ns']:
                possible.add(f['operation'])
        # Queue statistics establish waiting, not which application/protocol caused it.
        if mapping is not None:
            # A packet waiting now must be transmitted later. A mapping only at
            # this window would wrongly drop a packet that stays queued longer.
            # When receiver completion is outside coverage, later transmission
            # is unknown, so retain the operation rather than invent an absence.
            possible = {o for o in possible if endpoints[o,w['receiver']]['end_ns'] > mapping['end_ns']
                        or last_tx_window.get((o,w['switch_id'],w['port_id']),0) > w['start_ns']}
        result.append(dict(sample=w['sample'], possible_operations=sorted(possible),
            endpoint_only_waiting='unknown', plus_queue_waiting=bool(w['voq_byte_ns'] or w['fifo_byte_ns'])))
    return result


def judge(input_path, output, mapping=None):
    if output.exists():
        raise ValueError('Judgment output must be new')
    data = read(input_path)
    extra = read(mapping) if mapping is not None else None
    if extra is not None and extra['input_sha256'] != digest(input_path):
        raise ValueError('Ideal relation input seal differs')
    save(output, dict(input_sha256=digest(input_path), rule=RULE, samples=judge_input(data,extra),
        extra_information='ideal operation-to-port transmission windows' if extra else None,
        mapping_sha256=digest(mapping) if extra is not None else None, program_sha256=digest(__file__)))


def evaluate(input_path, prediction, truth, output):
    if output.exists():
        raise ValueError('Evaluation output must be new')
    p, t = read(prediction), read(truth)
    if p['input_sha256'] != digest(input_path) or t['input_sha256'] != p['input_sha256']:
        raise ValueError('Information input seal differs')
    expected = {s['sample']:s for s in t['samples']}; results = []
    if len(expected) != len(p['samples']) or {s['sample'] for s in p['samples']} != set(expected):
        raise ValueError('Information sample coverage differs')
    for s in p['samples']:
        x = expected[s['sample']]; a = set(s['possible_operations']); b = set(x['known_waiting_operations'])
        results.append(dict(**x, possible_operations=s['possible_operations'],
            queue_waiting=s['plus_queue_waiting'], missed_known_operations=sorted(b-a),
            unconfirmed_possible_operations=sorted(a-b),
            unique_and_known=bool(b) and len(a)==1 and a==b))
    def counts(items):
        return dict(windows=len(items), positive_queue_windows=sum(s['queue_waiting'] for s in items),
            known_application_wait_windows=sum(bool(s['known_waiting_operations']) for s in items),
            uniquely_localized_known_wait_windows=sum(s['unique_and_known'] for s in items),
            ambiguous_known_wait_windows=sum(bool(s['known_waiting_operations']) and len(s['possible_operations'])>1 for s in items),
            missed_known_operations=sum(len(s['missed_known_operations']) for s in items),
            queue_positive_no_known_application=sum(s['queue_waiting'] and not s['known_waiting_operations'] for s in items))
    save(output, dict(schema_version='issue-network-information-evaluation/1', primary=counts([s for s in results if s['primary']]),
        related_check=counts([s for s in results if not s['primary']]), samples=results,
        sources={str(f):digest(f) for f in (input_path,prediction,truth)}, rule=RULE, program_sha256=digest(__file__),
        interpretation=['Possible is not actual; a unique candidate is validated only against known waiting application packets.',
            'No queue-only window is called application waiting; missing upstream facts are retained.',
            'These related windows are not independent samples or a general accuracy estimate.']))


def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='action', required=True)
    a = sub.add_parser('prepare')
    for name in ('case','packets','output'): a.add_argument('--'+name,type=Path,required=True)
    a = sub.add_parser('judge')
    for name in ('input','output'): a.add_argument('--'+name,type=Path,required=True)
    a.add_argument('--mapping',type=Path,help='One prepared ideal operation/port/window relation; never raw truth')
    a = sub.add_parser('prepare-mapping')
    for name in ('case','input','truth','prediction','output'): a.add_argument('--'+name,type=Path,required=True)
    a = sub.add_parser('evaluate')
    for name in ('input','prediction','truth','output'): a.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS); args=p.parse_args()
    if args.action == 'judge':
        judge(args.input,args.output,args.mapping)
    elif args.worker:
        if args.action == 'prepare': prepare(args.case,args.packets,args.output)
        elif args.action == 'prepare-mapping': prepare_mapping(args.case,args.input,args.truth,args.prediction,args.output)
        else: evaluate(args.input,args.prediction,args.truth,args.output)
    else:
        from moe_ub_cosim.host_probe import probe
        result=probe([sys.executable,'-m',__spec__.name,'--worker',*sys.argv[1:]],
            str(args.output.resolve())+'-resources',**host(extra_roots=[args.output.parent]))
        if result['status']!='completed': raise SystemExit('Information check failed: '+result['status'])


if __name__=='__main__': main()
