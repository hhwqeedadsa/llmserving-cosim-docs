"""One ready-KV EP group using the locked LLMServingSim scheduler and cosim/1."""
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


def validate_service(config, layer):
    require(set(config) == {'schema_version', 'run_id', 'mode', 'execution', 'model',
                           'npu_memory_gib', 'requests'}, 'Unsupported service fields')
    require(config['schema_version'] == 'moe-ub-service-fixture/1' and config['mode'] == 'fixture',
            'Only the M3.1 service fixture is implemented')
    require(isinstance(config['run_id'], str) and re.fullmatch(r'[A-Za-z0-9_.-]+', config['run_id']),
            'Invalid run_id')
    require(config['execution'] == {'entry_mode': 'service'}, 'Unsupported execution mode')
    require(config['model'] == 'mistralai/Mixtral-8x7B-v0.1', 'M3.1 requires the locked Mixtral config')
    integer(config['npu_memory_gib'], 'npu_memory_gib', 1, 128)
    validate_config(layer)
    require(layer['hidden_dim'] == 4096 and layer['expert_placement'] == [0, 0, 1, 1, 2, 2, 3, 3],
            'Service model and layer placement disagree')
    requests = config['requests']
    require(isinstance(requests, list) and len(requests) == 4, 'M3.1 requires one request per rank')
    ids, ranks, lengths = set(), [], set()
    for request in requests:
        require(set(request) == {'request_id', 'rank_id', 'arrival_ns', 'prompt_tokens',
                                 'generated_tokens', 'decode_routes'}, 'Unsupported request fields')
        identity = integer(request['request_id'], 'request_id')
        require(identity not in ids, 'Duplicate request_id')
        ids.add(identity)
        ranks.append(integer(request['rank_id'], 'rank_id', 0, 3))
        integer(request['arrival_ns'], 'arrival_ns', 0, 0)
        integer(request['prompt_tokens'], 'prompt_tokens', 1, 32768)
        integer(request['generated_tokens'], 'generated_tokens', 1, 32768)
        routes = request['decode_routes']
        require(isinstance(routes, list) and 2 <= len(routes) <= 32, 'Require 2..32 explicit decode routes')
        require(request['prompt_tokens'] + request['generated_tokens'] + len(routes) <= 32768,
                'Request exceeds model context')
        lengths.add(len(routes))
    require(sorted(ranks) == [0, 1, 2, 3] and len(lengths) == 1,
            'M3.1 requires four active ranks for the same number of remaining steps')
    # Validate all explicit Token routes before starting a child process. Execution
    # graphs are exported only when the real scheduler returns each next batch.
    for step in range(next(iter(lengths))):
        candidate = deepcopy(layer)
        candidate['tokens'] = [dict(request_id=str(r['request_id']), src_rank=r['rank_id'],
            phase='decode', token_index=r['prompt_tokens'] + r['generated_tokens'] - 1 + step,
            layer_id=layer['layer_id'], expert_ids=r['decode_routes'][step]) for r in requests]
        build_plan(candidate)


class Service:
    """Map wave completions to existing Scheduler.add_done and schedule calls."""
    def __init__(self, config, layer):
        validate_service(config, layer)
        # Import this checkout, without copying or changing the upstream scheduler.
        sys.path.insert(0, str(LLM))
        from serving.core.scheduler import Scheduler
        from serving.core.request import Request

        self.config, self.layer = config, layer
        self.identities = RunIdentities()
        self.current_ns = 0
        self.decision_id = 0
        self.wave = None
        self.history = []
        self.schedulers, self.requests, self.inputs = [], [], []
        for source in sorted(config['requests'], key=lambda r: r['rank_id']):
            rank = source['rank_id']
            sched = Scheduler(config['model'], 0, rank, 1, 1, 1, 1, 1,
                config['npu_memory_gib'], 0, rank, 'decode', 16, 16, 1,
                False, False, None, None, ep_size=4)
            reached = source['prompt_tokens'] + source['generated_tokens']
            request = Request(source['request_id'], config['model'], source['prompt_tokens'],
                reached + len(source['decode_routes']), source['arrival_ns'], rank, is_init=False)
            # An explicit initial condition, not simulated prefill or a KV transfer.
            request.num_tokens_reached = reached
            request.num_computed_tokens = reached - 1
            sched.add_decode(request)
            self.schedulers.append(sched)
            self.requests.append(request)
            self.inputs.append(source)
        self.initial = self.snapshot()

    def snapshot(self):
        return [dict(request_id=r.id, rank_id=rank, status=r.status.name,
            num_computed_tokens=r.num_computed_tokens, num_tokens_reached=r.num_tokens_reached,
            target_tokens=r.output, decode_step=r.num_computed_tokens - r.original_input + 1,
            intertoken_latencies_ns=list(r.itl), latest_completion_ns=r.recent_end,
            preemptions=r.num_preemptions) for rank, r in enumerate(self.requests)]

    def decide(self, message, directory):
        require(set(message) == {'schema_version', 'type', 'run_id', 'decision_id', 'now_ns',
                                 'completed', 'available_rank_ids'}, 'Invalid DECIDE fields')
        require(message['schema_version'] == 'cosim/1' and message['type'] == 'DECIDE' and
                message['run_id'] == self.config['run_id'], 'Invalid DECIDE envelope')
        integer(message['decision_id'], 'decision_id', self.decision_id, self.decision_id)
        now = integer(message['now_ns'], 'now_ns', self.current_ns)
        require(message['available_rank_ids'] == [0, 1, 2, 3] and
                all(type(v) is int for v in message['available_rank_ids']), 'EP group is not idle')
        if self.wave is None:
            require(self.decision_id == 0 and now == 0 and message['completed'] == [], 'Invalid initial decision')
        else:
            require(isinstance(message['completed'], list) and len(message['completed']) == 1 and
                    isinstance(message['completed'][0], dict) and
                    type(message['completed'][0].get('finished_ns')) is int, 'Invalid wave completion time')
            require(message['completed'] == [dict(kind='WAVE_DONE', wave_id=self.wave['wave_id'],
                graph_id=self.wave['graph_id'], finished_ns=now)], 'Unexpected wave completion')
            for rank, sched in enumerate(self.schedulers):
                batch = self.wave['batches'][rank]
                prompt, generated, _ = sched.add_done(batch.batch_id + 1, rank, now)
                require(prompt == 0 and generated == 1 and not sched.inflight, 'Decode completion mismatch')
            self.history[-1]['finished_ns'] = now
            self.history[-1]['after'] = self.snapshot()
            self.wave = None
        self.current_ns = now  # Absolute UB clock; never sum batch durations.
        response = dict(schema_version='cosim/1', type='COMMIT', run_id=self.config['run_id'],
            decision_id=self.decision_id, now_ns=now, submissions=[], next_wakeup_ns=None,
            input_exhausted=True, service_finished=all(s.is_request_empty() for s in self.schedulers))
        if not response['service_finished']:
            before = self.snapshot()
            batches = [s.schedule(now, rank) for rank, s in enumerate(self.schedulers)]
            require(all(b is not None and len(b.requests) == b.num_decode == 1 and
                        b.num_prefill == 0 and b.scheduled_tokens == {b.requests[0].id: 1}
                        for b in batches), 'M3.1 requires one real decode Token on every rank')
            step = len(self.history)
            graph, wave = f'g{step}', f'D.w{step}'
            candidate = deepcopy(self.layer)
            candidate.update(graph_id=graph, wave_id=wave, ready_ns=now)
            candidate['tokens'] = []
            for rank, batch in enumerate(batches):
                request = batch.requests[0]
                index = request.num_computed_tokens - 1  # Schedule already advanced the computed count.
                source = self.inputs[rank]
                offset = index - (source['prompt_tokens'] + source['generated_tokens'] - 1)
                candidate['tokens'].append(dict(request_id=str(request.id), src_rank=rank, phase='decode',
                    token_index=index, layer_id=candidate['layer_id'], expert_ids=source['decode_routes'][offset]))
            plan = build_plan(candidate, self.identities)
            export_plan(plan, directory / 'graphs' / graph)
            self.wave = dict(graph_id=graph, wave_id=wave, batches=batches)
            self.history.append(dict(graph_id=graph, wave_id=wave, submitted_ns=now, before=before,
                scheduled=self.snapshot(), batch_ids=[b.batch_id for b in batches], tokens=candidate['tokens']))
            response['submissions'] = [dict(graph_id=graph, wave_id=wave, rank_ids=[0, 1, 2, 3],
                workload_prefix=f'graphs/{graph}/layer', semantic_manifest=f'graphs/{graph}/plan.json')]
        self.decision_id += 1
        return response


def run(args):
    config, layer = json.loads(args.input.read_text()), json.loads(args.layer.read_text())
    service = Service(config, layer)
    output = args.output_dir.resolve()
    output.mkdir(exist_ok=False)
    (output / 'graphs').mkdir()
    export_bootstrap(output / 'bootstrap')
    save(output / 'input.json', config)
    save(output / 'layer.json', layer)
    command = [str(args.backend.resolve()), config['run_id'], str(output), str(args.system.resolve()),
               str(args.network.resolve()), '-' if args.observation is None else str(args.observation.resolve()),
               str(output / 'execution'), str(args.max_simulation_ns)]
    manifest = dict(schema_version='moe-ub-service-run/1', mode='fixture', entry_mode='service',
        calibration_status='unvalidated_reference', routing_source='explicit_synthetic_per_token',
        kv_source='given_ready_at_0', compute_scope='single_layer_functional_profile',
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
            scheduler_calls=len(service.history) * 4, decisions=service.decision_id,
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
