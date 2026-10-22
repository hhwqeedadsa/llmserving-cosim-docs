"""Read censored native-service logs; never execute or import a simulator."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

NODE = re.compile(r'^(issue|callback),sys->id=(\d+), tick=(\d+), node->id=(\d+), '
                  r'node->name=([^,]+), node->type=(\d+), graph_id=(\S+)$')
GRAPH = re.compile(r'^graph_(load|activate),rank=(\d+), tick=(\d+), graph_id=(\S+)$')
IDENTITY = re.compile(r'^g(\d+)\.w(\d+)$')


def small(path):
    assert path.stat().st_size <= 2**20, path
    data = path.read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def wave(identity, rank, groups, steps):
    if identity == '_bootstrap':
        return None
    m = IDENTITY.fullmatch(identity)
    assert m, identity
    group, step = map(int, m.groups())
    assert 0 <= group < len(groups) and 0 <= step < steps and rank in groups[group]
    return group, step


def inspect(root):
    spec, spec_hash = small(root/'input.json')
    usage, usage_hash = small(root/'execution/probe.json')
    provenance, provenance_hash = small(root/'provenance.json')
    groups = spec['parallel']['groups']; steps = spec['workload']['decode_steps']
    assert len(groups) == 256 and all(len(g) == 4 for g in groups) and steps == 2
    assert sorted(r for g in groups for r in g) == list(range(1024))
    assert usage['status'] == 'wall_limit' and usage['child_exit_code'] != 0
    stdout = root/'native.stdout.log'; assert stdout.stat().st_size <= 2**20
    data = stdout.read_bytes(); decisions = []
    for line in data.decode().splitlines():
        if line.startswith('MOE_RDMA_EVENT '):
            event = json.loads(line.removeprefix('MOE_RDMA_EVENT '))
            assert event['type'] == 'DECIDE' and event['decision_id'] == len(decisions)
            decisions.append(event)
    assert decisions and decisions[0]['now_ns'] == 0
    completions = {}; previous = 0
    for event in decisions:
        assert event['now_ns'] >= previous; previous = event['now_ns']
        for done in event['completed']:
            identity = done['graph_id']; m = IDENTITY.fullmatch(identity); assert m
            group, step = map(int, m.groups())
            assert group < len(groups) and step < steps and identity not in completions
            assert done['finished_ns'] == event['now_ns']
            completions[identity] = done['finished_ns']

    path = root/'log/astra_nodes.log'; size = path.stat().st_size
    assert size <= 512*2**20
    checksum = hashlib.sha256(); counts = Counter(); by_wave = {}; pending = {}
    max_tick = 0; max_event = None; last_tick = -1; regressions = 0; tail = b''
    anomalies = Counter(); anomaly_examples = []
    with path.open('rb') as stream:
        while True:
            raw = stream.readline(65537)
            if not raw:
                break
            assert len(raw) <= 65536, 'node log line exceeds read budget'
            checksum.update(raw); counts['lines'] += 1; tail = raw
            # SIGTERM can cut a syntactically plausible node record anywhere,
            # including its graph id. Never treat a partial tail as an event.
            if not raw.endswith(b'\n'):
                counts['incomplete_tail_lines'] += 1
                continue
            line = raw.decode().rstrip('\n')
            m = NODE.fullmatch(line)
            if m:
                kind, rank, tick, node, name, node_type, identity = m.groups()
                rank, tick, node, node_type = map(int, (rank, tick, node, node_type))
                assert 0 <= rank < 1024
                location = wave(identity, rank, groups, steps)
                counts[kind] += 1
                event = dict(kind=kind, rank=rank, tick=tick, node=node, name=name,
                             node_type=node_type, graph_id=identity)
                if tick < last_tick:
                    regressions += 1
                last_tick = tick
                if tick >= max_tick:
                    max_tick, max_event = tick, event
                if location is None:
                    continue
                summary = by_wave.setdefault(identity, dict(group=location[0], step=location[1],
                    issue=0, callback=0, max_recorded_tick=0, max_issued_layer=-1,
                    max_callback_layer=-1, ranks={}))
                summary[kind] += 1
                summary['max_recorded_tick'] = max(summary['max_recorded_tick'], tick)
                layer = re.match(r'L(\d+)\.', name)
                if layer:
                    key = 'max_issued_layer' if kind == 'issue' else 'max_callback_layer'
                    summary[key] = max(summary[key], int(layer[1]))
                summary['ranks'][rank] = event
                key = identity, rank, node
                if kind == 'issue':
                    if key in pending:
                        anomalies['repeated_issue_in_prefix'] += 1
                    pending[key] = event
                elif pending.pop(key, None) is None:
                    anomalies['callback_without_issue_in_prefix'] += 1
                continue
            m = GRAPH.fullmatch(line)
            if m:
                kind, rank, tick, identity = m.groups(); rank, tick = int(rank), int(tick)
                assert 0 <= rank < 1024
                wave(identity, rank, groups, steps)
                counts['graph_'+kind] += 1
                continue
            counts['other_or_incomplete_lines'] += 1
            if len(anomaly_examples) < 4:
                anomaly_examples.append(line[:512])
    assert path.stat().st_size == size, 'input log changed while reading'
    missing = {str(step):[g for g in range(len(groups)) if f'g{g}.w{step}' not in completions]
               for step in range(steps)}
    # An early-posted receive can wait for a future layer. Keep counts and a
    # bounded sample; serializing every prefix difference exceeded 32MiB in
    # the original audit. The failed output is preserved, not retried here.
    counter = Counter(str(e['node_type']) for e in pending.values())
    import heapq
    outstanding = heapq.nsmallest(32,pending.values(),key=lambda e:(e['graph_id'],e['rank'],e['node']))
    return dict(root=str(root),scope='right-censored historical log prefix; no terminal network state',
        input_sha256=spec_hash,usage_sha256=usage_hash,provenance_sha256=provenance_hash,
        hashes=provenance['hashes'],model=spec['model'],workload=spec['workload'],groups=groups,
        host={k:usage[k] for k in ('status','wall_seconds','peak_sampled_group_rss_bytes',
                                  'peak_sampled_artifact_bytes','limits')},
        stdout_sha256=hashlib.sha256(data).hexdigest(),decisions=decisions,
        completed_wave_times=completions,completion_count_by_step={str(s):sum(
            i.endswith('.w'+str(s)) for i in completions) for s in range(steps)},
        missing_completion_notifications=missing,
        node_log=dict(bytes=size,sha256=checksum.hexdigest(),counts=dict(counts),max_recorded_tick=max_tick,
            max_recorded_event=max_event,timestamp_regressions_in_file_order=regressions,
            tail_newline=tail.endswith(b'\n'),tail_utf8=tail.decode(),anomalies=dict(anomalies),
            other_line_examples=anomaly_examples),
        wave_prefixes=by_wave,issued_without_callback_count=len(pending),
        issued_without_callback_sample=outstanding,
        issued_without_callback_count_by_node_type=dict(counter),
        warning='prefix difference is NOT actual live simulator state, deadlock, packet loss or CPU attribution')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline', required=True, type=Path)
    p.add_argument('--numeric', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args(); assert not args.output.exists(); args.output.mkdir()
    result = {}
    for name in ('baseline','numeric'):
        result[name] = inspect(getattr(args,name).resolve())
        (args.output/(name+'.json')).write_text(json.dumps(result[name],indent=2)+'\n')
    assert result['baseline']['input_sha256'] == result['numeric']['input_sha256']
    assert result['baseline']['decisions'] == result['numeric']['decisions']
    result['comparison'] = dict(same_input=True,task_event_records_equal=True,
        stdout_byte_identical=result['baseline']['stdout_sha256']==result['numeric']['stdout_sha256'],
        terminal_network_result_available=False,new_network_executed=False,
        persistent_stream_changes_used_by_historical_service=False,hardware_calibrated=False)
    (args.output/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({name:dict(completed=result[name]['completion_count_by_step'],
        max_recorded_tick=result[name]['node_log']['max_recorded_tick'],
        counts=result[name]['node_log']['counts'],anomalies=result[name]['node_log']['anomalies'])
        for name in ('baseline','numeric')}))


if __name__ == '__main__':
    main()
