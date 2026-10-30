"""Read-only published-source inventory and completion-source recheck.

No frontend imports, simulation, build, network, or private route export.
Receipts are historical evidence, not a replacement for executing their tests.
"""
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True, timeout=15).strip()


def inventory():
    paths = sorted((ROOT/'python/moe_ub_cosim').rglob('*.py'))
    forbidden, edges, imports = [], {}, Counter()
    names = {str(p.relative_to(ROOT/'python')).removesuffix('.py').replace('/', '.') for p in paths}
    for path in paths:
        name = str(path.relative_to(ROOT/'python')).removesuffix('.py').replace('/', '.')
        targets = set()
        for node in ast.walk(ast.parse(path.read_bytes(), filename=str(path))):
            if isinstance(node, ast.Import):
                candidates = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = name.split('.')[:-node.level] if node.level else []
                base = '.'.join([*prefix, node.module] if node.module else prefix)
                candidates = [base, *(base+'.'+a.name for a in node.names)]
            else:
                continue
            for target in candidates:
                if target.split('.')[0] in ('tests', 'experiments'):
                    forbidden.append(dict(file=str(path.relative_to(ROOT)), line=node.lineno, module=target))
                if target in names:
                    targets.add(target)
        edges[name] = sorted(targets)
        imports.update(targets)
    cpp = sorted([*(ROOT/'backend').rglob('*.cc'), *(ROOT/'backend').rglob('*.hh')])
    return dict(python_modules=len(paths), backend_cc_hh_files=len(cpp),
                python_lines=sum(len(p.read_text().splitlines()) for p in paths),
                backend_cc_hh_lines=sum(len(p.read_text().splitlines()) for p in cpp),
                direct_forbidden_imports=forbidden,
                scope='AST import statements, including function-local imports; dynamic imports and external dependency internals not certified',
                internal_import_edges=edges, most_imported_modules=imports.most_common(8),
                github_workflow_files=git('ls-files', '.github/workflows').splitlines(),
                source_sha256={str(p.relative_to(ROOT)): sha(p) for p in paths+cpp})


def sources():
    manifest = ROOT/'docs/review_artifacts/invocation_semantics_20261023/sources.json'
    rows = json.loads(manifest.read_text())['sources']
    verified = []
    for row in rows:
        p = ROOT/row['cache_path']
        data = p.read_bytes()
        blob = hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if sha(p) != row['sha256'] or len(data) != row['bytes'] or blob != row['git_blob_sha']:
            raise ValueError('cached fixed source differs: '+str(p))
        verified.append(dict(path=row['path'], sha256=sha(p), commit=row['commit'],
                             git_blob_sha=blob, public_url=row['public_url']))
    return dict(manifest_sha256=sha(manifest), verified=verified,
                scope='cached bytes and recorded Git blob identity; no remote refresh or GPU execution')


def capacity():
    limits = (ROOT/'python/moe_ub_cosim/scaleout_streams.py').read_text()
    native = (ROOT/'backend/rdma/persistent_streams.cc').read_text()
    assert "('wire_port',10000,65535)" in limits and "s['wire_port'] not in ports" in limits
    assert '!wire_streams_.count(wire_port)' in native and 'wire_streams_.emplace(wire_port,id)' in native
    contribution = 7168*2
    return dict(declared_stream_limit=4096, globally_unique_wire_port_capacity=65535-10000+1,
                dense_ep256_directional_pairs=256*255,
                four_dense_ep256_directional_pairs=4*256*255,
                r1_hidden_only_bf16_contribution_bytes=contribution,
                max_contributions_under_1MiB=(2**20)//contribution,
                ep32_batch16_top8_single_source_to_peer_message_bytes=16*8*contribution,
                scope='source-derived limits and conditional demand; not measured active connections or full modern wire layout')


def receipts():
    paths = [ROOT/'fixtures/astra_stream_service/verification.json',
             ROOT/'docs/reviews/evidence/astra_tail_lifecycle_20261029.json',
             ROOT/'experiments/service_recovery_baselines/results/20261029/closeout.json']
    result = {}
    for p in paths:
        obj = json.loads(p.read_text())
        result[str(p.relative_to(ROOT))] = dict(sha256=sha(p), schema_version=obj['schema_version'])
    acceptance = json.loads(paths[0].read_text())
    tail = json.loads(paths[1].read_text())
    assert acceptance['passed'] is True and tail['passed'] is True
    return dict(files=result, historical_cpu_tests_passed=acceptance['cpu_tests_passed'],
                historical_new_network_conditions=acceptance['new_network_conditions'],
                tail_ack_compute_to_retirement_ns=tail['ack_loss']['post_compute_graph_retirement_ns'],
                scope='saved versioned summaries only; tests/networks not rerun by this audit')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('refusing to overwrite audit output')
    baseline, fault = 563.347, 705.271
    payload = dict(schema_version='moe-published-snapshot-review/1', reviewed_head=git('rev-parse', 'HEAD'),
        source_inventory=inventory(), completion_source_recheck=sources(), capacity=capacity(),
        saved_receipts=receipts(), illustrative_frequency_model=dict(
            baseline_us=baseline, fault_us=fault,
            scope='hypothetical mean latency if all paired fault excess vanished with zero normal overhead; not an achievable bound, production rate, or SR result',
            examples=[dict(affected_window_fraction=p, illustrative_mean_reduction_percent=
                          100*p*(fault-baseline)/(baseline+p*(fault-baseline))) for p in (.001,.01,.1)]),
        executions=dict(new_network_runs=0, builds=0, downloads=0, full_unit_reruns=0))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps(dict(passed=True, python_modules=payload['source_inventory']['python_modules'],
                         fixed_sources_rechecked=len(payload['completion_source_recheck']['verified']),
                         forbidden_imports=len(payload['source_inventory']['direct_forbidden_imports'])), ensure_ascii=False))


if __name__ == '__main__':
    main()
