"""Compare separate, overlapping and staggered EP groups on one real UB bottleneck."""
import argparse
from collections import Counter, defaultdict, deque
from copy import deepcopy
import csv
import datetime
from decimal import Decimal
import json
from pathlib import Path
import re
import subprocess
import sys

from check_delay import check_execution, read, sha
from check_variants import rows


def signature(plan):
    """Run identities vary with interleaving; all actual work must stay identical."""
    source = deepcopy(plan['input'])
    for key in ('graph_id', 'wave_id', 'ready_ns'): source.pop(key)
    transfers = [{k:v for k,v in t.items() if k not in
        ('transfer_id', 'ub_task_id', 'comm_tag', 'operation_id')} for t in plan['transfers']]
    ordinals = {t['transfer_id']: i for i,t in enumerate(plan['transfers'])}
    graphs = deepcopy(plan['rank_graphs'])
    for graph in graphs:
        for node in graph['nodes']:
            if 'transfer_id' in node: node['transfer_id'] = ordinals[node['transfer_id']]
    return dict(input=source, transfers=transfers, graphs=graphs, expert_inputs=plan['expert_input_counts'])


def audit_queue(path):
    """Reconstruct the native physical FIFO and successful serialization spans."""
    events = rows(path / 'egress-queue-audit.jsonl')
    pending, starts, waits = deque(), [], []
    used = peak = area = previous = 0
    dequeued = None
    for event in events:
        now, size = event['time_ns'], event['packet_bytes']
        assert now >= previous and size > 0
        area += used * (now - previous); previous = now
        if event['event'] == 'enqueue':
            pending.append(event); used += size; peak = max(peak, used)
        elif event['event'] == 'dequeue':
            arrival = pending.popleft()
            assert dequeued is None and (arrival['packet_uid'],arrival['packet_bytes']) == (event['packet_uid'],size)
            used -= size
            dequeued = dict(packet_uid=event['packet_uid'], bytes=size, enqueue_ns=arrival['time_ns'],
                            dequeue_ns=now, wait_ns=now-arrival['time_ns'])
        else:
            assert event['event'] == 'tx_start' and dequeued is not None
            assert (event['packet_uid'],size,now) == (dequeued['packet_uid'],dequeued['bytes'],dequeued['dequeue_ns'])
            assert event['busy_until_ns'] > now
            # 10 Gbit/s, nanosecond resolution, zero interframe gap in this fixture.
            # DataRate -> Time(int64x64_t) rounds to the nearest nanosecond.
            assert event['busy_until_ns'] - now == (size * 8 + 5) // 10
            assert not starts or now >= starts[-1]['busy_until_ns']
            starts.append(event); waits.append(dequeued); dequeued = None
        assert used >= 0 and used == event['queue_bytes']
    assert not pending and dequeued is None and used == 0
    assert len(events) == len(starts) * 3
    assert area == sum(p['wait_ns'] * p['bytes'] for p in waits)
    native = (path / 'network/runlog/PortTrace_node_8_port_4.tr').read_text()
    tx = [(int(Decimal(t)*1000),int(n)) for t,n in re.findall(r'\[([\d.]+)us\] Port Tx, port ID: 4 PacketSize: (\d+)',native)]
    assert tx == [(e['time_ns'],e['packet_bytes']) for e in starts]
    audit = read(path / 'observation-audit.json')
    assert len(starts) == audit['all_successful_start_packets']
    assert sum(p['packet_bytes'] for p in starts) == audit['native_tx_bytes_delta']
    # Longest uninterrupted successful transmission interval, from native delays.
    begin = end = longest = 0
    for event in starts:
        if event['time_ns'] != end: begin = event['time_ns']
        end = event['busy_until_ns']; longest = max(longest,end-begin)
    return dict(peak_bytes=peak, queue_byte_ns=area, longest_busy_ns=longest,
                packets=waits, starts=starts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    build, output = args.build_dir.resolve(), args.output_dir.resolve()
    output.mkdir(parents=True,exist_ok=False)
    fixture = root / 'fixtures/m3_shared_bottleneck'
    base, observation = read(fixture / 'service.json'), read(fixture / 'observation.json')
    layer, system = root / 'fixtures/m2_single_layer/layer.json', root / 'fixtures/m1_compute/system.json'
    binary = build / 'output/cosim/ns3.44-cosim-service'
    commands, results = [], {}
    # Validate paths before interpreting contention as a shared physical resource.
    topology = list(csv.DictReader((fixture / 'network/topology.csv').open()))
    assert topology[-1] == dict(nodeId1='8',portId1='4',nodeId2='9',portId2='4',bandwidth='10Gbps',delay='20ns')
    assert [(int(r['nodeId1']),int(r['nodeId2']),int(r['portId2'])) for r in topology[:-1]] == [(r,8+r%2,r//2) for r in range(8)]
    routing = {(int(r['nodeId']),int(r['dstNodeId'])):int(r['outPorts']) for r in csv.DictReader((fixture / 'network/routing_table.csv').open())}
    assert all(routing[sw,r] == (r//2 if r%2 == sw-8 else 4) for sw in (8,9) for r in range(8))
    assert (fixture / 'network/network_attribute.txt').read_bytes() == (root / 'fixtures/m3_dynamic_arrivals/network/network_attribute.txt').read_bytes()

    def run(label, config, observed=True):
        input_path = output / (label+'.input.json'); input_path.write_text(json.dumps(config,indent=2)+'\n')
        argv = [sys.executable,'-m','moe_ub_cosim.service','--input',str(input_path),'--layer',str(layer),
            '--backend',str(binary),'--system',str(system),'--network',str(fixture / 'network'),
            '--output-dir',str(output / label)]
        if observed is not None:
            obs_path = output / (label+'.observation.json')
            obs_path.write_text(json.dumps(dict(observation,record_provenance=observed),indent=2)+'\n')
            argv.extend(['--observation',str(obs_path)])
        start = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=90)
        for stream in ('stdout','stderr'): (output / (label+'.'+stream+'.log')).write_text(getattr(result,stream))
        commands.append(dict(label=label,argv=argv,cwd=str(root),started_utc=start,
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),exit_code=result.returncode))
        (output / 'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        assert result.returncode == 0,(label,result.stderr)
        path = output / label
        front, report = read(path / 'service_result.json'), read(path / 'execution/result.json')
        assert read(path / 'manifest.json')['normal_exit'] and front['service_finished'] and report['normal_exit']
        assert report['network_initializations'] == 1 and report['loaded_ranks_before_activation'] == 8
        assert report['bootstrap'] == dict(business_nodes=0,finished_ranks=8,time_ns=0)
        assert front['waiting'] == front['running'] == front['inflight'] == front['pending'] == [0]*8
        assert all(s['finished'] and not s['remaining_nodes'] and all(s[k] == 0 for k in
            ('compute_slots','send_slots','cpu_slots','pending_events','pending_tasks','stale_callbacks')) for s in report['rank_resources'])
        assert all(b['same_network_objects'] for b in report['boundary_resources'])
        assert report['active_wqes'] == [0]*8 and report['csv_completed_tasks'] == 0
        assert report['network_drain']['idle'] and report['network_drain']['started_packets'] == report['network_drain']['arrived_packets']
        assert front['finished_ns'] == report['service_finished_ns'] <= report['simulation_end_ns'] < 1000000
        assert len(report['transfers']) == len(report['wqe_starts']) == len(report['wqe_completions']) == 42 * (len(config['requests'])//4)
        assert sum(t['bytes'] for t in report['transfers']) == 344064 * (len(config['requests'])//4)
        for field in ('transfer_id','ub_task_id'): assert len({t[field] for t in report['transfers']}) == len(report['transfers'])
        libraries = {Path(p).resolve() for p in report['loaded_libraries'] if Path(p).name.startswith('libns3')}
        assert len(libraries) == 10 and all(p.parent == build / 'output/lib' for p in libraries)
        plans, groups, operation_group = {}, defaultdict(list), {}
        for wave in front['waves']:
            graph = wave['graph_id']; plan = read(path / f'graphs/{graph}/plan.json'); plans[graph] = plan
            local = dict(loaded_ranks_before_activation=4)
            for key in ('node_execution','activation_order','transfers','graph_completions'):
                local[key] = [{k:v for k,v in e.items() if k!='graph_id'} for e in report[key] if e['graph_id']==graph]
            check_execution(plan,local,path / 'execution',check_rank2_ahead=False,graph_id=graph)
            assert wave['submitted_ns'] == plan['ready_ns']
            assert wave['finished_ns'] == max(e['time_ns'] for e in local['graph_completions'])
            assert all(t['submitted_tasks'] == 1 and t['send_callbacks'] == t['receive_callbacks'] == 1 for t in local['transfers'])
            assert [e for e in report['graph_loads'] if e['graph_id']==graph] == [dict(graph_id=graph,rank_id=r,time_ns=wave['submitted_ns']) for r in plan['rank_ids']]
            group = plan['input']['group_id']
            groups[group].append(dict(wave=wave,plan=plan,execution=local))
            for op in plan['operations']: operation_group[op['operation_id']] = group
        for group,waves in groups.items():
            assert len(waves) == 3
            for old,new in zip(waves,waves[1:]): assert new['wave']['submitted_ns'] == old['wave']['finished_ns']
        expected = {r['request_id']:r for r in config['requests']}
        assert len(front['arrivals']) == len(expected)
        for request in front['requests']:
            source = expected[request['request_id']]
            assert request['status'] == 'FINISHED' and request['num_tokens_reached'] == request['target_tokens']
            assert len(request['intertoken_latencies_ns']) == 3 and request['preemptions'] == 0
            assert sum(request['intertoken_latencies_ns']) == request['latest_completion_ns'] - source['arrival_ns']
        transcript = rows(path / 'protocol.jsonl')
        assert len(transcript) == front['decisions'] * 2 == report['decisions'] * 2
        for i in range(0,len(transcript),2):
            decide,commit = transcript[i:i+2]
            assert decide['decision_id'] == commit['decision_id'] == i//2 and decide['now_ns'] == commit['now_ns']
            for done in decide['completed']:
                assert done['finished_ns'] == decide['now_ns']
                wave = next(w for w in front['waves'] if w['graph_id']==done['graph_id'])
                assert wave['finished_ns'] == done['finished_ns']
            for submission in commit['submissions']: assert plans[submission['graph_id']]['ready_ns'] == commit['now_ns']
        record = dict(front=front,report=report,groups=groups,protocol=transcript,path=path)
        if observed is not None:
            queue = audit_queue(path / 'execution'); record['queue'] = queue
            counters = rows(path / 'execution/observed/port_counters.jsonl')
            for c in counters:
                selected = [s for s in queue['starts'] if c['local_window_start_ns'] <= s['time_ns'] < c['local_window_end_ns']]
                assert c['counted_packets'] == len(selected) and c['counted_packet_bytes'] == sum(s['packet_bytes'] for s in selected)
            if observed:
                ranges = rows(path / 'execution/truth/packet_ranges.jsonl')
                transfers = {t['transfer_id']:t for p in plans.values() for t in p['transfers']}
                # Every even-to-odd transfer, and only those, must cross 8:4.
                crossing = {t['transfer_id'] for t in transfers.values() if t['src_rank']%2==0 and t['dst_rank']%2==1}
                assert {p['transfer_id'] for p in ranges} == crossing
                by_uid = {p['packet_uid']:p for p in ranges}; assert len(by_uid)==len(ranges)
                waits = {p['packet_uid']:p for p in queue['packets']}
                for p in ranges:
                    assert p['packet_uid'] in waits and waits[p['packet_uid']]['dequeue_ns']==p['sim_time_ns']
                    assert p['counted_packet_bytes']==waits[p['packet_uid']]['bytes']
                    p['group_id'] = operation_group[p['operation_id']]
                for tid in crossing:
                    parts = sorted((p for p in ranges if p['transfer_id']==tid),key=lambda p:p['task_byte_range'][0])
                    cursor = 0
                    for part in parts:
                        assert part['task_byte_range'][0]==cursor
                        cursor = part['task_byte_range'][1]
                    assert cursor == transfers[tid]['payload_bytes']
                windows = []
                truth = rows(path / 'execution/truth/port_byte_truth.jsonl')
                assert len(counters) == len(truth) == 200
                for counter,t in zip(counters,truth):
                    amounts = Counter()
                    for p in ranges:
                        if counter['local_window_start_ns'] <= p['sim_time_ns'] < counter['local_window_end_ns']:
                            amounts[p['group_id']] += p['task_byte_range'][1]-p['task_byte_range'][0]
                    assert sum(amounts.values()) == t['business_payload_bytes']
                    assert counter['counted_packet_bytes'] == sum(t[k] for k in ('business_payload_bytes','application_metadata_bytes','data_protocol_overhead_bytes','control_packet_bytes','unattributed_bytes'))
                    windows.append(dict(start_ns=counter['local_window_start_ns'],end_ns=counter['local_window_end_ns'],business_bytes_by_group=dict(amounts)))
                record.update(ranges=ranges,windows=windows)
                (output / (label+'.group-windows.json')).write_text(json.dumps(windows,indent=2)+'\n')
            else: assert not (path / 'execution/truth').exists()
        else: assert not (path / 'execution/observed').exists() and not (path / 'execution/egress-queue-audit.jsonl').exists()
        results[label]=record
        print(f"PASS {label}: {len(front['waves'])} waves, finished {front['finished_ns']} ns",flush=True)
        return record

    a,b = deepcopy(base),deepcopy(base)
    a['requests']=[r for r in a['requests'] if r['rank_id']<4]
    b['requests']=[r for r in b['requests'] if r['rank_id']>=4]
    delayed=deepcopy(base)
    for r in delayed['requests']:
        if r['rank_id']>=4: r['arrival_ns']=500000
    run('solo-a',a); run('solo-b',b); overlap=run('overlap',base); staggered=run('staggered',delayed)
    for label,observed in (('overlap-off',False),('overlap-absent',None),('overlap-repeat',True)):
        other=run(label,base,observed)
        for field in ('front','report','protocol'): assert other[field]==overlap[field],(label,field)
        def native_hashes(path): return {str(p.relative_to(path)):sha(p) for p in (path/'network/runlog').rglob('*') if p.is_file()}
        assert native_hashes(other['path']/'execution')==native_hashes(overlap['path']/'execution')
        if observed is not None:
            assert other['queue']==overlap['queue']
            assert {p.name:sha(p) for p in (other['path']/'execution/observed').iterdir()}=={p.name:sha(p) for p in (overlap['path']/'execution/observed').iterdir()}
    comparison, transfer_comparison, queue_waits = [], [], {}
    for group,solo,arrival in (('ep0',results['solo-a'],0),('ep1',results['solo-b'],5500)):
        for step,(single,shared,shifted) in enumerate(zip(solo['groups'][group],overlap['groups'][group],staggered['groups'][group])):
            assert signature(single['plan'])==signature(shared['plan'])==signature(shifted['plan'])
            delta=shared['wave']['finished_ns']-single['wave']['finished_ns']
            comparison.append(dict(group_id=group,step=step,solo_start_ns=single['wave']['submitted_ns'],
                overlap_start_ns=shared['wave']['submitted_ns'],solo_finish_ns=single['wave']['finished_ns'],
                overlap_finish_ns=shared['wave']['finished_ns'],finish_shift_ns=delta))
        # Initial wave: inspect individual real transfers and their dependent compute.
        single,shared=solo['groups'][group][0],overlap['groups'][group][0]
        single_transfers = {t['transfer_id']:t for t in single['execution']['transfers']}
        shared_transfers = {t['transfer_id']:t for t in shared['execution']['transfers']}
        group_transfers = []
        for left,right in zip(single['plan']['transfers'],shared['plan']['transfers']):
            st,ot = single_transfers[left['transfer_id']],shared_transfers[right['transfer_id']]
            group_transfers.append(dict(group_id=group,src_rank=left['src_rank'],dst_rank=left['dst_rank'],
                solo_transfer_id=left['transfer_id'],overlap_transfer_id=right['transfer_id'],
                solo={k:st[k] for k in ('send_start_ns','receive_done_ns','send_done_ns')},
                overlap={k:ot[k] for k in ('send_start_ns','receive_done_ns','send_done_ns')},
                transaction_duration_shift_ns=(ot['send_done_ns']-ot['send_start_ns'])-(st['send_done_ns']-st['send_start_ns'])))
        assert any(t['transaction_duration_shift_ns']>0 for t in group_transfers),group
        transfer_comparison.extend(group_transfers)
        sx={(e['rank_id'],e['node_id']):e for e in single['execution']['node_execution']}
        ox={(e['rank_id'],e['node_id']):e for e in shared['execution']['node_execution']}
        shifts=[]
        for graph in single['plan']['rank_graphs']:
            rank=graph['rank_id'];node=graph['phase_nodes']['expert']
            shifts.append(dict(rank_id=rank,solo_expert_start_ns=sx[rank,node]['start_ns'],
                overlap_expert_start_ns=ox[rank,node]['start_ns'],shift_ns=ox[rank,node]['start_ns']-sx[rank,node]['start_ns']))
        assert any(s['shift_ns']>0 for s in shifts),(group,shifts)
        assert shared['wave']['finished_ns']>single['wave']['finished_ns'],group
        assert overlap['groups'][group][1]['wave']['submitted_ns']>solo['groups'][group][1]['wave']['submitted_ns'],group
        (output/(group+'.compute-feedback.json')).write_text(json.dumps(shifts,indent=2)+'\n')
    assert overlap['front']['finished_ns']>max(results['solo-a']['front']['finished_ns'],results['solo-b']['front']['finished_ns'])
    # A queued business packet spans B graph activation, proving queue survival.
    first_a=overlap['groups']['ep0'][0]['wave'];first_b=overlap['groups']['ep1'][0]['wave']
    assert first_b['submitted_ns']==5500<first_a['finished_ns']
    a_uids={p['packet_uid'] for p in overlap['ranges'] if p['group_id']=='ep0'}
    preserved=[p for p in overlap['queue']['packets'] if p['packet_uid'] in a_uids and p['enqueue_ns']<5500<p['dequeue_ns']]
    assert preserved,'No first-group queue persisted across second-group activation'
    assert any(t['src_rank']<4 and t['send_start_ns']<5500<t['send_done_ns'] for t in overlap['report']['transfers'])
    assert any(set(w['business_bytes_by_group'])=={'ep0','ep1'} for w in overlap['windows'])
    assert overlap['queue']['longest_busy_ns']>10000,'No continuous saturation interval'
    # The allocator prefetches from ingress queues into this physical FIFO.
    # Its instantaneous peak need not grow: compare integrated queue occupancy
    # against the sum of the two independent runs with the same business work.
    assert overlap['queue']['queue_byte_ns']>sum(results[k]['queue']['queue_byte_ns'] for k in ('solo-a','solo-b'))
    for group,solo in (('ep0',results['solo-a']),('ep1',results['solo-b'])):
        def mean_wait(case):
            ids={p['packet_uid'] for p in case['ranges'] if p['group_id']==group}
            waits=[p['wait_ns'] for p in case['queue']['packets'] if p['packet_uid'] in ids]
            return sum(waits)/len(waits)
        queue_waits[group] = dict(solo_mean_ns=mean_wait(solo),overlap_mean_ns=mean_wait(overlap),
                                 staggered_mean_ns=mean_wait(staggered))
        assert mean_wait(overlap)>mean_wait(solo),(group,queue_waits[group])
    # Staggering changes only arrival; no overlapping UB transactions or port use.
    ar=[t for t in staggered['report']['transfers'] if t['src_rank']<4]
    br=[t for t in staggered['report']['transfers'] if t['src_rank']>=4]
    assert max(t['send_done_ns'] for t in ar)<500000<min(t['send_start_ns'] for t in br)
    assert max(p['dequeue_ns'] for p in staggered['queue']['packets'] if p['enqueue_ns']<500000)<500000
    assert all(len(w['business_bytes_by_group'])<=1 for w in staggered['windows'])
    assert [w['wave']['finished_ns'] for w in staggered['groups']['ep0']]==[w['wave']['finished_ns'] for w in results['solo-a']['groups']['ep0']]
    assert staggered['groups']['ep1'][-1]['wave']['finished_ns']-500000<overlap['groups']['ep1'][-1]['wave']['finished_ns']-5500
    (output/'summary.json').write_text(json.dumps(dict(passed=True,actual_runs=len(results),comparison=comparison,
        first_wave_transfers=transfer_comparison,business_fifo_waits=queue_waits,
        preserved_queue_packets=preserved,mixed_windows=sum(set(w['business_bytes_by_group'])=={'ep0','ep1'} for w in overlap['windows']),
        cases={label:dict(finished_ns=r['front']['finished_ns'],waves=len(r['front']['waves']),writes=len(r['report']['transfers']),
            business_bytes=sum(t['bytes'] for t in r['report']['transfers']),
            queue={k:v for k,v in r.get('queue',{}).items() if k not in ('packets','starts')},
            wave_times={g:[dict(start_ns=w['wave']['submitted_ns'],finish_ns=w['wave']['finished_ns']) for w in waves] for g,waves in r['groups'].items()}) for label,r in results.items()},
        source_hashes={str(p.relative_to(root)):sha(p) for folder in ('backend','python/moe_ub_cosim') for p in (root/folder).glob('*') if p.is_file()}),indent=2)+'\n')
    print('PASS: actual shared queue, preserved packets, group byte accounting, compute and next-schedule feedback, staggered control and passive audit')


if __name__=='__main__':
    main()
