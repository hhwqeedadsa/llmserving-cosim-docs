"""Frozen first-round inputs, bounded observation reuse and dependency-driven replay.

A slice starts from a fresh network at time zero. It is not a simulator snapshot
and never substitutes historical packet timestamps for execution dependencies.
"""
from copy import deepcopy
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile

from .plan import integer, require
from .plan_io import file_sha256
from .user_io import ROOT, fields, inside, inventory, read, relative, save, timestamp, verify_inventory


SCHEMA = 'moe-ub-research-slice/1'


def _copy(source, target, storage):
    source, target = Path(source), Path(target)
    require(not source.is_symlink(), f'Slice inputs must be regular files/directories: {source}')
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=False)
        for child in sorted(source.iterdir()):
            if child.name != '__pycache__':
                _copy(child, target/child.name, storage)
    else:
        require(source.is_file(), f'Missing slice source: {source}')
        target.parent.mkdir(parents=True, exist_ok=True)
        require(not target.exists(), f'Duplicate slice destination: {target}')
        if storage == 'hardlink':
            os.link(source, target)
        else:
            shutil.copyfile(source, target)


def _first_round(protocol):
    messages = [json.loads(row) for row in Path(protocol).read_text().splitlines()]
    commits = [m for m in messages if m.get('type') == 'COMMIT']
    require(commits and commits[0]['now_ns'] == 0 and commits[0]['submissions'],
            'Slice requires an initial graph submission at simulation time zero')
    require(all(not c['submissions'] and c['next_wakeup_ns'] is None for c in commits[1:]) and
            commits[-1]['service_finished'] and all(c['next_wakeup_ns'] is None for c in commits),
            'Only a complete first round with no later arrivals/submissions can be frozen')
    submissions = deepcopy(commits[0]['submissions'])
    expected = {(s['graph_id'], s['wave_id']) for s in submissions}
    completed = [(c['graph_id'], c['wave_id']) for m in messages if m.get('type') == 'DECIDE'
                 for c in m['completed'] if c['kind'] == 'WAVE_DONE']
    require(len(expected) == len(submissions) == len(completed) and set(completed) == expected,
            'First-round graph completions are missing or duplicated')
    return commits[0]['run_id'], submissions


def _archive_metadata(path):
    path = Path(path)
    if path.suffix == '.gz':
        with gzip.open(path, 'rt') as stream:
            return json.load(stream)
    return read(path)


def _import_archive(config, name, output, base, storage, destination):
    entry = config[name]
    fields(entry, {'path', 'inventory'}, label=name)
    archive = relative(base, entry['path']); evidence = relative(base, entry['inventory'])
    detail = _archive_metadata(evidence)
    require(file_sha256(archive) == detail['archive']['sha256'] and archive.stat().st_size == detail['archive']['bytes'],
            f'{name}: archive does not match historical inventory')
    for member in detail['files']:
        inside(output, member)
    target = output/'archives'/(name+'.tar.gz')
    _copy(archive, target, storage)
    normalized = output/'archives'/(name+'.inventory.json.gz')
    with gzip.open(normalized, 'xt', compresslevel=1) as stream:
        json.dump(dict(files=detail['files']), stream, separators=(',', ':'))
    return dict(kind=name, path=str(target.relative_to(output)), inventory=str(normalized.relative_to(output)),
                destination=destination, expanded_logical_bytes=sum(v['bytes'] for v in detail['files'].values()))


def _archived_json(directory, archive, name):
    entries = _archive_metadata(inside(directory, archive['inventory']))['files']
    require(name in entries and entries[name]['bytes'] <= 128*2**20, 'Missing or oversized archived JSON record')
    with tarfile.open(inside(directory, archive['path']), 'r|gz') as stream:
        for member in stream:
            if member.name == name:
                require(member.isfile() and member.size == entries[name]['bytes'], 'Invalid archived JSON member')
                data = stream.extractfile(member).read()
                require(hashlib.sha256(data).hexdigest() == entries[name]['sha256'], 'Archived JSON checksum mismatch')
                return json.loads(data)
    raise ValueError('Missing archived JSON: '+name)


def create(run, output, storage='copy', import_config=None):
    output = Path(output).resolve()
    require(not output.exists(), f'Slice output already exists: {output}')
    archives = []
    if import_config is None:
        from .user_workflow import validate
        run = Path(run).resolve(); validate(run)
        source = read(run/'manifest.json')
        require(source['action'] == 'run', 'Freeze the original configured run, not a sampled derivative')
        raw = run/'raw'; inputs = run/'inputs'
        run_id, submissions = _first_round(raw/'protocol.jsonl')
        report = read(raw/'execution/result.json')
        effective = read(inputs/'effective.json')
        observation = effective['observation']; max_ns = effective['max_simulation_ns']
        output.mkdir(parents=True)
        _copy(raw, output/'raw', storage)
        _copy(inputs, output/'inputs', storage)
        source_scope = 'completed_configured_first_round'
    else:
        path = Path(import_config).resolve(); config = read(path); base = path.parent
        fields(config, {'schema_version', 'run_directory', 'shared_directory', 'system', 'network', 'observation',
                        'graph_archive', 'execution_archive', 'max_simulation_ns', 'source_evidence'}, label=str(path))
        require(config['schema_version'] == 'moe-ub-slice-import/1', 'Unsupported slice import schema')
        raw = relative(base, config['run_directory'], kind='directory')
        shared = relative(base, config['shared_directory'], kind='directory')
        run_id, submissions = _first_round(raw/'protocol.jsonl')
        report = read(raw/'preserved-execution-summary.json')
        require(report['normal_exit'] and report['network_drain']['idle'], 'Historical execution did not complete')
        max_ns = config['max_simulation_ns']; observation = read(relative(base, config['observation']))
        output.mkdir(parents=True); (output/'raw').mkdir(); (output/'inputs').mkdir()
        for name in ('deployment.json', 'bootstrap', 'record-storage.json'):
            _copy(shared/name, output/'raw'/name, storage)
        for name in ('service_result.json', 'protocol.jsonl', 'scope.json', 'preserved-execution-summary.json'):
            _copy(raw/name, output/'raw'/name, storage)
        _copy(relative(base, config['system']), output/'inputs/system.json', storage)
        _copy(relative(base, config['network'], kind='directory'), output/'inputs/network', storage)
        save(output/'inputs/observation.json', observation)
        for name, source_path in config['source_evidence'].items():
            _copy(relative(base, source_path), inside(output/'source', name), storage)
        archives.append(_import_archive(config, 'graph_archive', output, base, storage, 'raw/graph'))
        archives.append(_import_archive(config, 'execution_archive', output, base, storage, 'raw/execution'))
        # A compact historical summary can omit the simulation end time. Bind
        # scope to the actual sealed backend result, never infer it from a wave.
        report = _archived_json(output, archives[-1], 'result.json')
        require(report['normal_exit'] and report['network_drain']['idle'], 'Archived backend result is incomplete')
        source = dict(import_config=config, historical_scope=read(raw/'scope.json'))
        source_scope = 'historical_frozen_first_round_not_configured_service_completion'
    require(report['schema_version'] == 'moe-ub-service-result/2', 'Slice requires bounded result/2 records')
    deployment = read(output/'raw/deployment.json')
    ranks = sorted(r for group in deployment['groups'] for r in group['rank_ids'])
    for submission in submissions:
        inside(output/'raw', submission['semantic_manifest'])
        inside(output/'raw', submission['workload_prefix'])
    manifest = dict(schema_version=SCHEMA, run_id=run_id, created_utc=timestamp(), normal_exit=True,
        scope=source_scope, source=source, initial_state=dict(simulation_ns=0, network='fresh', kv='ready_at_decode_start'),
        simulation_end_ns=report['simulation_end_ns'], capture_end_ns=max_ns, ranks=ranks, submissions=submissions,
        observation=observation, random=report['random'], calibration_status='unvalidated_reference',
        archives=archives, storage=storage, baseline_validation='historical_completion_and_sealed_files',
        artifacts=inventory(output))
    save(output/'slice.json', manifest)
    return inspect(output)


def inspect(directory, *, verify=False):
    directory = Path(directory).resolve(); manifest = read(directory/'slice.json')
    require(manifest.get('schema_version') == SCHEMA and manifest.get('normal_exit') is True,
            'Incomplete or unsupported research slice')
    require(manifest['initial_state'] == dict(simulation_ns=0, network='fresh', kv='ready_at_decode_start'),
            'Unsupported slice initial state')
    integer(manifest['capture_end_ns'], 'slice capture_end_ns', 1)
    integer(manifest['simulation_end_ns'], 'slice simulation_end_ns', 0, manifest['capture_end_ns'])
    ranks = manifest['ranks']
    require(isinstance(ranks, list) and ranks == sorted(set(ranks)) and ranks, 'Invalid slice rank list')
    for rank in ranks:
        integer(rank, 'slice rank', 0, 2**31-2)
    required = {'raw/deployment.json', 'raw/record-storage.json', 'inputs/system.json'}
    required.update('raw/bootstrap/layer.'+str(rank)+'.et' for rank in ranks)
    required.update('inputs/network/'+name for name in ('node.csv', 'topology.csv', 'routing_table.csv', 'transport_channel.csv'))
    archive_kinds = set()
    for archive in manifest['archives']:
        require(archive['kind'] in ('graph_archive', 'execution_archive') and archive['kind'] not in archive_kinds,
                'Unknown or duplicate slice archive')
        archive_kinds.add(archive['kind']); required.update((archive['path'], archive['inventory']))
        expected = 'raw/graph' if archive['kind'] == 'graph_archive' else 'raw/execution'
        require(archive['destination'] == expected, 'Invalid slice archive destination')
    require(not archive_kinds or archive_kinds == {'graph_archive', 'execution_archive'}, 'Incomplete slice archive pair')
    submitted = []
    for submission in manifest['submissions']:
        submitted.extend(submission['rank_ids'])
        inside(directory/'raw', submission['semantic_manifest'])
        inside(directory/'raw', submission['workload_prefix'])
        if not archive_kinds:
            required.add('raw/'+submission['semantic_manifest'])
            required.update('raw/'+submission['workload_prefix']+'.'+str(rank)+'.et' for rank in submission['rank_ids'])
    require(sorted(submitted) == ranks, 'Slice submissions must cover each rank exactly once')
    if not archive_kinds:
        required.add('raw/execution/result.json')
    require(required <= set(manifest['artifacts']), 'Slice inventory omits required execution inputs')
    if verify:
        verify_inventory(directory, manifest['artifacts'])
    return dict(passed=True, scope=manifest['scope'], simulation_end_ns=manifest['simulation_end_ns'],
        capture_end_ns=manifest['capture_end_ns'], ranks=len(manifest['ranks']), graphs=len(manifest['submissions']),
        calibration_status=manifest['calibration_status'], files=len(manifest['artifacts']),
        stored_logical_bytes=sum(v['bytes'] for v in manifest['artifacts'].values()),
        archive_expanded_logical_bytes=sum(v['expanded_logical_bytes'] for v in manifest['archives']),
        integrity='all_file_hashes_verified' if verify else 'metadata_only_no_hash_scan')


def _extract(directory, archive, destination):
    entries = _archive_metadata(inside(directory, archive['inventory']))['files']
    destination.mkdir(parents=True, exist_ok=False); seen = set()
    with tarfile.open(inside(directory, archive['path']), 'r|gz') as stream:
        for member in stream:
            path = inside(destination, member.name)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            require(member.name in entries and member.name not in seen, 'Unknown or repeated archive member')
            path.parent.mkdir(parents=True, exist_ok=True)
            if member.islnk():
                require(member.linkname in seen and entries[member.linkname] == entries[member.name], 'Invalid archive hardlink')
                os.link(inside(destination, member.linkname), path)
            else:
                require(member.isfile() and member.size == entries[member.name]['bytes'], 'Invalid archive member type/size')
                digest = hashlib.sha256()
                with stream.extractfile(member) as source, path.open('xb') as target:
                    while chunk := source.read(2**20):
                        digest.update(chunk); target.write(chunk)
                require(digest.hexdigest() == entries[member.name]['sha256'], 'Archive content checksum mismatch')
            seen.add(member.name)
    require(seen == set(entries), 'Incomplete archive')


def materialize(directory, manifest, output, config, kind):
    """One explicitly owned cache, shared only when the config names it."""
    archives = [a for a in manifest['archives'] if a['kind'] == kind]
    if not archives:
        return directory/'raw'
    cache = Path(config.get('cache_directory', output/'work/slice'))
    cache.mkdir(parents=True, exist_ok=True)
    binding = cache/'slice-binding.json'
    identity = dict(slice_sha256=file_sha256(directory/'slice.json'))
    if binding.exists():
        require(read(binding) == identity, f'Cache belongs to another slice: {cache}')
    else:
        require(not any(cache.iterdir()), f'Cache must be empty or owned by this slice: {cache}')
        save(binding, identity)
        _copy(directory/'raw', cache/'raw', 'copy')
    for archive in archives:
        marker = cache/(archive['kind']+'.ready.json')
        destination = inside(cache, archive['destination'])
        entries = _archive_metadata(inside(directory, archive['inventory']))['files']
        if marker.exists():
            require(read(marker) == archive, 'Cache archive binding differs')
            verify_inventory(destination, entries)
        else:
            require(not destination.exists(), f'Incomplete extraction: {destination}; retain failure and select a new cache')
            _extract(directory, archive, destination)
            save(marker, archive)
    return cache/'raw'


def sample(output, config):
    from .observation import validate_observation
    from .user_workflow import export
    directory = Path(config['slice']); manifest = read(directory/'slice.json')
    observation = config['observation']
    _, selected = validate_observation(observation, manifest['ranks'], manifest['capture_end_ns'])
    _, available = validate_observation(manifest['observation'], manifest['ranks'], manifest['capture_end_ns'])
    require({(p['switch_id'], p['port_id']) for p in selected} <= {(p['switch_id'], p['port_id']) for p in available},
            'Requested port has no captured packet events')
    require(observation['record_provenance'] == manifest['observation']['record_provenance'], 'Cannot change capture provenance')
    require(observation.get('resource_budget') == manifest['observation'].get('resource_budget'),
            'Sampling keeps the source capture resource budget; reduce windows if the declared budget is exceeded')
    raw = materialize(directory, manifest, output, config, 'execution_archive')
    export(raw, observation, manifest['ranks'], manifest['capture_end_ns'], output, config['export_policy'])
    return dict(simulation_end_ns=manifest['simulation_end_ns'], scope='passive_resampling_of_frozen_execution',
                source_slice_sha256=file_sha256(directory/'slice.json'), frontend_executed=False, backend_executed=False)


def replay(output, config):
    from .frozen_replay import run
    directory = Path(config['slice']); manifest = read(directory/'slice.json')
    shared = materialize(directory, manifest, output, config, 'graph_archive')
    raw = output/'raw'
    result = run(directory, manifest, shared, raw, output/'inputs', config)
    return dict(simulation_end_ns=result['simulation_end_ns'], scope='dependency_driven_frozen_first_round',
                source_slice_sha256=file_sha256(directory/'slice.json'), frontend_executed=False,
                graph_generation_executed=False, backend_executed=True, raw='raw', random=result['random'])
