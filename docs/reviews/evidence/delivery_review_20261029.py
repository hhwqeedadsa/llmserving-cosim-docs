"""Read-only audit of the in-progress ASTRA message transport integration.

No build, network execution, raw route export, or modification of saved runs.
Saved runs are optional: --acceptance audits small functional acceptance files;
--summary-run audits an existing SimAI service summary. Outputs must be new.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def read(path):
    if path.stat().st_size > 128 * 2**20:
        raise ValueError('oversized review input')
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True, timeout=15).strip()


def inventory():
    modules = sorted((ROOT/'python/moe_ub_cosim').glob('*.py'))
    forbidden = []
    for path in modules:
        for node in ast.walk(ast.parse(path.read_bytes(), filename=str(path))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                     [node.module or ''] if isinstance(node, ast.ImportFrom) and not node.level else [])
            for name in names:
                if name.split('.')[0] in ('tests', 'experiments'):
                    forbidden.append(dict(file=str(path.relative_to(ROOT)), line=node.lineno, module=name))
    sources = [*modules, *(ROOT/'backend').rglob('*.cc'), *(ROOT/'backend').rglob('*.hh'),
               *(ROOT/'backend').rglob('*.cmake')]
    return dict(python_modules_including_untracked=len(modules), direct_forbidden_imports=forbidden,
                import_scope='AST import statements only; dynamic and transitive imports not certified',
                github_workflow_files=[str(p.relative_to(ROOT)) for p in sorted((ROOT/'.github/workflows').glob('*'))],
                source_sha256={str(p.relative_to(ROOT)): sha(p) for p in sorted(sources)})


def acceptance(directory):
    from moe_ub_cosim.scaleout_verify import verify

    verdicts = {}
    hashes = {}
    for name in ('legacy', 'normal', 'data-loss', 'ack-loss', 'two-groups'):
        d = directory/name
        verdicts[name] = verify(d)  # public saved-result verifier, no simulation
        hashes[name] = {file: sha(d/file) for file in ('result.json', 'frontend.json')}
    compatibility = read(directory/'legacy-compatibility.json')
    if not compatibility['compared'] or not all(compatibility['fields'].values()):
        raise ValueError('saved legacy comparison did not pass')

    blackhole = directory/'blackhole'
    lines = (blackhole/'native.stdout.log').read_text().splitlines()
    damaged = []
    for line in lines:
        if not line.startswith('MOE_RDMA_EVENT '):
            continue
        payload = line[len('MOE_RDMA_EVENT '):]
        try:
            json.loads(payload)
        except json.JSONDecodeError as error:
            # Forensic extraction only; never used to bypass production parsing.
            event, end = json.JSONDecoder().raw_decode(payload)
            damaged.append(dict(error=str(error), event_type=event.get('type'),
                                now_ns=event.get('now_ns'), decision_id=event.get('decision_id'),
                                diagnostic_suffix=payload[end:][:200]))
    f = read(blackhole/'frontend.json')
    if not (damaged and not f['history'] and not f['transcript'] and not (blackhole/'result.json').exists()):
        raise ValueError('blackhole pre-submission failure evidence changed')

    r = read(directory/'ack-loss/result.json')
    fault = r['fault_injection'];rule = fault['rules'][0]
    sid, seq = rule['message_id'], rule['sequence']
    packet = [dict(zip(r['packet_capture']['columns'], row)) for row in r['packet_capture']['events']]
    drop = next(p for p in packet if p['message_id'] == sid and p['node'] == rule['node'] and
                p['event'] == 2 and p['ip_protocol'] == 252 and p['sequence'] == seq)
    higher = next(p for p in packet if p['message_id'] == sid and p['node'] == rule['node'] and
                  p['event'] == 1 and p['ip_protocol'] == 252 and p['sequence'] > seq and p['time_ns'] > drop['time_ns'])
    work = next(m for m in r['service_transport']['messages'] if m['stream'] == sid and m['begin'] < seq <= m['end'])
    expiries = sum(s['timeout_expiries'] for s in r['service_transport']['native']['recovery']['states'])
    if work['ack_ns'] != higher['time_ns'] or expiries != 0:
        raise ValueError('ACK-loss was not covered by later cumulative feedback as recorded')
    return dict(saved_verifiers=verdicts, result_hashes=hashes, legacy_compatibility=compatibility,
                blackhole=dict(control_corruption=damaged, before_first_commit=True, native_result_present=False,
                               recovery_failure_tested=False, stdout_sha256=sha(blackhole/'native.stdout.log')),
                simai_started=(directory/'simai').exists(),
                ack_loss=dict(stream_id=sid, dropped_sequence=seq, dropped_at_ns=drop['time_ns'],
                              later_cumulative_sequence=higher['sequence'], later_feedback_ns=higher['time_ns'],
                              message_ack_ns=work['ack_ns'], timeout_expiries=expiries,
                              proves_terminal_ack_timeout=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acceptance', type=Path)
    parser.add_argument('--summary-run', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('review output already exists')
    facts = dict(schema_version='moe-delivery-review/1', source_head=git('rev-parse', 'HEAD'),
                 branch=git('branch', '--show-current'), worktree_status=git('status', '--short'),
                 new_network_runs=0, builds=0, downloads=0, inventory=inventory(),
                 dense_one_plane_upper_bounds=[dict(endpoints=1024, ep=ep, groups=1024//ep,
                    directional_streams=1024*(ep-1), messages_one_layer_step=2*1024*(ep-1),
                    within_4096_streams=1024*(ep-1)<=4096) for ep in (4, 32, 64, 128, 256)],
                 scale_scope='dense directional pair requirement, not measured active pairs or wall time',
                 r1_message_limit=dict(hidden=7168, activation_bytes=2, max_message_bytes=2**20,
                    max_contributions_per_message=(2**20)//(7168*2),
                    batch16_top8_all_to_one_bytes=16*8*7168*2,
                    condition='all eight experts on one remote destination; EP32 has eight experts/rank'))
    if args.acceptance:
        facts['acceptance'] = acceptance(args.acceptance.resolve())
    if args.summary_run:
        from moe_ub_cosim.run_summary import describe
        summary = describe(args.summary_run)
        facts['saved_terminal_audit'] = dict(outcome=summary['outcome'],
            result_sha256=sha(args.summary_run/'result.json'),
            frontend_sha256=sha(args.summary_run/'frontend.json'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        stream.write(json.dumps(facts, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(dict(modules=facts['inventory']['python_modules_including_untracked'],
        saved_networks_reverified=len(facts.get('acceptance',{}).get('saved_verifiers',{})), new_network_runs=0)))


if __name__ == '__main__':
    main()
