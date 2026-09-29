"""Dynamic ready-KV EP groups using the locked LLMServingSim scheduler and cosim/1."""
import argparse
from copy import deepcopy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time

from .generate import export_bootstrap, export_plan
from .plan import RunIdentities, build_plan, integer, require, validate_config

ROOT = Path(__file__).resolve().parents[2]
LLM = ROOT / 'third_party/LLMServingSim'


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def read_line(child, selector, buffer, timeout):
    """Bound waits even when a child writes only a partial JSON line."""
    deadline = time.monotonic() + timeout
    while b'\n' not in buffer:
        require(len(buffer) < 1048576, 'Backend protocol line exceeds 1 MiB')
        remaining = deadline - time.monotonic()
        require(remaining > 0 and selector.select(remaining), 'Timed out waiting for backend DECIDE')
        chunk = os.read(child.stdout.fileno(), 65536)
        require(bool(chunk), f'Backend EOF before service finished (exit={child.poll()}); see backend.stderr.log')
        buffer.extend(chunk)
    end = buffer.index(b'\n') + 1
    line = bytes(buffer[:end]).decode('utf-8')
    del buffer[:end]
    return line


def service_groups(config, layer):
    """The legacy fixture supplies one group; dynamic fixtures declare members."""
    return deepcopy(config.get('groups', [dict(group_id=layer['group_id'],
        rank_ids=layer['rank_ids'], rank_to_ub_node=layer['rank_to_ub_node'],
        expert_placement=layer['expert_placement'])]))


def group_layer(layer, group):
    candidate = deepcopy(layer)
    candidate.update(group)
    return candidate


def validate_service(config, layer):
    fields = {'schema_version', 'run_id', 'mode', 'execution', 'model', 'npu_memory_gib', 'requests'}
    require(set(config) in (fields, fields | {'groups'}), 'Unsupported service fields')
    require(config['schema_version'] == 'moe-ub-service-fixture/1' and config['mode'] == 'fixture',
            'Only the ready-KV service fixture is implemented')
    require(isinstance(config['run_id'], str) and re.fullmatch(r'[A-Za-z0-9_.-]+', config['run_id']),
            'Invalid run_id')
    require(config['execution'] == {'entry_mode': 'service'}, 'Unsupported execution mode')
    require(config['model'] == 'mistralai/Mixtral-8x7B-v0.1', 'Service requires the locked Mixtral config')
    integer(config['npu_memory_gib'], 'npu_memory_gib', 1, 128)
    validate_config(layer)
    require(layer['hidden_dim'] == 4096, 'Service model and layer hidden dimension disagree')
    groups = service_groups(config, layer)
    require(isinstance(groups, list) and 1 <= len(groups) <= 2, 'Require one or two EP groups')
    owners, group_ids = {}, set()
    for group in groups:
        require(set(group) == {'group_id', 'rank_ids', 'rank_to_ub_node', 'expert_placement'},
                'Unsupported group fields')
        name, ranks = group['group_id'], group['rank_ids']
        require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', name) and name not in group_ids,
                'Invalid or duplicate group_id')
        group_ids.add(name)
        require(isinstance(ranks, list) and len(ranks) == 4 and
                all(type(r) is int and 0 <= r < 8 for r in ranks) and ranks == sorted(set(ranks)),
                'Require four distinct ordered group members')
        require(group['rank_to_ub_node'] == ranks and all(type(r) is int for r in group['rank_to_ub_node']),
                'Service requires explicit identity UB mapping')
        require(group['expert_placement'] == [r for r in ranks for _ in range(2)] and
                all(type(r) is int for r in group['expert_placement']), 'Require two experts per group member')
        for rank in ranks:
            require(rank not in owners, 'EP groups overlap')
            owners[rank] = group
    require(sorted(owners) == list(range(4 * len(groups))), 'Service ranks must cover 0..N-1')
    requests = config['requests']
    require(isinstance(requests, list) and 4 <= len(requests) <= 64, 'Require 4..64 requests')
    ids, cohorts = set(), {}
    for request in requests:
        require(set(request) == {'request_id', 'rank_id', 'arrival_ns', 'prompt_tokens',
                                 'generated_tokens', 'decode_routes'}, 'Unsupported request fields')
        identity = integer(request['request_id'], 'request_id')
        require(identity not in ids, 'Duplicate request_id')
        ids.add(identity)
        rank = integer(request['rank_id'], 'rank_id', 0, len(owners) - 1)
        arrival = integer(request['arrival_ns'], 'arrival_ns', 0, 2**63 - 1000001)
        integer(request['prompt_tokens'], 'prompt_tokens', 1, 32768)
        integer(request['generated_tokens'], 'generated_tokens', 1, 32768)
        routes = request['decode_routes']
        require(isinstance(routes, list) and 2 <= len(routes) <= 32, 'Require 2..32 explicit decode routes')
        require(request['prompt_tokens'] + request['generated_tokens'] + len(routes) <= 32768,
                'Request exceeds model context')
        cohorts.setdefault((owners[rank]['group_id'], arrival), []).append(request)
    for (name, arrival), cohort in cohorts.items():
        group = next(g for g in groups if g['group_id'] == name)
        cohort.sort(key=lambda r: r['rank_id'])
        require([r['rank_id'] for r in cohort] == group['rank_ids'] and
                len({len(r['decode_routes']) for r in cohort}) == 1,
                'M3.2 requires simultaneous four-rank cohorts with equal remaining steps')
        for step in range(len(cohort[0]['decode_routes'])):
            candidate = group_layer(layer, group)
            candidate['tokens'] = [dict(request_id=str(r['request_id']), src_rank=r['rank_id'],
                phase='decode', token_index=r['prompt_tokens'] + r['generated_tokens'] - 1 + step,
                layer_id=layer['layer_id'], expert_ids=r['decode_routes'][step]) for r in cohort]
            build_plan(candidate)


class Service:
    """Ready-KV arrival queues feed real schedulers only when their EP group is idle."""
    def __init__(self, config, layer):
        validate_service(config, layer)
        sys.path.insert(0, str(LLM))
        from serving.core.scheduler import Scheduler
        from serving.core.request import Request

        self.config, self.layer = config, layer
        self.groups = sorted(service_groups(config, layer), key=lambda g: g['group_id'])
        self.ranks = sorted(r for g in self.groups for r in g['rank_ids'])
        self.owners = {r: g['group_id'] for g in self.groups for r in g['rank_ids']}
        self.identities = RunIdentities()
        self.current_ns = self.decision_id = self.scheduler_calls = 0
        self.waves, self.history, self.arrivals, self.decisions = {}, [], [], []
        self.schedulers = [Scheduler(config['model'], 0, rank, 1, 1, 1, 1, 1,
            config['npu_memory_gib'], 0, rank, 'decode', 16, 16, len(config['requests']),
            False, False, None, None, ep_size=4) for rank in self.ranks]
        self.pending = {rank: [] for rank in self.ranks}
        self.inputs = {r['request_id']: r for r in config['requests']}
        self.requests = []
        for source in sorted(config['requests'], key=lambda r: (r['arrival_ns'], r['rank_id'], r['request_id'])):
            reached = source['prompt_tokens'] + source['generated_tokens']
            request = Request(source['request_id'], config['model'], source['prompt_tokens'],
                reached + len(source['decode_routes']), source['arrival_ns'], source['rank_id'], is_init=False)
            request.num_tokens_reached = reached
            request.num_computed_tokens = reached - 1
            request.recent_end = source['arrival_ns']  # Ready-KV observation starts at arrival.
            self.requests.append(request)
        self.future = list(self.requests)
        self.initial = self.snapshot()

    def snapshot(self):
        return [dict(request_id=r.id, rank_id=self.inputs[r.id]['rank_id'], status=r.status.name,
            num_computed_tokens=r.num_computed_tokens, num_tokens_reached=r.num_tokens_reached,
            target_tokens=r.output, decode_step=r.num_computed_tokens - r.original_input + 1,
            intertoken_latencies_ns=list(r.itl), latest_completion_ns=r.recent_end if r.itl else None,
            preemptions=r.num_preemptions) for r in self.requests]

    def decide(self, message, directory):
        require(set(message) == {'schema_version', 'type', 'run_id', 'decision_id', 'now_ns',
                                 'completed', 'available_rank_ids'}, 'Invalid DECIDE fields')
        require(message['schema_version'] == 'cosim/1' and message['type'] == 'DECIDE' and
                message['run_id'] == self.config['run_id'], 'Invalid DECIDE envelope')
        integer(message['decision_id'], 'decision_id', self.decision_id, self.decision_id)
        now = integer(message['now_ns'], 'now_ns', self.current_ns)
        require(self.decision_id != 0 or (now == 0 and message['completed'] == []), 'Invalid initial decision')
        completed = message['completed']
        require(isinstance(completed, list), 'Invalid completed list')
        finished_groups = []
        for event in completed:
            require(isinstance(event, dict) and type(event.get('finished_ns')) is int, 'Invalid wave completion')
            matches = [name for name, wave in self.waves.items() if event == dict(kind='WAVE_DONE',
                wave_id=wave['wave_id'], graph_id=wave['graph_id'], finished_ns=now)]
            require(len(matches) == 1 and matches[0] not in finished_groups, 'Unexpected wave completion')
            finished_groups.extend(matches)
        require(finished_groups == sorted(finished_groups), 'Unordered wave completions')
        expected_idle = [r for r in self.ranks if self.owners[r] not in self.waves or
                         self.owners[r] in finished_groups]
        require(message['available_rank_ids'] == expected_idle and
                all(type(r) is int for r in message['available_rank_ids']), 'EP group availability mismatch')
        for name in finished_groups:
            wave = self.waves.pop(name)
            for rank, batch in wave['batches'].items():
                sched = self.schedulers[rank]
                prompt, generated, _ = sched.add_done(batch.batch_id + 1, rank, now)
                require(prompt == 0 and generated == 1 and not sched.inflight, 'Decode completion mismatch')
            self.history[wave['history_index']].update(finished_ns=now, after=self.snapshot())
        self.current_ns = now  # Absolute UB time, never accumulated batch durations.
        while self.future and self.future[0].arrival <= now:
            request = self.future.pop(0)
            require(request.arrival == now, 'Backend skipped a request arrival')
            rank = self.inputs[request.id]['rank_id']
            name = self.owners[rank]
            self.pending[rank].append(request)
            self.arrivals.append(dict(request_id=request.id, rank_id=rank, group_id=name,
                arrival_ns=now, active_graph_id=self.waves.get(name, {}).get('graph_id'),
                pending_request_ids=[r.id for r in self.pending[rank]]))
        response = dict(schema_version='cosim/1', type='COMMIT', run_id=self.config['run_id'],
            decision_id=self.decision_id, now_ns=now, submissions=[],
            next_wakeup_ns=self.future[0].arrival if self.future else None,
            input_exhausted=not self.future, service_finished=False)
        for group in self.groups:
            name, ranks = group['group_id'], group['rank_ids']
            if name in self.waves:
                continue
            empty = [self.schedulers[r].is_request_empty() for r in ranks]
            require(all(empty) or not any(empty), 'Partial EP group requires M3.4')
            if all(empty):
                if not any(self.pending[r] for r in ranks):
                    continue
                require(all(self.pending[r] for r in ranks), 'Incomplete ready-KV cohort')
                for rank in ranks:
                    self.schedulers[rank].add_decode(self.pending[rank].pop(0))
            before = self.snapshot()
            batches = {r: self.schedulers[r].schedule(now, r) for r in ranks}
            self.scheduler_calls += len(ranks)
            require(all(b is not None and len(b.requests) == b.num_decode == 1 and
                        b.num_prefill == 0 and b.scheduled_tokens == {b.requests[0].id: 1}
                        for b in batches.values()), 'Require one real decode Token on every group rank')
            step = len(self.history)
            graph, wave = f'g{step}', f'D.w{step}'
            candidate = group_layer(self.layer, group)
            candidate.update(graph_id=graph, wave_id=wave, ready_ns=now, tokens=[])
            for rank, batch in batches.items():
                request = batch.requests[0]
                index = request.num_computed_tokens - 1  # schedule() already advanced this count.
                source = self.inputs[request.id]
                offset = index - (source['prompt_tokens'] + source['generated_tokens'] - 1)
                candidate['tokens'].append(dict(request_id=str(request.id), src_rank=rank, phase='decode',
                    token_index=index, layer_id=candidate['layer_id'], expert_ids=source['decode_routes'][offset]))
            export_plan(build_plan(candidate, self.identities), directory / 'graphs' / graph)
            self.waves[name] = dict(graph_id=graph, wave_id=wave, batches=batches, history_index=step)
            self.history.append(dict(graph_id=graph, wave_id=wave, group_id=name, rank_ids=ranks,
                submitted_ns=now, before=before, scheduled=self.snapshot(),
                batch_ids=[b.batch_id for b in batches.values()], tokens=candidate['tokens']))
            response['submissions'].append(dict(graph_id=graph, wave_id=wave, rank_ids=ranks,
                workload_prefix=f'graphs/{graph}/layer', semantic_manifest=f'graphs/{graph}/plan.json'))
        response['service_finished'] = (not self.future and not self.waves and
            not any(self.pending.values()) and all(s.is_request_empty() for s in self.schedulers))
        self.decisions.append(dict(now_ns=now, active_graphs={g:w['graph_id'] for g,w in self.waves.items()},
            pending_request_ids={str(r):[q.id for q in self.pending[r]] for r in self.ranks},
            request_state=self.snapshot()))
        self.decision_id += 1
        return response


def run(args):
    config, layer = json.loads(args.input.read_text()), json.loads(args.layer.read_text())
    service = Service(config, layer)
    output = args.output_dir.resolve()
    output.mkdir(exist_ok=False)
    (output / 'graphs').mkdir()
    export_bootstrap(output / 'bootstrap', service.ranks)
    save(output / 'deployment.json', dict(schema_version='moe-ub-service-deployment/1', groups=service.groups))
    save(output / 'input.json', config)
    save(output / 'layer.json', layer)
    command = [str(args.backend.resolve()), config['run_id'], str(output), str(args.system.resolve()),
               str(args.network.resolve()), '-' if args.observation is None else str(args.observation.resolve()),
               str(output / 'execution'), str(args.max_simulation_ns)]
    manifest = dict(schema_version='moe-ub-service-run/1', mode='fixture', entry_mode='service',
        calibration_status='unvalidated_reference', routing_source='explicit_synthetic_per_token',
        kv_source='given_ready_at_arrival', compute_scope='single_layer_functional_profile',
        scheduler_source='third_party/LLMServingSim/serving/core/scheduler.py',
        scheduler_sha256=hashlib.sha256((LLM / 'serving/core/scheduler.py').read_bytes()).hexdigest(),
        command=command, cwd=str(ROOT), started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        normal_exit=False)
    save(output / 'manifest.json', manifest)
    child = None
    try:
        with (output / 'backend.stderr.log').open('w') as error, (output / 'protocol.jsonl').open('w') as transcript:
            child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=error, bufsize=0)
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                buffer = bytearray()
                finished = False
                while not finished:
                    line = read_line(child, selector, buffer, args.wall_timeout_seconds)
                    transcript.write(line); transcript.flush()
                    message = json.loads(line)
                    response = service.decide(message, output)
                    line = json.dumps(response) + '\n'
                    transcript.write(line); transcript.flush()
                    child.stdin.write(line.encode('utf-8')); child.stdin.flush()
                    finished = response['service_finished']
                child.stdin.close()
                # After final COMMIT, permit only EOF; collect status without losing errors.
                code = child.wait(timeout=args.wall_timeout_seconds)
                require(not buffer and child.stdout.read() == b'', 'Unexpected protocol after final COMMIT')
                require(code == 0, f'Backend failed with exit {code}; see backend.stderr.log')
        manifest['normal_exit'] = True
        save(output / 'service_result.json', dict(initial=service.initial, waves=service.history,
            requests=service.snapshot(), service_finished=True, finished_ns=service.current_ns,
            scheduler_calls=service.scheduler_calls, decisions=service.decision_id,
            arrivals=service.arrivals, decision_state=service.decisions,
            pending=[len(service.pending[r]) for r in service.ranks],
            waiting=[len(s.waiting) for s in service.schedulers], running=[len(s.running) for s in service.schedulers],
            inflight=[len(s.inflight) for s in service.schedulers], done=[len(s.done) for s in service.schedulers]))
    except Exception as error:
        manifest['error'] = str(error)
        raise
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
            manifest['backend_exit_code'] = child.wait()
            if child.stdin and not child.stdin.closed:
                child.stdin.close()
            child.stdout.close()
        manifest['finished_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(output / 'manifest.json', manifest)
    print(f'Service finished at {service.current_ns} ns: {output}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--layer', type=Path, required=True)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--system', type=Path, required=True)
    parser.add_argument('--network', type=Path, required=True)
    parser.add_argument('--observation', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--max-simulation-ns', type=int, default=1000000)
    parser.add_argument('--wall-timeout-seconds', type=int, default=60)
    args = parser.parse_args()
    integer(args.max_simulation_ns, 'max_simulation_ns', 1)
    integer(args.wall_timeout_seconds, 'wall_timeout_seconds', 1, 3600)
    run(args)


if __name__ == '__main__':
    main()
