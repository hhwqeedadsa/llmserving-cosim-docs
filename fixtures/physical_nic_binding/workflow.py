"""Bounded public platform acceptance, separate from network benefit research."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

from moe_ub_cosim.host_probe import probe


def load(path): return json.loads(path.read_bytes())
def save(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def run(stage, label, command, *, expected=0, build=False):
    records_path = stage/'commands.json'
    records = load(records_path) if records_path.exists() else []
    assert label not in {row['label'] for row in records}
    elapsed = sum(load(path).get('wall_seconds', 0) for path in stage.rglob('probe.json')
                  if 'restored' not in path.relative_to(stage).parts)
    assert elapsed+(400 if build else 60) <= 1800, 'cumulative monitored wall budget'
    public_networks = {'rdma-stream-probe', 'rdma-transaction-probe', 'modern-deepep-native'}
    is_network = bool(public_networks.intersection(map(str, command)))
    used_networks = sum(bool(public_networks.intersection(row['command'])) for row in records)
    assert not is_network or used_networks < 8, 'actual network count budget'
    report = probe(list(map(str, command)), stage/label, wall_seconds=400 if build else 60,
        rss_bytes=2**31 if build else 2**30, address_bytes=2**32 if build else 2**31,
        artifact_bytes=2**31-4*2**20, artifact_roots=[stage], min_free_bytes=8*2**30)
    records.append(dict(label=label, command=list(map(str, command)), status=report['status'],
                        exit_code=report.get('child_exit_code'), expected=expected, actual_network=is_network))
    save(records_path, records)
    assert report['status'] == ('completed' if expected == 0 else 'child_failed'), label
    assert (report['child_exit_code'] == 0) == (expected == 0), label
    return report


def cli(*args): return [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', *args]


def prepare(repository, stage):
    inputs = stage/'inputs'
    inputs.mkdir()
    single = load(repository/'examples/scaleout_rdma/small-clos-100g.json')
    base = load(repository/'examples/modern_deepep_native/topology.json')
    base['fabric'].update(rails=2, planes=1, upper_tier_scope='shared')
    base['nic']['rails_by_gpu_slot'] = [[0, 1], [0, 1]]
    independent = deepcopy(base)
    independent['fabric']['upper_tier_scope'] = 'rail'
    redundant = deepcopy(base)
    redundant['fabric'].update(aggs_per_pod_per_group=2, cores_per_group=2, tor_uplinks=2)
    for name, value in (('single', single), ('independent', independent), ('shared', base), ('redundant', redundant)):
        save(inputs/(name+'-topology.json'), value)
    protocol = load(repository/'examples/scaleout_rdma/rdma.json')
    protocol.update(queue_metrics=True, packet_capture=dict(nodes=[], start_ns=0, end_ns=5000000,
                                                           max_events=20000, wire_headers=True))
    save(inputs/'protocol.json', protocol)
    single_work = load(repository/'examples/scaleout_rdma_streams/work.json')
    save(inputs/'single-work.json', single_work)
    streams = [dict(id=i, src=src, dst=dst, plane=0, wire_port=21000+i,
                    source_nic=sn, destination_nic=dn, queue_depth=2)
               for i, (src, dst, sn, dn) in enumerate(((0, 4, 1, 1), (1, 5, 2, 2),
                                                       (0, 4, 2, 1), (4, 0, 1, 2)))]
    # Both source NICs address the SAME remote NIC in the shared condition.
    work = dict(schema_version='moe-rdma-stream-work/1', streams=streams,
                work=[dict(id=i, stream=i, bytes=32768, after=[]) for i in range(4)])
    save(inputs/'shared-work.json', work)
    isolated = deepcopy(work)
    isolated['streams'] = isolated['streams'][:2]
    isolated['work'] = isolated['work'][:2]
    save(inputs/'independent-work.json', isolated)
    reference = load(repository/'examples/scaleout_rdma_streams/work-reference.json')['dcqcn_reference']
    reference['np_interval_ns'] = 5000
    reference['max_events'] = 10000
    work['dcqcn_reference'] = reference
    save(inputs/'shared-reference-work.json', work)
    congested = deepcopy(protocol)
    congested['ecn_by_rate'] = {str(rate): dict(kmin_bytes=1024, kmax_bytes=2048, pmax=1.0)
                               for rate in (100000000000, 10000000000)}
    slow = deepcopy(base)
    slow['links']['agg_core']['rate_bps'] = 10000000000
    save(inputs/'shared-slow-topology.json', slow)
    save(inputs/'congested-protocol.json', congested)
    transaction = load(repository/'examples/scaleout_rdma_transactions/work.json')
    transaction['connections'][1].update(src=0, source_nic=2, destination_nic=1)
    save(inputs/'transaction-work.json', transaction)
    modern = load(repository/'examples/modern_deepep_native/input.json')
    for rank in modern['deployment']:
        for device in rank['devices']:
            device['plane'] = 0
    save(inputs/'modern-input.json', modern)
    recovery = dict(mode='go_back_n/1', initial_rto_ns=80000, max_rto_ns=320000,
                    max_retries=2, max_events=2000)
    failure_work = deepcopy(load(inputs/'shared-work.json'))
    failure_work['transport_recovery'] = recovery
    save(inputs/'failure-work.json', failure_work)
    save(stage/'prepared.json', dict(inputs={path.name: sha(path) for path in inputs.iterdir()},
         maximum_actual_networks=8, research_experiment=False, new_binding_specification='6147fe8'))


def before(repository, stage, previous_build):
    old = load(previous_build/'backend.json')
    expected = load(repository/'fixtures/native_build_identity/verification.json')
    assert old['native_commit'] == expected['native_commit']
    assert sha(Path(old['stream_probe'])) == expected['binaries']['stream_probe']['sha256']
    for path, digest in expected['loaded_libraries'].items():
        assert sha(Path(path)) == digest
    run(stage, 'before-topology', cli('topology', '--config', stage/'inputs/single-topology.json',
                                     '--output', stage/'single-topology'))
    run(stage, 'before-network', cli('rdma-stream-probe', '--topology', stage/'single-topology',
        '--protocol', stage/'inputs/protocol.json', '--config', stage/'inputs/single-work.json',
        '--backend', old['stream_probe'], '--output', stage/'before-single'))
    save(stage/'before.json', dict(passed=True, network_executions=1, native_commit=old['native_commit'],
         old_runtime_hashes_verified=True, result_sha256=sha(stage/'before-single/result.json')))


def build(repository, stage, args):
    run(stage, 'fresh-build-saved-source', cli('rdma-build', '--output', stage/'build',
        '--cmake', args.cmake, '--ninja', args.ninja,
        '--protobuf-prefix', args.protobuf_prefix), build=True)


def native_api(repository, stage):
    backend = load(stage/'build/backend.json')
    output = stage/'build/output'
    executable = stage/'rdma-local-address-test'
    run(stage, 'native-api-compile-linked', ['/usr/bin/c++', '-std=c++20',
        '-I'+str(output/'include'), repository/'tests/native/rdma_local_address_test.cc',
        '-Wl,-rpath,'+str(output/'lib'), '-Wl,--no-as-needed', *backend['linked_ns3_libraries'], '-o', executable])
    run(stage, 'native-api', [executable])


def networks(repository, stage, selected):
    backend = load(stage/'build/backend.json')
    inputs = stage/'inputs'
    def topology(name):
        directory = stage/(name+'-topology')
        if not directory.exists():
            run(stage, name+'-topology-command', cli('topology', '--config', inputs/(name+'-topology.json'),
                                                    '--output', directory))
        return directory
    def stream(name, fabric, work, protocol='protocol.json', expected=0):
        run(stage, name+'-network', cli('rdma-stream-probe', '--topology', topology(fabric),
            '--protocol', inputs/protocol, '--config', inputs/work,
            '--backend', backend['stream_probe'], '--output', stage/name), expected=expected)
    if selected == 'after-single':
        stream('after-single', 'single', 'single-work.json')
        before_result, after_result = [load(stage/name/'result.json') for name in ('before-single', 'after-single')]
        # Only loader locations change; every actual packet, event, time and queue stays.
        for result in (before_result, after_result): del result['loaded_libraries']
        assert before_result == after_result, 'single-NIC result changed'
        save(stage/'single-compatibility.json', dict(passed=True, ignored_fields=['loaded_libraries']))
    if selected == 'independent': stream('independent', 'independent', 'independent-work.json')
    if selected == 'shared-congestion':
        stream('shared-congestion', 'shared-slow', 'shared-reference-work.json', 'congested-protocol.json')
    if selected == 'transactions':
        run(stage, 'transactions-network', cli('rdma-transaction-probe', '--topology', topology('shared'),
            '--protocol', inputs/'protocol.json', '--config', inputs/'transaction-work.json',
            '--backend', backend['transaction_probe'], '--output', stage/'transactions'))
    if selected == 'modern':
        run(stage, 'modern-network', cli('modern-deepep-native', '--topology', topology('shared'),
            '--protocol', inputs/'protocol.json', '--config', inputs/'modern-input.json',
            '--backend', backend['transaction_session'], '--output', stage/'modern', '--callback-batches'))
    # Choose the first uplink of source0's NIC1 by topology, before outcomes.
    for name, fabric, expected in (('alternate', 'redundant', 0), ('partition', 'shared', 1)):
        if selected != name: continue
        directory = topology(fabric)
        generated = load(directory/'topology.json')
        access = next(edge for edge in generated['links'] if edge['src'] == 0 and edge['src_port'] == 1)
        tor = access['dst']
        uplink = next(edge for edge in generated['links'] if edge['src'] == tor and edge['tier'] == 'tor_agg')
        protocol = load(inputs/'protocol.json')
        protocol['link_failures'] = dict(mode='switch_links/1', events=[dict(src=uplink['src'], dst=uplink['dst'],
            time_ns=1500, route_update_delay_ns=2000)])
        save(inputs/(name+'-protocol.json'), protocol)
        stream(name, fabric, 'failure-work.json', name+'-protocol.json', expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'before', 'build', 'native-api', 'networks'))
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--previous-build', type=Path)
    parser.add_argument('--cmake', default='cmake')
    parser.add_argument('--ninja', default='ninja')
    parser.add_argument('--protobuf-prefix', type=Path)
    parser.add_argument('--case', choices=('after-single', 'independent', 'shared-congestion',
                                         'transactions', 'modern', 'alternate', 'partition'))
    args = parser.parse_args()
    repository, stage = args.repository.resolve(), args.stage.resolve()
    if args.action == 'prepare': prepare(repository, stage)
    elif args.action == 'before': before(repository, stage, args.previous_build.resolve())
    elif args.action == 'build': build(repository, stage, args)
    elif args.action == 'native-api': native_api(repository, stage)
    else: networks(repository, stage, args.case)


if __name__ == '__main__': main()
