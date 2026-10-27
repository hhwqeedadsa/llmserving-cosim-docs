"""Saved evidence acceptance; runtime paths are optional during offline recovery."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import shlex
from moe_ub_cosim.native_compilation import inspect_commands
from fixtures.native_release_build.workflow import load, save, sha


def need(ok, message):
    if not ok: raise ValueError(message)


def normalize(result):
    return dict(result, loaded_libraries=[Path(p).name if Path(p).name.startswith('libns3.') else p
        for p in result['loaded_libraries']])


def physical(topology, result):
    need(result['passed'], 'incomplete ordinary probe')
    capture = result['packet_capture']
    need(capture['complete'] and not capture['omitted_events'] and not capture['selection']['nodes'], 'capture incomplete')
    edges = {}
    for edge in topology['links']:
        a, b = (edge['src'], edge['src_port']), (edge['dst'], edge['dst_port'])
        edges[a] = b; edges[b] = a
    sends, receives, tx_bytes = Counter(), Counter(), Counter()
    for r in capture['events']:
        if r[3] == 0:
            sends[edges[r[1], r[2]], r[4], r[6], r[7], r[8]] += 1
            tx_bytes[r[1], r[2]] += r[8]
        elif r[3] == 1: receives[(r[1], r[2]), r[4], r[6], r[7], r[8]] += 1
        else: need(r[3] in (3, 4), 'unexpected physical drop')
    need(sends == receives and sum(sends.values()) > 0, 'physical packet conservation')
    for port in result['ports']:
        need(port['model_tx_bytes'] == tx_bytes[port['node'], port['port']], 'port byte total differs')
    return dict(passed=True, physical_TX=sum(sends.values()), physical_RX=sum(receives.values()),
        port_counters_checked=len(result['ports']), finish_ns=result['finish_ns'])


def verify(stage, live=False):
    repository = Path(__file__).resolve().parents[2]
    records = load(stage/'commands.json')
    need(all(r['exit_code'] == 0 for r in records), 'failed command')
    need(sum(r['network'] for r in records) == 2, 'network count')
    identity = load(stage/'reference-identity.json')
    for name, digest in identity['files'].items(): need(sha(stage/name) == digest, 'reference/input differs: '+name)
    backend, provenance = load(stage/'build/backend.json'), load(stage/'build/provenance.json')
    for name, digest in provenance['sources'].items(): need(sha(repository/name) == digest, 'built source changed')
    compiler = inspect_commands(stage/'build/cmake/compile_commands.json')
    need(compiler == backend['compiler_options'] == provenance['compiler_options'] == load(stage/'build/compiler-options.json'),
        'compiler evidence differs')
    rows = load(stage/'build/cmake/compile_commands.json')
    flag_counts = Counter()
    for row in rows:
        args = row.get('arguments') or shlex.split(row['command'])
        flags = tuple(a for a in args if a.startswith('-O'))
        need(flags == ('-O3',), 'unexpected actual optimization sequence')
        flag_counts[flags] += 1
    cache = (stage/'build/cmake/CMakeCache.txt').read_text()
    need('NS3_WARNINGS:BOOL=OFF\n' in cache and 'CMAKE_BUILD_TYPE:STRING=Release\n' in cache, 'configure mismatch')
    need('CMAKE_EXPORT_COMPILE_COMMANDS:BOOL=ON\n' in cache, 'compile database not exported')
    libraries = {Path(p).name: sha(stage/'build/output/lib'/Path(p).name) for p in backend['linked_ns3_libraries']}
    programs = {key: sha(stage/'build/output/moe-rdma'/Path(backend[key]).name)
        for key in ('probe', 'stream_probe', 'transaction_probe', 'transaction_session', 'service')}
    need(len(libraries) == 8, 'mixed native libraries')
    for mode in ('probe', 'modern'):
        result = load(stage/mode/'result.json')
        actual = {Path(p).name: p for p in result['loaded_libraries'] if Path(p).name.startswith('libns3.')}
        need(set(actual) == set(libraries) and all('unified-bus' not in p for p in result['loaded_libraries']), 'mixed runtime')
        need(all(Path(p).parent == Path(backend['ns3_core']).parent for p in actual.values()), 'unexpected runtime path')
        if live:
            for name, path in actual.items(): need(sha(Path(path)) == libraries[name], 'live library mismatch')
    if live:
        for key, digest in programs.items(): need(sha(Path(backend[key])) == digest, 'live binary mismatch')
    original, current = stage/'reference-modern', stage/'modern'
    need(set(identity['original_libraries']) == set(libraries), 'reference library set differs')
    need(normalize(load(original/'result.json')) == normalize(load(current/'result.json')), 'optimized native result differs')
    need(load(original/'modern-execution.json') == load(current/'modern-execution.json'), 'optimized application differs')
    need((original/'control.jsonl').read_bytes() == (current/'control.jsonl').read_bytes(), 'control order differs')
    from fixtures.physical_nic_binding.checks import audit
    from fixtures.modern_native.audit import audit as modern_audit
    modern_physical = audit(load(stage/'modern-topology/topology.json'), load(stage/'inputs/modern-protocol.json'),
        load(current/'result.json'))
    application = modern_audit(current)
    probe = physical(load(stage/'probe-topology/topology.json'), load(stage/'probe/result.json'))
    requests = {r['id']: r for r in load(stage/'inputs/probe-messages.json')}
    messages = load(stage/'probe/result.json')['messages']
    need({r['id'] for r in messages} == set(requests), 'ordinary message set differs')
    for row in messages:
        expected = requests[row['id']]
        need(all(row[k] == expected[k] for k in ('src', 'dst', 'bytes', 'plane', 'ready_ns')), 'message input differs')
        need(row['completion_state'] == 'complete' and row['ack_callbacks'] == 1 and
            row['receiver_contiguous_bytes'] == row['source_unique_data_payload_bytes'] == row['bytes'], 'message byte completion')
    probe['messages'] = len(messages)
    probe['payload_bytes'] = sum(r['bytes'] for r in messages)
    corruptions = []
    def reject(label, action):
        try: action()
        except (AssertionError, ValueError, KeyError, IndexError): corruptions.append(label)
        else: raise ValueError('corruption accepted: '+label)
    bad = load(stage/'probe/result.json'); bad['ports'][0]['model_tx_bytes'] += 1
    reject('port bytes', lambda: physical(load(stage/'probe-topology/topology.json'), bad))
    bad = load(current/'result.json'); bad['packet_capture']['events'][0][0] += 1
    reject('modern physical packet time', lambda: need(normalize(load(original/'result.json')) == normalize(bad), 'result differs'))
    bad_app = load(current/'modern-execution.json')
    bad_app['events'][-1]['time_ns'] += 1
    reject('modern application event', lambda: need(load(original/'modern-execution.json') == bad_app, 'event differs'))
    return dict(passed=True, effective_optimization='-O3', translation_units=compiler['translation_units'],
        optimization_sequences=[dict(flags=list(k), units=v) for k, v in flag_counts.items()],
        retained_warnings=compiler['retained_warnings'], compiler_database_sha256=compiler['compile_commands_sha256'],
        source_hashes_checked=len(provenance['sources']), binaries=programs, libraries=libraries,
        probe=probe, modern_physical=modern_physical, modern_application=application,
        original_result_control_application_identical=True, rejected_corruptions=corruptions,
        actual_networks=2, builds=1, hardware_calibrated=False, performance_speedup_measured=False,
        dependencies_changed=False, novel_algorithm=False)


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); p.add_argument('--live', action='store_true')
    a = p.parse_args(); need(not a.output.exists(), 'do not replace previous verification')
    save(a.output, verify(a.stage.resolve(), a.live))
