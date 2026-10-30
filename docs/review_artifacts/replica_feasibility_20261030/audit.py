"""Offline source/graph feasibility audit; never executes or changes a network."""
import argparse
from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess

from moe_ub_cosim.scaleout_service import validate
from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_verify import node_events, event_time


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        if tag in ('p', 'h1', 'h2', 'h3', 'h4', 'li', 'tr'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip -= 1
        if tag in ('p', 'h1', 'h2', 'h3', 'h4', 'li', 'tr'):
            self.parts.append('\n')

    def handle_data(self, value):
        if not self.skip:
            self.parts.append(value)

    def value(self):
        return '\n'.join(' '.join(x.split()) for x in ''.join(self.parts).split('\n') if x.strip())+'\n'


def load(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main(root, output):
    root = root.resolve()
    cache = root/'data/research/replica_feasibility_20261030'
    run = root/'runs/replica_feasibility_20261030'
    sources, monitors = [], {}
    for path in sorted((run/'monitors').glob('*/probe.json')):
        record = load(path)
        require(not record.get('remaining_live_group_pids'), 'source command still alive')
        monitors[path.parent.name] = dict(status=record['status'], wall_seconds=record['wall_seconds'],
            peak_rss_bytes=record['peak_sampled_group_rss_bytes'], receipt=str(path.relative_to(root)), sha256=sha(path))
    for path in sorted((cache/'requests').glob('*.json')):
        record = load(path)
        monitor = monitors[record['id']]
        effective = record['status']
        if effective == 'started':
            require(monitor['status'] == 'wall_limit', 'unterminated or unexplained retrieval')
            effective = 'interrupted_by_host_wall_limit'
        if 'local_path' in record:
            local = Path(record['local_path'])
            require(local.stat().st_size == record['captured_bytes'] and sha(local) == record['sha256'], 'source changed')
            record['local_path'] = str(local.relative_to(root))
        if effective == 'retrieved':
            require(monitor['status'] == 'completed', 'retrieval has no successful host receipt')
        sources.append(dict(record, effective_status=effective, host_monitor=monitor))
    require(len(sources) <= 24 and len({r['requested_url'] for r in sources}) == len(sources), 'source attempt budget')
    require(sum(r.get('captured_bytes', 0) for r in sources) <= 32*2**20, 'source byte budget')
    # A search hit is not a primary-paper claim. These short excerpts are checked
    # only against actually retrieved full text / author README.
    import base64
    faster = load(cache/'primary-fastermoe-readme.body')
    readme = base64.b64decode(faster['content'])
    require(hashlib.sha1(b'blob '+str(len(readme)).encode()+b'\0'+readme).hexdigest() == faster['sha'], 'README Git blob identity')
    fulltexts = {'fastermoe': readme.decode(), 'lazarus': (cache/'paper-lazarus.txt').read_text()}
    for identity, name in [('eep', 'primary-partial-rank-html'), ('megaflux', 'primary-megaflux')]:
        parser = Text()
        parser.feed((cache/(name+'.body')).read_text())
        fulltexts[identity] = parser.value()
        require(fulltexts[identity] == (cache/(name+'.txt')).read_text(), 'HTML extraction differs')
    excerpts = {
        'fastermoe': 'Dynamic shadowing is enabled by environment variable',
        'lazarus': 'adaptive expert placement',
        'eep': 'Requests in flight at the moment of failure are reported as failed and must be retried by the client',
        'megaflux': 'The plan preserves token counts, expert selections, and routing weights',
    }
    for identity, excerpt in excerpts.items():
        require(excerpt in ' '.join(fulltexts[identity].split()), 'primary-source excerpt missing: '+identity)
    # Audit saved execution, without regenerating graphs or running ns-3.
    cases, inputs = {}, {}
    for case in ('normal', 'local'):
        directory = root/'runs/token_transient_20261030'/case
        wave = load(directory/'graphs/g0.w0/wave.json')
        config, topology, rdma = (load(directory/name) for name in ('input.json', 'topology.json', 'rdma.json'))
        require(validate(config, topology, rdma, Replay(config, directory))[0] == 128, 'baseline input invalid')
        bad = deepcopy(config)
        bad['parallel']['groups'][1][10] = 20  # Preserve length/order/E-divisibility but overlap physical rank20.
        try:
            validate(bad, topology, rdma, Replay(config, directory))
        except ValueError as failure:
            require(str(failure) == 'Scale-Out topology: EP groups must partition physical GPUs exactly', 'unexpected rejection')
            rejection = str(failure)
        else:
            raise ValueError('overlapping EP groups unexpectedly accepted')
        events = node_events(directory)
        gate = next(g for g in wave['phase_completion']['gates'] if g['id'] == 'L0.dispatch')
        gate_members = {m['rank']: m['node'] for m in gate['members']}
        rows = []
        for rank in wave['rank_ids']:
            tasks = {task['id']: task for task in wave['rank_nodes'][str(rank)]}
            expert = next(t for t in tasks.values() if t['name'] == 'L0.experts')
            require(gate_members[rank] in expert['deps'], 'expert computation does not depend on dispatch gate')
            gate_done = event_time(events, wave['graph_id'], rank, gate_members[rank], 'callback')
            issued = event_time(events, wave['graph_id'], rank, expert['id'], 'issue')
            finished = event_time(events, wave['graph_id'], rank, expert['id'], 'callback')
            require(issued >= gate_done and finished >= issued, 'expert computed before dispatch flush')
            rows.append(dict(rank=rank, gate_ns=gate_done, expert_issue_ns=issued,
                             expert_done_ns=finished, declared_expert_ns=expert['duration_ns']))
        require(len(set(r['gate_ns'] for r in rows)) == 1 and len(rows) == 64, 'group gate differs')
        physical = next(n for n in wave['routes'] if n['layer'] == 0)
        target_experts = sorted({e for t in physical['tokens'] for e in t['experts'] if e//4 == 10})
        require(target_experts and all(40 <= e <= 43 for e in target_experts), 'target expert mapping differs')
        cases[case] = dict(first_dispatch_gate_ns=rows[0]['gate_ns'], ranks=len(rows),
            expert_completions_before_dispatch_gate=sum(r['expert_done_ns'] < r['gate_ns'] for r in rows),
            expert_rows=rows, target_rank=20, declared_same_expert_replica_rank=21,
            target_experts_accessed=target_experts, overlapping_groups_rejected=rejection)
        for p in (directory/'graphs/g0.w0/wave.json', directory/'log/astra_nodes.log', directory/'input.json'):
            inputs[str(p.relative_to(root))] = sha(p)
    code_specs = {
        'backend/rdma/service.cc': ['rank not available for graph', 'message leaves its EP group', 'can_load_graph()'],
        'backend/rdma/astra_adapter.cc': ['reused logical message identity', 'SEND posted twice'],
        'python/moe_ub_cosim/scaleout_service.py': ['EP groups must partition physical GPUs exactly', "f'L{layer}.experts'"],
        'python/moe_ub_cosim/scaleout_replay.py': ['replication unsupported'],
        'backend/rdma/stream_probe.cc': ['local_busy', 'serial_per_gpu/1'],
        'python/moe_ub_cosim/scaleout_llm.py': ['device_bytes=None', 'other_workspace_bytes=0'],
    }
    code = {}
    for name, anchors in code_specs.items():
        path = root/name
        lines = path.read_text().splitlines()
        matches = {anchor: [i for i, line in enumerate(lines, 1) if anchor in line] for anchor in anchors}
        require(all(matches.values()), 'code anchor changed: '+name)
        code[name] = dict(sha256=sha(path), anchors=matches)
    result = dict(schema_version='moe-replica-feasibility-audit/1', passed=True,
        source_checkout=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        network_executions=0, builds=0, gpu_executions=0, sources=sources,
        verified_primary_excerpts=excerpts, fastermoe_readme_git_blob=faster['sha'],
        text_sha256={k: hashlib.sha256(v.encode()).hexdigest() for k, v in fulltexts.items()},
        code=code, saved_execution_inputs=inputs, cases=cases,
        monitored_wall_seconds_before_audit=sum(r['wall_seconds'] for r in monitors.values()),
        novelty_proven=False, algorithm_gain_proven=False,
        decision='no_go_for_replication_as_novelty; conditional_feasibility_for_inflight_bundle_repair')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({k: result[k] for k in ('passed', 'network_executions', 'decision')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    main(args.root, args.output)
