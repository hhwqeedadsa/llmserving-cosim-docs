"""Bounded independent ASTRA-native RDMA builds and real small-fabric probes.

The probe is a network gate, not yet the LLMServingSim online service.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .scaleout_topology import require, fields, integer
from .scaleout_limits import limits

ROOT = Path(__file__).resolve().parents[2]
ASTRA = ROOT / 'third_party/LLMServingSim/astra-sim'
NS3 = ASTRA / 'extern/network_backend/ns-3'
NATIVE_COMMIT = '3964b1d41cb5f7b51004c316b0eae1a3713c2498'


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_json(path, maximum):
    path = Path(path)
    require(path.stat().st_size <= maximum, 'input byte budget exceeded: ' + str(path))
    return json.loads(path.read_text())


def validate_protocol(c):
    fields(c, 'schema_version cc_mode payload_bytes chunk_bytes ack_interval_bytes base_rtt_ns '
           'switch_buffer_bytes ecn_by_rate seed' + (' limits' if c.get('schema_version') == 'moe-scaleout-rdma/2' else '')
           + ''.join(' '+key for key in ('drop_once','faults','packet_capture','admission','lifecycle_capture','queue_metrics','recovery') if key in c), 'RDMA protocol')
    budget=limits(c)
    from .scaleout_admission import validate_options
    validate_options(c)
    if 'queue_metrics' in c:require(type(c['queue_metrics']) is bool,'queue_metrics must be boolean')
    if 'recovery' in c:
        from .scaleout_recovery_config import validate as validate_recovery
        validate_recovery(c['recovery'],budget['max_sim_ns'])
    require(c['schema_version'] in ('moe-scaleout-rdma/1','moe-scaleout-rdma/2') and type(c['cc_mode']) is int and c['cc_mode'] == 1,
            'only explicit native DCQCN mode 1 is currently supported')
    for key, minimum, maximum in [('payload_bytes', 64, 9000), ('chunk_bytes', 1, 1048576),
            ('ack_interval_bytes', 1, 9000), ('base_rtt_ns', 1, 1000000),
            ('switch_buffer_bytes', 1048576, 16777216), ('seed', 1, 2**31 - 1)]:
        integer(c[key], key, minimum, maximum)
    require(c['ack_interval_bytes'] == 1,
            'this pinned native receiver needs ACK interval 1 for arbitrary short messages; '
            'delayed ACK semantics are not validated')
    require(isinstance(c['ecn_by_rate'], dict) and 1 <= len(c['ecn_by_rate']) <= 8, 'invalid ECN rate map')
    for rate, row in c['ecn_by_rate'].items():
        require(isinstance(rate, str) and rate.isdecimal() and 0 < int(rate) <= 10**15, 'invalid ECN rate key')
        fields(row, 'kmin_bytes kmax_bytes pmax', 'ECN thresholds')
        integer(row['kmin_bytes'], 'kmin_bytes', maximum=16777216)
        integer(row['kmax_bytes'], 'kmax_bytes', row['kmin_bytes'], 16777216)
        require(type(row['pmax']) in (int, float) and 0 <= row['pmax'] <= 1, 'invalid ECN pmax')
    if 'drop_once' in c:
        require('faults' not in c, 'choose drop_once or faults, not both')
        fields(c['drop_once'], 'message_id dst_gpu sequence', 'bounded fault injection')
        integer(c['drop_once']['message_id'], 'drop message', 0, 49999)
        integer(c['drop_once']['dst_gpu'], 'drop destination', 0, 31)
        integer(c['drop_once']['sequence'], 'drop byte sequence', 0, 67108863)
    if 'faults' in c:
        require(isinstance(c['faults'],list) and 1<=len(c['faults'])<=32, 'expected 1..32 bounded fault rules')
        for rule in c['faults']:
            fields(rule, 'node port message_id kind sequence start_ns end_ns count', 'fault rule')
            integer(rule['node'], 'fault receive node', 0, budget['max_gpus']+budget['max_switches']-1)
            integer(rule['port'], 'fault receive port (0=all)', 0, 1024)
            integer(rule['message_id'], 'fault message', 0, 2**31-1)
            require(rule['kind'] in ('data','ack','nack'), 'fault kind must be data, ack or nack')
            integer(rule['sequence'], 'fault byte sequence', 0, 67108864)
            integer(rule['start_ns'], 'fault start_ns', 0, budget['max_sim_ns']-1)
            integer(rule['end_ns'], 'fault end_ns', rule['start_ns']+1, budget['max_sim_ns'])
            integer(rule['count'], 'fault count', 1, 10000)
    if 'packet_capture' in c:
        capture=c['packet_capture']
        fields(capture, 'nodes start_ns end_ns max_events'+(' wire_headers' if 'wire_headers' in capture else ''), 'packet capture')
        if 'wire_headers' in capture:
            require(type(capture['wire_headers']) is bool, 'wire_headers must be boolean')
        require(isinstance(capture['nodes'],list) and len(capture['nodes'])<=budget['max_gpus']+budget['max_switches'],
                'capture nodes must be a bounded list (empty means all)')
        for node in capture['nodes']:
            integer(node,'capture node',0,budget['max_gpus']+budget['max_switches']-1)
        require(len(set(capture['nodes']))==len(capture['nodes']), 'duplicate capture node')
        integer(capture['start_ns'], 'capture start_ns', 0, budget['max_sim_ns']-1)
        integer(capture['end_ns'], 'capture end_ns', capture['start_ns']+1, budget['max_sim_ns'])
        integer(capture['max_events'], 'capture max_events', 1, 100000)


def monitored(command, output, artifacts, *, build=False, runtime=None):
    budget = runtime or limits({'schema_version':'moe-scaleout-rdma/1'})
    argv = [sys.executable, '-m', 'moe_ub_cosim.host_probe', '--output-dir', str(output),
            '--rss-bytes', str(2147483648 if build else budget['rss_bytes']),
            '--address-bytes', str(4294967296 if build else budget['address_bytes']),
            '--artifact-bytes', str(536870912 if build else budget['artifact_bytes']),
            '--wall-seconds', '300' if build else str(budget['wall_seconds']),
            '--min-free-bytes', '8589934592']
    for root in artifacts:
        argv.extend(['--artifact-root', str(root)])
    argv.extend(['--', *map(str, command)])
    try:
        subprocess.run(argv, cwd=ROOT, check=True, timeout=330 if build else budget['wall_seconds']+30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError('bounded RDMA command failed; inspect ' + str(output)) from error


def build(directory, *, cmake='cmake', ninja='ninja', protobuf_prefix=None, resume=False):
    directory = Path(directory).resolve()
    previous_build = None
    if resume:
        previous_build = checked_json(directory / 'provenance.json', 1048576)
        require(previous_build['native_commit'] == NATIVE_COMMIT and
                (directory / 'cmake/CMakeCache.txt').is_file(), 'not an owned pinned RDMA build')
    else:
        require(not directory.exists(), 'RDMA build requires a new isolated directory (or explicit --resume)')
    require(shutil.disk_usage(directory.parent).free >= 8589934592, 'build volume below 8GiB reserve')
    require(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=NS3, text=True).strip() == NATIVE_COMMIT,
            'native ns-3 differs from pinned commit')
    require(subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--'], cwd=NS3).returncode == 0,
            'tracked native ns-3 source is dirty')
    cmake, ninja = shutil.which(cmake), shutil.which(ninja)
    require(cmake and ninja, 'pass existing cmake and ninja; no installation is attempted')
    schema = ASTRA / 'extern/graph_frontend/chakra/schema/protobuf'
    require(all((schema / ('et_def.pb.' + ext)).is_file() for ext in ('h', 'cc')),
            'generate the locked Chakra protobuf once before configuring native ASTRA')
    directory.mkdir(exist_ok=resume)
    logs = directory / 'logs'; logs.mkdir(exist_ok=resume)
    if resume:
        attempt = 1
        while (logs / f'resume-{attempt}').exists():
            attempt += 1
        logs = logs / f'resume-{attempt}'; logs.mkdir()
    disabled = ('EXAMPLES', 'TESTS', 'MPI', 'MTP', 'ASSERT', 'LOG', 'WARNINGS_AS_ERRORS',
                'NATIVE_OPTIMIZATIONS', 'CCACHE', 'PRECOMPILE_HEADERS', 'FETCH_OPTIONAL_COMPONENTS',
                'NETANIM', 'CPM', 'VCPKG')
    configure = [cmake, '-S', NS3, '-B', directory / 'cmake', '-G', 'Ninja',
        '-DCMAKE_MAKE_PROGRAM=' + ninja, '-DCMAKE_BUILD_TYPE=Release',
        '-DCMAKE_PROJECT_NS3_INCLUDE=' + str(ROOT / 'backend/rdma/inject.cmake'),
        '-DNS3_OUTPUT_DIRECTORY=' + str(directory / 'output'),
        '-DNS3_ENABLED_MODULES=core;network;internet;point-to-point;applications;csma',
        *('-DNS3_' + name + '=OFF' for name in disabled)]
    if protobuf_prefix is not None:
        prefix = Path(protobuf_prefix).resolve()
        require((prefix / 'bin/protoc').is_file() and
                (prefix / 'include/google/protobuf/message.h').is_file(),
                'protobuf-prefix must refer to an existing installation')
        configure.extend(['-DCMAKE_PREFIX_PATH=' + str(prefix),
                          '-DMOE_PROTOBUF_CONFIG=ON',
                          '-DProtobuf_PROTOC_EXECUTABLE=' + str(prefix / 'bin/protoc')])
    provenance = dict(native_commit=NATIVE_COMMIT, commands=[], network_executed=False,
        platform_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        sources={str(p.relative_to(ROOT)): digest(p) for p in sorted([*(ROOT/'backend/rdma').glob('*'),
            ROOT/'backend/communication_admission.hh',ROOT/'backend/queue_integral.hh',ROOT/'backend/retransmission_timer.hh',
            ROOT/'backend/ack_rtt_estimator.hh',ROOT/'backend/source_recovery.hh',ROOT/'backend/recovery_history.hh']) if p.is_file()})
    if previous_build is not None:
        provenance['previous_build'] = previous_build
    lock = NS3 / '.lock-ns3_linux_build'
    previous = lock.read_bytes() if lock.exists() else None
    try:
        for name, cmd in [('configure', configure), ('compile', [cmake, '--build', directory / 'cmake',
                  '--target', 'moe-rdma-probe', 'moe-rdma-stream-probe', 'moe-rdma-transaction-probe', 'moe-rdma-transaction-session', 'moe-rdma-service', '--parallel', '2'])]:
            provenance['commands'].append(dict(cwd=str(ROOT), argv=list(map(str, cmd))))
            save(directory / 'provenance.json', provenance)
            monitored(cmd, logs / name, [directory], build=True)
    finally:
        if previous is not None:
            lock.write_bytes(previous)
        elif lock.exists():
            lock.unlink()
    targets = checked_json(directory / 'cmake/moe-rdma-targets.json', 65536)
    overlay = directory / 'cmake/moe-serial-native'
    provenance['serial_build_overlay_sha256'] = {p.name: digest(p) for p in overlay.glob('*.cc')}
    save(directory / 'provenance.json', provenance)
    binary = Path(targets['probe'])
    linked = subprocess.check_output(['ldd', binary], text=True)
    (directory / 'ldd.txt').write_text(linked)
    native_libraries = [line.split('=>', 1)[1].split()[0] for line in linked.splitlines() if 'libns3' in line and '=>' in line]
    require(native_libraries and 'not found' not in linked and
            all(Path(p).resolve().is_relative_to(directory / 'output') for p in native_libraries),
            'binary links another ns-3 build or unresolved libraries')
    result = dict(passed=True, **targets, native_commit=NATIVE_COMMIT,
                  linked_ns3_libraries=native_libraries, state='native_rdma_probes_and_service_built_not_executed')
    save(directory / 'backend.json', result)
    return result


def validate_probe(t, c, batch, *, match_faults=True):
    validate_protocol(c)
    budget=limits(c)
    require(t.get('schema_version') in ('moe-scaleout-physical-topology/1','moe-scaleout-physical-topology/2'), 'wrong physical graph schema')
    nodes = t['nodes']; gpus = {n['id'] for n in nodes if n['kind'] == 'gpu'}
    require(len(nodes)-len(gpus) <= budget['max_switches'] and len(t['links']) <= budget['max_links'], 'native graph budget exceeded')
    require(0 < len(gpus) <= budget['max_gpus'], 'GPU budget exceeded')
    require((len(nodes)-len(gpus))*1025*1025*8*4 + 268435456 < budget['rss_bytes'],
            'fixed switch counters alone exceed declared RSS budget')
    require(all(str(l['rate_bps']) in c['ecn_by_rate'] for l in t['links']), 'missing ECN threshold for link rate')
    require(isinstance(batch, list) and 0 < len(batch) <= 128, 'probe message budget exceeded')
    adjacency = {n['id']: [] for n in nodes}
    by_id = {n['id']: n for n in nodes}
    if 'drop_once' in c:
        require(c['drop_once']['dst_gpu'] in gpus, 'legacy drop destination is not a GPU')
    for node in c.get('packet_capture',{}).get('nodes',[]):
        require(node in by_id, 'capture node absent from topology')
    for rule in c.get('faults',[]):
        require(rule['node'] in by_id and (rule['port']==0 or any(
            link[side]==rule['node'] and link[side+'_port']==rule['port']
            for link in t['links'] for side in ('src','dst'))), 'fault receive node/port absent from topology')
    for link in t['links']:
        adjacency[link['src']].append(link['dst'])
        adjacency[link['dst']].append(link['src'])
    ids = set()
    for m in batch:
        fields(m, 'id src dst bytes plane ready_ns', 'probe message')
        integer(m['id'], 'message id', minimum=0, maximum=2**31-1)
        require(m['id'] not in ids, 'duplicate message id'); ids.add(m['id'])
        integer(m['src'], 'source GPU', minimum=0, maximum=budget['max_gpus']-1)
        integer(m['dst'], 'destination GPU', minimum=0, maximum=budget['max_gpus']-1)
        require(m['src'] in gpus and m['dst'] in gpus and m['src'] != m['dst'], 'invalid endpoint pair')
        integer(m['bytes'], 'message bytes', maximum=67108864)
        byte_cap=c.get('admission',{}).get('max_active_bytes_per_source',0)
        local=t.get('intra_host',{}).get('enabled',False) and by_id[m['src']]['server']==by_id[m['dst']]['server']
        require(not byte_cap or local or m['bytes']<=byte_cap,
                'message exceeds max_active_bytes_per_source; increase budget or regenerate smaller logical messages')
        peer_cap=c.get('admission',{}).get('per_destination',{}).get('max_bytes',0)
        require(not peer_cap or local or m['bytes']<=peer_cap,
                'message exceeds per_destination.max_bytes; change explicit share or logical messages')
        integer(m['plane'], 'message plane', minimum=0, maximum=15)
        integer(m['ready_ns'], 'message ready_ns', minimum=0, maximum=budget['max_sim_ns']-1)
        seen, todo = {m['src']}, [m['src']]
        while todo:
            for peer in adjacency[todo.pop()]:
                if peer in seen:
                    continue
                if peer in gpus:
                    if peer == m['dst']:
                        seen.add(peer)
                    continue  # GPUs never bridge two rails/planes.
                if by_id[peer]['plane'] == m['plane']:
                    seen.add(peer); todo.append(peer)
        require(m['dst'] in seen, 'message has no packet-switch path in the selected rail/plane')
    if 'drop_once' in c and match_faults:
        fault=c['drop_once']; selected=[m for m in batch if m['id']==fault['message_id']]
        require(len(selected)==1 and selected[0]['dst']==fault['dst_gpu'] and
                fault['sequence']<selected[0]['bytes'] and fault['sequence']%c['payload_bytes']==0,
                'drop rule does not identify one actual destination data packet')
    if match_faults:
        for rule in c.get('faults',[]):
            selected=[m for m in batch if m['id']==rule['message_id']]
            require(len(selected)==1, 'fault message absent from probe')
            m=selected[0]
            if rule['kind']=='data':
                require(rule['sequence']<m['bytes'] and rule['sequence']%c['payload_bytes']==0,
                        'fault sequence is not an actual data offset')
            else:
                require(rule['sequence']<=m['bytes'], 'fault ACK/NACK sequence exceeds message bytes')


def probe(topology, config, messages, backend, output):
    topology, config, messages, backend, output = (Path(p).resolve() for p in (topology, config, messages, backend, output))
    c = checked_json(config, 65536)
    t = checked_json(topology / 'topology.json', 8388608)
    batch = checked_json(messages, 65536)
    validate_probe(t, c, batch)
    require(backend.is_file() and os.access(backend, os.X_OK), 'pass the native RDMA probe executable')
    require(shutil.disk_usage(output.parent).free >= 8589934592, 'run volume below 8GiB reserve')
    output.mkdir(exist_ok=False)
    command = [backend, topology/'topology.json', config, messages, output/'result.json']
    save(output/'inputs.json', dict(command=list(map(str, command)), native_commit=NATIVE_COMMIT,
        hashes={str(p):digest(p) for p in [topology/'topology.json',config,messages,backend]}))
    monitored(command, output/'execution', [output],runtime=limits(c))
    result = checked_json(output/'result.json', limits(c)['result_json_bytes'])
    require(result['passed'] and result['native_network_executed'], 'native probe did not finish cleanly')
    from .scaleout_admission import audit
    audit(result)
    from .scaleout_metrics import network_summary, phase_intervals
    network_summary(result)
    for flow in result['messages']:phase_intervals(flow,result['finish_ns'])
    from .scaleout_recovery import audit as recovery_audit
    recovery_audit(result)
    return dict(passed=True, output=str(output), messages=result['completed_messages'],
                state='native_rdma_probe_executed_not_service')
