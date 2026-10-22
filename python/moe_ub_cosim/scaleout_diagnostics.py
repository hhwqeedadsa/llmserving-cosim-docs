"""Post-run diagnosis, including failed runs; evaluation only, never policy input."""
from collections import Counter, defaultdict
from pathlib import Path

from .scaleout_rdma import checked_json, save
from .scaleout_topology import require
from .scaleout_verify import node_events
from .scaleout_metrics import phase_intervals, network_summary


def diagnose(run, output):
    run, output = Path(run).resolve(), Path(output).resolve()
    require(not output.exists(), 'diagnosis output already exists')
    result = checked_json(run/'result.json', 1073741824)
    capture = result.get('packet_capture')
    faults = result.get('fault_injection', {})
    flows = {m['id']: m for m in result['messages']}
    callbacks = {m['id']: m for m in result.get('service', {}).get('message_callbacks', [])}
    failure=result.get('failure',{})
    traces = []
    if capture:
        columns = capture['columns']
        traces = [dict(zip(columns, row)) for row in capture['events']]
    source_packets = Counter((e['message_id'], e['sequence']) for e in traces
        if e['ip_protocol'] == 17 and e['event'] == 0 and e['message_id'] in flows
        and e['node'] == flows[e['message_id']]['src'])
    repeated=defaultdict(list)
    for (mid,seq),count in sorted(source_packets.items()):
        if count>1:repeated[mid].append(dict(sequence=seq,transmissions=count))
    messages = []
    for identity, flow in sorted(flows.items()):
        state = ('retry_exhausted' if failure.get('message_id')==identity and failure.get('reason')=='recovery_retry_exhausted' else
                 'awaiting_admission' if 'admission_granted_ns' in flow and flow['admission_granted_ns'] is None else
                 'awaiting_resource_release' if flow['ack_callbacks'] and 'sender_resource_released_ns' in flow and flow['sender_resource_released_ns'] is None else
                 'complete' if flow['ack_callbacks'] else
                 'awaiting_sender_ack' if flow['receive_ready_ns'] is not None else 'awaiting_contiguous_data')
        messages.append(dict(id=identity, state=state, src=flow['src'], dst=flow['dst'],
            bytes=flow['bytes'], receiver_contiguous_bytes=flow.get('receiver_contiguous_bytes'),
            receive_ready_ns=flow['receive_ready_ns'], ack_ns=flow['ack_ns'],
            source_data_payload_bytes=flow['source_data_payload_bytes'],
            stage_times=phase_intervals(flow,result['finish_ns']),
            **({'sender_progress':flow['sender_progress']} if 'sender_progress' in flow else {}),
            **{k:flow[k] for k in ('admission_granted_ns','qp_registered_ns','sender_resource_released_ns','sender_qp_present') if k in flow},
            repeated_source_offsets_seen=repeated[identity],
            **{k:v for k,v in callbacks.get(identity, {}).items() if k!='id'}))
    waits, graphs = [], []
    events=node_events(run) if (run/'graphs').is_dir() else {}
    # The graph supplies the declared dependency; actual callbacks determine
    # whether its communication edge finished. Do not predict future waves.
    for path in sorted((run/'graphs').glob('*/wave.json')):
        wave = checked_json(path, 268435456)
        progress=dict(graph_id=wave['graph_id'],declared_nodes=0,issued_nodes=0,completed_nodes=0)
        for rank, nodes in wave['rank_nodes'].items():
            progress['declared_nodes']+=len(nodes)
            for node in nodes:
                event_key=(wave['graph_id'],int(rank),node['id'])
                progress['issued_nodes']+=(*event_key,'issue') in events
                progress['completed_nodes']+=(*event_key,'callback') in events
                if node['kind'] not in ('SEND', 'RECV'):
                    continue
                mid = node['message']['id']
                callback = callbacks.get(mid, {})
                key = 'send_callback_ns' if node['kind'] == 'SEND' else 'recv_callback_ns'
                if callback.get(key) is not None:
                    continue
                waits.append(dict(graph_id=wave['graph_id'], rank=int(rank), node_id=node['id'],
                    name=node['name'], kind=node['kind'], message_id=mid,
                    issue_ns=events.get((*event_key,'issue')), callback_ns=events.get((*event_key,'callback')),
                    state='not_posted' if mid not in flows else
                        'waiting_admission' if flows[mid].get('completion_state')=='awaiting_admission' else
                        'waiting_resource_release' if node['kind']=='SEND' and flows[mid].get('ack_callbacks') else
                        'waiting_ack' if node['kind'] == 'SEND' else 'waiting_data',
                    direct_consumers=[dict(node_id=n['id'], name=n['name'], kind=n['kind'],
                        issue_ns=events.get((wave['graph_id'],int(rank),n['id'],'issue')),
                        callback_ns=events.get((wave['graph_id'],int(rank),n['id'],'callback')))
                                      for n in nodes if node['id'] in n['deps']]))
        if not events:progress.update(issued_nodes=None,completed_nodes=None)
        graphs.append(progress)
    from .scaleout_admission import audit
    from .scaleout_recovery import audit as recovery_audit
    summary = dict(schema_version='moe-scaleout-diagnosis/1', scope='post_run_evaluation_only',
        passed=result['passed'], finish_ns=result['finish_ns'], drained=result['drained'],
        completion_counts=dict(Counter(m['state'] for m in messages)), messages=messages,
        packet_capture_available=bool(capture), packet_capture_complete=capture.get('complete') if capture else None,
        packet_capture_selection=capture.get('selection') if capture else None,
        fault_drop_counts=faults.get('drop_counts', []),
        unmatched_fault_rules=[i for i, n in enumerate(faults.get('drop_counts', [])) if n == 0],
        outstanding_graph_edges=waits, graph_progress=graphs, node_evidence_available=bool(events),
        service=result.get('service'),
        admission_resources=audit(result),
        network_queues=network_summary(result),
        recovery=recovery_audit(result),failure=failure or None,
        limitations=['Capture completeness applies only to selected nodes and time window.',
            'Missing events in absent, filtered or truncated capture cannot prove no retransmission or feedback.',
            'Graph edges lacking callbacks may be unissued; direct consumers are dependencies, not measured delay attribution.',
            'Graph progress counts observed node events; absent node logs give null counts, not proof of no execution.',
            'A native protocol stall is not a claim that production RDMA lacks recovery.'])
    save(output, summary)
    return dict(passed=True, output=str(output), run_passed=result['passed'],
                completion_counts=summary['completion_counts'], unmatched_fault_rules=summary['unmatched_fault_rules'])
