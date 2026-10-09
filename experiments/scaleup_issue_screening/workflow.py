"""Independent E experiment preparation using public platform commands.

Run from the repository root with PYTHONPATH=python:. . No fixture or old-run
inputs are consulted. Large work is supervised by the public host probe.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).resolve().parent / 'configs'
MODULE = 'experiments.scaleup_issue_screening.workflow'


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def host(backend=False):
    result = read(CONFIG / 'host.json')
    result['artifact_roots'] = [str(ROOT / p) for p in result['artifact_roots']]
    if backend:
        result.update(rss_bytes=48*2**30, address_bytes=56*2**30, wall_seconds=7200)
    return result


def command(directory, name, argv):
    directory = Path(directory)
    start = time.monotonic()
    stamp = datetime.now(timezone.utc).isoformat()
    with (directory / (name + '.stdout.log')).open('w') as out, (directory / (name + '.stderr.log')).open('w') as err:
        result = subprocess.run(argv, cwd=ROOT, stdout=out, stderr=err)
    record = dict(command=argv, cwd=str(ROOT), started_utc=stamp,
                  wall_seconds=time.monotonic()-start, exit_code=result.returncode,
                  stdout=name+'.stdout.log', stderr=name+'.stderr.log')
    with (directory / 'commands.jsonl').open('a') as stream:
        stream.write(json.dumps(record)+'\n')
    if result.returncode:
        raise RuntimeError(f'{name} exited {result.returncode}; see {directory / (name+".stderr.log")}')


def cli(directory, name, *args):
    command(directory, name, [sys.executable, '-m', 'moe_ub_cosim', *map(str, args)])


def generate_inputs(case, directory, backend):
    """Write a closed input set; manifest subset retains original identities."""
    directory = Path(directory).resolve()
    spec = read(CONFIG / 'batch.json')
    if digest(backend) != spec['backend_sha256']:
        raise ValueError('Backend differs from the fixed accepted baseline')
    source = read(CONFIG / 'source-base.json')
    manifest = read(CONFIG / 'route-manifest.json')
    count = case['requests']
    if count not in (256, 512, 1024):
        raise ValueError('Unknown predeclared request count')
    manifest['files'] = manifest['files'][:count]
    if len(manifest['files']) != count or len({r['file']['sha256'] for r in manifest['files']}) != count:
        raise ValueError('Source count or distinct-file check failed')
    save(directory / 'route-manifest.json', manifest)
    source['route_manifest'] = dict(path=str(directory/'route-manifest.json'), sha256=digest(directory/'route-manifest.json'))
    source['run_id'] = source['service']['run_id'] = case['case_id']
    source['requests'] = [dict(request_id=i+1, rank_id=i % 128, arrival_ns=0,
        file_id=row['file_id'], first_decode_entry=1, decode_steps=1, target_total_tokens=None)
        for i, row in enumerate(manifest['files'])]
    save(directory/'source.json', source)
    template = read(CONFIG/'layer-template.json')
    layers = []
    # Template token contents are replaced by the service's verified routes.
    # Keep the required schema valid without creating any simulated requests.
    for index in range(3, 61):
        layer = deepcopy(template)
        layer['layer_id'] = index
        for token in layer['tokens']:
            token['layer_id'] = index
        layers.append(layer)
    save(directory/'source-layers.json', dict(schema_version='moe-ub-layer-segments/1',
                                             groups=[dict(group_id='ep0', layers=layers)]))
    save(directory/'case.json', dict(**case, schema_version='issue-screening-case/1',
        owned_by='scaleup_issue_screening', platform_commit=spec['platform_commit'],
        backend_sha256=digest(backend), config_sha256={p.name:digest(p) for p in CONFIG.iterdir() if p.is_file()},
        source_files=count, independent_group='g0', decode_step=spec['decode_step']))


def prepare(case_id, directory, backend):
    spec = read(CONFIG/'batch.json')
    case = next(c for c in spec['cases'] if c['case_id'] == case_id)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    backend = Path(backend).resolve()
    generate_inputs(case, directory, backend)
    command(directory, 'fabric', [sys.executable, '-m', 'moe_ub_cosim.fabric',
        '--input', str(CONFIG/'fabric.json'), '--defaults', str(CONFIG/'network-defaults.json'),
        '--output-dir', str(directory/'source-network')])
    source_run = dict(schema_version='moe-ub-segment-source/1', host=host(),
        inputs=dict(service=str(directory/'source.json'), layer=str(directory/'source-layers.json'),
                    system=str(CONFIG/'system.json'), network=str(directory/'source-network')),
        observation=str(CONFIG/'observation.json'), record_policy=str(CONFIG/'record-policy.json'),
        export_policy=str(CONFIG/'export-policy.json'), backend=str(backend),
        backend_timeout_seconds=7000, max_simulation_ns=10000000)
    save(directory/'source-run.json', source_run)
    save(directory/'select.json', dict(schema_version='moe-ub-segment-prepare/1',
        source_run='source-run.json', first_layer=case['first_layer'], layer_count=case['layer_count'],
        decode_step=spec['decode_step']))
    cli(directory, 'prepare', 'segment', 'prepare', '--config', directory/'select.json', '--output', directory/'prepared')
    attributes = {'ns3::UbSwitchAllocator::AllocationTime':'10ns', 'ns3::UbLdstInstance::ThreadNum':1,
        'ns3::UbLdstThread::StoreOutstanding':32, 'ns3::UbLdstThread::LoadOutstanding':32,
        'ns3::UbLdstThread::StoreRequestSize':4096, 'ns3::UbLdstThread::LoadResponseSize':4096,
        'ns3::UbLdstApi::RequestIdCount':256, 'ns3::UbLdstApi::UsePacketSpray':True,
        'ns3::UbLdstApi::UseShortestPaths':True}
    scenario = dict(schema_version='moe-ub-scenario-run/2', scenario_id=case_id, basis='selected_reference',
        service=str(directory/'prepared/service.json'), layer=str(directory/'prepared/layers.json'),
        system=str(CONFIG/'system.json'), backend=dict(reference_file=str(CONFIG/'backend-reference.yaml'),
        profile_by_role=dict(dispatch='native_store', combine='native_store'), attributes=attributes,
        globals={}, tp_max_data_payload_bytes=4096, transport_channel_setup='precreated'),
        network=dict(fabric_file=str(CONFIG/'fabric.json'), defaults=read(CONFIG/'network-defaults.json'), nodes=[], links=[]),
        execution=dict(chunk_bytes=8192, send_slots_per_rank=0, seed=1, run=1), observation=None, max_simulation_ns=10000000)
    (directory/'scenario.yaml').write_text(yaml.safe_dump(scenario, sort_keys=False))
    cli(directory, 'resolve', 'resolve', '--config', directory/'scenario.yaml', '--backend', backend, '--output', directory/'resolved')
    run = read(directory/'prepared/run.json')
    for key, value in run['inputs'].items():
        run['inputs'][key] = str(directory/'prepared'/value)
    run['inputs']['network'] = str(directory/'resolved/network')
    for key in ('observation', 'record_policy', 'export_policy', 'input_evidence'):
        run[key] = str(directory/'prepared'/run[key])
    run['host'], run['export_host'] = host(True), host()
    save(directory/'run.json', run)
    print(json.dumps(dict(prepared=True, case=case_id, output=str(directory))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('prepare', help='Budgeted public source verification, segment preparation and scenario resolution')
    p.add_argument('--case', required=True, choices=[c['case_id'] for c in read(CONFIG/'batch.json')['cases']])
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--backend', type=Path, required=True)
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    p = sub.add_parser('run', help='Execute and export through moe-ub run; retain all failures')
    p.add_argument('--case-dir', type=Path, required=True)
    args = parser.parse_args()
    if Path.cwd().resolve() != ROOT:
        parser.error('Run from the repository root (all source paths are relative to it)')
    if args.action == 'prepare':
        if args.worker:
            prepare(args.case, args.output, args.backend)
        else:
            from moe_ub_cosim.host_probe import probe
            report = probe([sys.executable, '-m', MODULE, 'prepare', '--worker', '--case', args.case,
                '--output', str(args.output.resolve()), '--backend', str(args.backend.resolve())],
                str(args.output.resolve())+'-prepare', **host())
            if report['status'] != 'completed':
                raise SystemExit('Preparation failed: '+report['status'])
    elif args.action == 'run':
        directory = args.case_dir.resolve()
        if read(directory/'case.json')['owned_by'] != 'scaleup_issue_screening':
            parser.error('Not an owned experiment case')
        cli(directory, 'run', 'run', '--config', directory/'run.json', '--output', directory/'run')


if __name__ == '__main__':
    main()
