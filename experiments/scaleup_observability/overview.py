"""Researcher-only execution overview. Internal data are not measurement input."""
from collections import Counter, defaultdict
import csv
from pathlib import Path
import sys

from moe_ub_cosim.record_storage import iter_result, iter_port_records
from moe_ub_cosim.research_storage import stored_rows

from .common import digest, read, require, save, sources
from .statistics import dependency_rows, distribution, merge_intervals, queue_summary


def table(path, rows):
    if not rows:
        Path(path).write_text('')
        return
    with Path(path).open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    require(Path(path).stat().st_size <= 64*2**20, 'Overview table exceeds 64 MiB')


def transfer_rows(planned, actual, local, operations):
    plans = {t['transfer_id']:t for t in planned}
    require(len(plans) == len(planned), 'Duplicate planned transfer')
    require(set(plans) == {t['transfer_id'] for t in actual+local}, 'Transfer coverage differs from plan')
    require(len(plans) == len(actual)+len(local), 'Duplicate executed transfer')
    rows = []
    for t in actual:
        p = plans[t['transfer_id']]
        require(t['committed_bytes'] == t['bytes'] == p['payload_bytes'], 'Transfer byte mismatch')
        require(t['submitted_tasks'] == t['send_callbacks'] == t['receive_callbacks'] == 1,
                'Transfer must execute and complete exactly once')
        require(not t['context_active'] and t['initiator_resources_retired'], 'Transfer resources remain live')
        start, ready, done = t['send_start_ns'], t['data_ready_ns'], t['transaction_done_ns']
        require(start <= ready <= done <= t['send_done_ns'] and t['receive_done_ns'] >= ready,
                'Inconsistent completion times')
        owner, consumer = t['data_owner_rank'],t['consumer_rank']
        locality = 'same_server_cross_chip' if owner//4 == consumer//4 else 'cross_server'
        op = operations[p['operation_id']]
        rows.append(dict(transfer_id=t['transfer_id'], ub_task_id=t['ub_task_id'],
            layer_id=op['layer_id'], kind=p['kind'], message_role=p['message_role'], operation=t['operation'],
            initiator_rank=t['initiator_rank'], data_owner_rank=owner, consumer_rank=consumer,
            locality=locality, payload_bytes=t['bytes'], business_bytes=p['business_payload_bytes'],
            committed_segments=t['committed_segments'], submit_ns=start, data_ready_ns=ready,
            protocol_complete_ns=done, initiator_release_ns=t['send_done_ns'],
            other_callback_ns=t['receive_done_ns'], data_latency_ns=ready-start,
            task_latency_ns=done-start, data_to_protocol_complete_ns=done-ready))
    return rows


def build(run, output):
    run, output = Path(run).resolve(), Path(output).resolve()
    require(output.is_dir() and not (output/'summary.json').exists(), 'New overview directory required')
    manifest = read(run/'manifest.json')
    require(manifest['normal_exit'] and manifest['coverage']['execution_complete'] and
            manifest['coverage']['observation_complete'], 'Need complete run and observation coverage')
    ex = run/'raw/execution'
    report = read(ex/'result.json')
    require(report['normal_exit'] and report['network_drain']['idle'], 'Network did not finish/drain')
    service = read(run/'inputs/service.json')
    state = read(run/'raw/service_result.json')
    require(len(state['waves']) == 1 and state['service_finished'], 'Exactly one complete Decode wave required')
    require(len(state['requests']) == len(service['requests']) and
            all(r['status'] == 'FINISHED' and len(r['intertoken_latencies_ns']) == 1 for r in state['requests']),
            'Requested load was not completely executed in one wave')
    require(state['route_records_consumed'] == state['route_records_expected'] == len(service['requests'])*3,
            'Not all declared layer routes consumed')
    require(all(r['free_blocks'] == r['kv_capacity_blocks'] and not r['request_blocks'] for r in state['memory_final']),
            'KV resources not released')
    end = report['simulation_end_ns']
    finish = report['service_finished_ns']
    backend = read(run/'inputs/network/backend.json')
    require(not backend['port_service']['intervals'] and backend['attributes']['ns3::UbLdstApi::UsePacketSpray'] == 'true',
            'First batch requires normal native spray')
    observation = read(run/'inputs/observation.json')
    require(observation['recording']['mode'] == 'detailed' and
            all(c['offset_ns'] == c['drift_ppb'] == 0 for c in observation['clocks']),
            'Overview requires detailed, common-clock initial batch')
    public = read(run/'observed/public_config.json')
    ports = {(p['switch_id'],p['port_id']) for p in public['ports']}
    kinds = {n['node_id']:n['kind'] for n in backend['topology']['nodes']}
    topology = {}
    for link in backend['topology']['links']:
        for side, other in (('src','dst'),('dst','src')):
            if kinds[link[side]] != 'DEVICE':
                topology[link[side],link[side+'_port']] = (kinds[link[side]], kinds[link[other]], link['rate_bps'])
    require(ports == set(topology) and len(ports) == 32, 'Need all 32 switch egress ports equally observed')
    window_totals = defaultdict(Counter)
    queue_totals = defaultdict(Counter)
    for row in stored_rows(run/'observed','port_windows.csv'):
        key = row['switch_id'],row['port_id']
        window_totals[key].update(packets=row['counted_packets'],bytes=row['counted_packet_bytes'])
    for row in stored_rows(run/'observed','queue_windows.csv'):
        key = row['switch_id'],row['port_id']
        for q in ('voq','fifo'):
            queue_totals[key][q+'_byte_ns'] += row[q+'_waiting_byte_ns']
            queue_totals[key][q+'_peak_bytes'] = max(queue_totals[key][q+'_peak_bytes'],row[q+'_waiting_peak_bytes'])
    network, gaps, progress = [], [], defaultdict(Counter)
    period = 100
    for switch, port in sorted(ports):
        folder = ex/'ports'/f'{switch}-{port}'
        packets = list(iter_port_records(ex,folder,'packet-events.jsonl'))
        events = list(iter_port_records(ex,folder,'queue-state-events.jsonl'))
        audit = list(iter_port_records(ex,folder,'egress-queue-audit.jsonl'))
        tx = [r for r in audit if r['event'] == 'tx_start']
        require(Counter((r['time_ns'],r['packet_uid'],r['packet_bytes']) for r in tx) ==
                Counter((r['sim_time_ns'],r['packet_uid'],r['counted_packet_bytes']) for r in packets),
                'Physical busy intervals do not cover all counted sends')
        summary, intervals = queue_summary(events, [(r['time_ns'],r['busy_until_ns']) for r in tx], end)
        key = switch, port
        require(len(packets) == window_totals[key]['packets'] and
                sum(r['counted_packet_bytes'] for r in packets) == window_totals[key]['bytes'],
                'Detailed/aggregate send totals differ')
        for q in ('voq','fifo'):
            require(summary[q+'_byte_ns'] == queue_totals[key][q+'_byte_ns'] and
                    summary[q+'_peak_bytes'] == queue_totals[key][q+'_peak_bytes'], 'Queue integral/peak differs')
        kind, target, rate = topology[key]
        network.append(dict(switch_id=switch, port_id=port, source_kind=kind, target_kind=target,
            nominal_rate_bps=rate, counted_packets=len(packets), counted_packet_bytes=window_totals[key]['bytes'], **summary))
        gaps.extend(dict(switch_id=switch,port_id=port,start_ns=a,end_ns=b,duration_ns=b-a) for a,b in intervals)
        for row in packets:
            progress[row['sim_time_ns']//period]['port_packet_bytes'] += row['counted_packet_bytes']
    table(output/'ports.csv', network)
    table(output/'queued-without-transmission.csv', gaps)
    planned = list(iter_result(ex,'planned_transfers'))
    actual = list(iter_result(ex,'transfers'))
    local = list(iter_result(ex,'local_deliveries'))
    operations = {r['operation_id']:r for r in iter_result(ex,'operation_registry')}
    transfers = transfer_rows(planned, actual, local, operations)
    require(len(list(iter_result(ex,'native_starts'))) == len(actual) == len(list(iter_result(ex,'native_completions'))),
            'Native task start/completion count mismatch')
    table(output/'transfers.csv',transfers)
    table(output/'local-deliveries.csv',local)
    grouped = defaultdict(list)
    for row in transfers:
        key = tuple(row[k] for k in ('kind','message_role','operation','payload_bytes','locality'))
        grouped[key].append(row)
        progress[row['data_ready_ns']//period]['data_ready_tasks'] += 1
        progress[row['data_ready_ns']//period]['delivered_business_bytes'] += row['business_bytes']
        progress[row['protocol_complete_ns']//period]['protocol_completed_tasks'] += 1
    groups = []
    for key,rows in sorted(grouped.items()):
        group = dict(zip(('kind','message_role','operation','payload_bytes','locality'),key))
        group['tasks'] = len(rows)
        for metric in ('data_latency_ns','task_latency_ns','data_to_protocol_complete_ns','committed_segments'):
            for stat,value in distribution([r[metric] for r in rows]).items():
                if stat != 'count':
                    group[metric+'_'+stat] = value
        groups.append(group)
    table(output/'transfer-groups.csv',groups)
    graph_id = state['waves'][0]['graph_id']
    require('/' not in graph_id and '..' not in graph_id, 'Invalid graph id')
    plan = read(run/'raw/graphs'/graph_id/'plan.json')
    require(plan['schema_version'] == 'moe-ub-full-model-plan/1', 'Need full Decode model graph')
    executed = list(iter_result(ex,'node_execution'))
    timing = {(r['rank_id'],r['node_id']):r for r in executed}
    require(len(timing) == len(executed), 'Repeated node timing')
    nodes = [row for g in plan['rank_graphs'] for row in dependency_rows(g,timing,plan['ready_ns'])]
    table(output/'nodes.csv',nodes)
    ranks, layers = [], []
    for rank in plan['rank_ids']:
        rn = [r for r in nodes if r['rank_id'] == rank]
        compute = [r for r in rn if r['kind'] == 'COMP']
        busy = merge_intervals((r['start_ns'],r['end_ns']) for r in compute)
        total = sum(r['duration_ns'] for r in compute)
        require(sum(b-a for a,b in busy) == total, 'Single compute slot overlap')
        ranks.append(dict(rank_id=rank,finished_ns=max(r['end_ns'] for r in rn),compute_work_ns=total,
            compute_busy_fraction=total/finish,
            compute_after_dependencies_ns=sum(r['after_dependencies_before_start_ns'] for r in compute),
            send_after_dependencies_ns=sum(r['after_dependencies_before_start_ns'] for r in rn if r['kind']=='SEND')))
        for lid in plan['executed_layer_ids']:
            selected = [r for r in rn if r['layer_id']==lid]
            layers.append(dict(rank_id=rank,layer_id=lid,start_ns=min(r['start_ns'] for r in selected),
                end_ns=max(r['end_ns'] for r in selected),
                compute_work_ns=sum(r['duration_ns'] for r in selected if r['kind']=='COMP')))
    table(output/'ranks.csv',ranks)
    table(output/'layers.csv',layers)
    require(max(r['finished_ns'] for r in ranks) == finish, 'Rank/round completion mismatch')
    for row in nodes:
        if row['kind']=='COMP':
            progress[row['end_ns']//period]['completed_compute_work_ns'] += row['duration_ns']
    cumulative = Counter()
    series = []
    fields = ('port_packet_bytes','data_ready_tasks','delivered_business_bytes',
              'protocol_completed_tasks','completed_compute_work_ns')
    for i in range(end//period+1):
        current = progress[i]
        cumulative.update(current)
        series.append(dict(start_ns=i*period,end_ns=(i+1)*period,
            **{k:current[k] for k in fields}, **{'cumulative_'+k:cumulative[k] for k in fields}))
    table(output/'progress.csv',series)
    summary = dict(schema_version='scaleup-execution-overview/1', access='researcher_internal',
        analysis_version='normal-overview-v1', run=str(run), manifest_sha256=digest(run/'manifest.json'),
        implementation=sources(), request_count=len(service['requests']), input_sha256=digest(run/'inputs/service.json'),
        round_ns=finish, simulation_end_ns=end, completed_decode_tokens=len(state['requests']),
        decode_tokens_per_second=len(state['requests'])*1e9/finish,
        route_records_consumed=state['route_records_consumed'],
        compute_work_ns=sum(r['compute_work_ns'] for r in ranks),
        network=dict(observed_ports=len(network), counted_packet_bytes=sum(r['counted_packet_bytes'] for r in network),
            summed_voq_byte_ns=sum(r['voq_byte_ns'] for r in network),
            summed_fifo_byte_ns=sum(r['fifo_byte_ns'] for r in network),
            queued_without_transmission_port_ns=sum(r['queued_without_transmission_ns'] for r in network),
            queued_without_transmission_max_ns=max(r['queued_without_transmission_max_ns'] for r in network)),
        transfers=dict(fabric_tasks=len(transfers), local_deliveries=len(local),
            unique_fabric_business_bytes=sum(r['business_bytes'] for r in transfers),
            tasks_with_multiple_committed_segments=sum(r['committed_segments']>1 for r in transfers),
            data_latency_ns=distribution([r['data_latency_ns'] for r in transfers]),
            task_latency_ns=distribution([r['task_latency_ns'] for r in transfers]),
            data_to_protocol_complete_ns=distribution([r['data_to_protocol_complete_ns'] for r in transfers])),
        input_expert_counts={str(l['input']['layer_id']):l['expert_input_counts'] for l in plan['layers']},
        raw_native_sizes={k:v for k,v in backend['attributes'].items() if k.endswith(('StoreRequestSize','LoadResponseSize','LoadRequestSize'))},
        missing_evidence=['Direct scheduler-ready and blocked-reason events',
            'Time histories of outstanding/request-id/credit occupancy',
            'Per-native-request create/arrival/release event table', 'HBM service/consumption model'],
        qualifications=['Dependency-ready reconstructed from explicit predecessors, not a scheduler-ready observation.',
            'Queued without physical transmission is a symptom, not a credit-stall or backpressure label.',
            'Global task quantiles mix sizes/roles; use transfer-groups.csv for comparisons.',
            'Summed port bytes are hop-counted; unique delivered business bytes are separate.',
            'Native starts/completions describe memory tasks, not individual native requests.',
            'Declared reference compute, not hardware calibrated; whole Decode is not steady-state throughput.'],
        checks=dict(execution_complete=True, all_requested_work_done=True, all_switch_ports_covered=True,
            detailed_aggregate_counts_equal=True, queue_integrals_equal=True, graph_timing_consistent=True),
        tables={p.name:digest(p) for p in sorted(output.glob('*.csv'))})
    save(output/'summary.json',summary)
    lines = ['# 正常执行概览', '', f'请求数：{summary["request_count"]}；整轮：{finish} ns；网络排空：{end} ns。',
        f'计算工作量：{summary["compute_work_ns"]} rank·ns；跨芯片搬运任务：{len(transfers)}；同芯片交付：{len(local)}。', '',
        '| 层次 | 文件 |', '|---|---|', '| 网络资源 | ports.csv、queued-without-transmission.csv |',
        '| 传输完成 | transfers.csv、transfer-groups.csv、local-deliveries.csv |',
        '| 执行影响 | nodes.csv、ranks.csv、layers.csv |', '| 完成进度 | progress.csv |', '',
        '各表用于研究者核验，不能直接交给后续被动测量方法。指标定义与缺失资料见仓库的 overview-method.md。']
    (output/'overview.md').write_text('\n'.join(lines)+'\n')
    return summary


if __name__ == '__main__':
    result = build(*sys.argv[1:])
    print(result['round_ns'])
