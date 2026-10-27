"""Final saved evidence acceptance, including deliberate corruption rejection."""
import argparse
from copy import deepcopy
import hashlib
from pathlib import Path

from .checks import audit, check, transactions, need
from .workflow import load, save


def verify(stage):
    summary = check(stage)
    # Reuse the independent, full symbolic/byte/window oracle, not controller
    # code or a test driver. Physical paths are checked separately above.
    from fixtures.modern_native.audit import audit as modern_audit
    summary['modern']['functional_acceptance'] = modern_audit(stage/'modern')
    repository = Path(__file__).resolve().parents[2]
    backend = load(stage/'build/backend.json')
    provenance = load(stage/'build/provenance.json')
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    for name, digest in provenance['sources'].items():
        need(sha(repository/name) == digest, 'built root source changed: '+name)
    need(backend['native_commit'] == provenance['native_commit'], 'native build identity mismatch')
    for name in ('independent', 'shared-congestion', 'transactions', 'modern', 'alternate', 'partition'):
        paths = load(stage/name/'result.json')['loaded_libraries']
        native = [Path(p) for p in paths if Path(p).name.startswith('libns3')]
        need(len(native) == 8 and all(p.parent == Path(backend['ns3_core']).parent for p in native), 'mixed native runtime')
        need(all('unified-bus' not in str(p) for p in paths), 'UB library in native process')
    api = __import__('json').loads((stage/'native-api/stdout.log').read_text().splitlines()[-1])
    need(api['passed'] and api['native_api_checks'] == 20 and not api['network_executed'], 'native object checks missing')
    summary.update(passed=True, native_source_commit=backend['native_commit'], native_object_checks=20,
                   binaries={key: sha(stage/'build/output/moe-rdma'/Path(backend[key]).name)
                             for key in ('probe', 'stream_probe', 'transaction_probe', 'transaction_session', 'service')},
                   libraries={Path(p).name: sha(stage/'build/output/lib'/Path(p).name) for p in backend['linked_ns3_libraries']})
    topology = load(stage/'shared-slow-topology/topology.json')
    protocol = load(stage/'inputs/congested-protocol.json')
    original = load(stage/'shared-congestion/result.json')
    rejected = {}
    for name in ('source_binding', 'wrong_reverse_nic', 'wire_ip', 'port_counter', 'queue_area', 'fake_callback', 'incomplete_capture'):
        result = deepcopy(original)
        if name == 'source_binding': result['persistent_transport']['streams'][0]['source_nic'] = 2
        elif name == 'wrong_reverse_nic':
            row = next(r for r in result['packet_capture']['events'] if r[3] == 1 and r[6] == 255 and r[1] < 8)
            row[2] = 3-row[2]
        elif name == 'wire_ip': result['packet_capture']['wire_headers']['headers'][0][1] += 1
        elif name == 'port_counter': result['ports'][0]['model_tx_bytes'] += 1
        elif name == 'queue_area': next(p for p in result['ports'] if 'queue_metrics' in p)['queue_metrics']['area_byte_ns'] += 1
        elif name == 'fake_callback': result['persistent_transport']['work'][0]['acked_ns'] -= 1
        else: result['packet_capture']['complete'] = False
        try: audit(topology, protocol, result)
        except (ValueError, KeyError) as error: rejected[name] = str(error)
        else: raise ValueError('auditor accepted '+name)
    tx = load(stage/'transactions/result.json'); program = load(stage/'inputs/transaction-work.json')
    for name in ('atomic_memory', 'target_time', 'atomic_reply'):
        result = deepcopy(tx)
        if name == 'atomic_memory': result['transactions']['windows'][0]['hex'] = 'ff'+result['transactions']['windows'][0]['hex'][2:]
        elif name == 'target_time': result['transactions']['target_events'][0]['time_ns'] -= 1
        else: next(op for op in result['transactions']['operations'] if op['verb'] == 'fetch_add')['returned_value'] += 1
        try: transactions(program, result)
        except ValueError as error: rejected[name] = str(error)
        else: raise ValueError('auditor accepted '+name)
    need(load(stage/'negative/verification.json')['passed'], 'preflight verification missing')
    save(stage/'verification.json', summary)
    save(stage/'tampering.json', dict(passed=True, rejected=rejected))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True, type=Path)
    args = parser.parse_args()
    verify(args.stage)
