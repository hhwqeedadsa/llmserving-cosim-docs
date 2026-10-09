"""Public experiment lifecycle with explicit host budgets and reusable exporters."""
import argparse
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

from .plan import integer, require
from .plan_io import file_sha256
from .user_io import ROOT, fields, host_policy, inventory, read, relative, save, timestamp, verify_inventory, versions


def configuration(path, action, backend=None):
    path = Path(path).resolve(); config = read(path); base = path.parent
    common = {'schema_version', 'host', 'export_policy'}
    extra = {'run': {'inputs', 'observation', 'record_policy', 'max_simulation_ns', 'backend', 'backend_timeout_seconds'},
             'sample': {'observation'}, 'replay': {'backend', 'backend_timeout_seconds'}}[action]
    replay_v2=action=='replay' and config.get('schema_version')=='moe-ub-replay/2'
    if replay_v2:extra=extra|{'max_simulation_ns'}
    optional = ({'network', 'cache_directory', 'export_host'}|({'observation'} if replay_v2 else set()) if action == 'replay' else
                {'cache_directory', 'period_ns'} if action == 'sample' else {'export_host','input_evidence'})
    fields(config, common | extra, optional, label=str(path))
    require(replay_v2 or config['schema_version'] == f'moe-ub-{action}/1', f'{path}: unsupported schema_version')
    if replay_v2:integer(config['max_simulation_ns'],'max_simulation_ns',1)
    config['host'] = host_policy(config['host'], base)
    if 'export_host' in config:
        config['export_host'] = host_policy(config['export_host'], base)
    if action in ('run', 'replay'):
        config['backend'] = str(relative(base, str(backend.resolve()) if backend else config['backend']))
        integer(config['backend_timeout_seconds'], 'backend_timeout_seconds', 1)
    for key in ('observation', 'record_policy', 'export_policy'):
        if key in config:
            if key == 'observation' and action == 'sample' and config[key] is None:
                continue
            config[key] = read(relative(base, config[key]))
    if 'period_ns' in config:
        integer(config['period_ns'], 'period_ns', 1)
    from .bounded_export import policy
    policy(config['export_policy'])
    if action == 'run':
        from .record_storage import validate_policy
        validate_policy(config['record_policy'])
        integer(config['max_simulation_ns'], 'max_simulation_ns', 1)
        fields(config['inputs'], {'service', 'layer', 'system', 'network'}, label='inputs')
        config['inputs'] = {name: str(relative(base, value, kind='directory' if name == 'network' else 'file'))
                            for name, value in config['inputs'].items()}
        service = read(config['inputs']['service'])
        # The first public workflow exposes inline configured full-model inputs;
        # legacy entrypoints retain their existing capabilities and path rules.
        require(service.get('schema_version') in ('moe-ub-configured-service/4','moe-ub-configured-service/5'),
                'inputs.service: public run requires full-model service/4 or finite-segment service/5 with inline model/routes')
        if 'input_evidence' in config:
            evidence_path=relative(base,config['input_evidence']);evidence=read(evidence_path)
            require(evidence['schema_version']=='moe-ub-segment-preparation/1' and evidence['passed'] and
                    service.get('execution',{}).get('segment')==evidence['segment'], 'Prepared source scope differs from run')
            for key,name in (('service','service.json'),('layer','layers.json')):
                require(file_sha256(Path(config['inputs'][key]))==evidence['artifacts'][name]['sha256'],
                        'Prepared source input changed: '+key)
            config['input_evidence']=str(evidence_path)
    if 'network' in config:
        config['network'] = str(relative(base, config['network'], kind='directory'))
    if 'cache_directory' in config:
        config['cache_directory'] = str((base/config['cache_directory']).resolve())
        config['host']['artifact_roots'].append(config['cache_directory'])
    return config


def execute(args):
    config = configuration(args.config, args.command, getattr(args, 'backend', None))
    output = args.output.resolve()
    require(not output.exists(), f'Output already exists: {output}; choose a new experiment name')
    if args.command in ('sample', 'replay'):
        from .research_slice import inspect
        if args.command=='sample':
            from .observation_recording import sample_observation,sampling_plan
            source=read(args.slice/'slice.json')
            sampling_plan(source['observation'],sample_observation(source['observation'],config),
                          source['ranks'],source['capture_end_ns'])
        inspect(args.slice, verify=True)
        config['slice'] = str(args.slice.resolve())
    output.mkdir(parents=True)
    (output/'inputs').mkdir(); (output/'logs').mkdir()
    save(output/'inputs/effective.json', config)
    manifest = dict(schema_version='moe-ub-user-run/1', action=args.command, normal_exit=False,
                    started_utc=timestamp(), source_config=str(args.config.resolve()),
                    calibration_status='unvalidated_reference', observation_access_boundary='observed_only',
                    software=versions())
    if args.command in ('run', 'replay'):
        manifest['backend_binary'] = dict(path=config['backend'], sha256=file_sha256(Path(config['backend'])))
    save(output/'manifest.json', manifest)
    from .host_probe import probe
    phases = [args.command, 'export'] if args.command in ('run', 'replay') else [args.command]
    passed = True; reports = []
    for phase in phases:
        host = dict(config.get('export_host', config['host']) if phase == 'export' else config['host'])
        host['artifact_roots'] = [*host['artifact_roots'], output, *([config['cache_directory']] if 'cache_directory' in config else [])]
        result = probe([sys.executable, '-m', 'moe_ub_cosim.user_workflow', phase, str(output)],
                       output/'logs'/('resources-'+phase), **host)
        reports.append(dict(phase=phase, status=result['status'], path='logs/resources-'+phase+'/probe.json'))
        passed = result['status'] == 'completed' and not result['remaining_live_group_pids']
        if not passed:
            break
    manifest = read(output/'manifest.json')
    passed = passed and manifest.get('completed_phases') == phases
    manifest.update(normal_exit=passed, host_reports=reports, finished_utc=timestamp())
    if passed:
        manifest['artifacts'] = inventory(output, exclude={'manifest.json'})
    save(output/'manifest.json', manifest)
    detail = ': '+manifest['error'] if manifest.get('error') else ''
    require(passed, f'{args.command} failed ({result["status"]}){detail}; see {output/"logs"/("resources-"+phase)}/stderr.log and probe.json')
    return dict(passed=True, output=str(output), simulation_end_ns=manifest.get('simulation_end_ns'),
                coverage=manifest.get('coverage'),
                observed=str(output/'observed'), resources=reports)


def service_run(output, config):
    from .observation import backend_observation, validate_observation
    from .service import run
    inputs = output/'inputs'
    for name, source in config['inputs'].items():
        if name == 'network':
            shutil.copytree(source, inputs/name)
        else:
            shutil.copyfile(source, inputs/(name+'.json'))
    service, layers = read(inputs/'service.json'), read(inputs/'layer.json')
    ranks = sorted(r for group in layers['groups'] for r in group['layers'][0]['rank_ids'])
    validate_observation(config['observation'], ranks, config['max_simulation_ns'])
    save(inputs/'observation.json', config['observation'])
    save(inputs/'backend-observation.json', backend_observation(config['observation'], ranks, config['max_simulation_ns']))
    save(inputs/'record-policy.json', config['record_policy'])
    if 'input_evidence' in config:
        shutil.copyfile(config['input_evidence'],inputs/'preparation.json')
    # Importing Scheduler is intentionally confined to the run worker.
    sys.path.insert(0, str(ROOT/'third_party/LLMServingSim'))
    raw = output/'raw'
    run(SimpleNamespace(input=inputs/'service.json', layer=inputs/'layer.json', backend=Path(config['backend']),
        system=inputs/'system.json', network=inputs/'network', observation=inputs/'backend-observation.json',
        record_storage=inputs/'record-policy.json', graph_storage=None, output_dir=raw,
        prepared_input_evidence=inputs/'preparation.json' if 'input_evidence' in config else None,
        max_simulation_ns=config['max_simulation_ns'], wall_timeout_seconds=config['backend_timeout_seconds']))
    report = read(raw/'execution/result.json')
    from .observation import execution_coverage
    return dict(simulation_end_ns=report['simulation_end_ns'], run_id=service['run_id'],
                coverage=execution_coverage(config['observation'],ranks,config['max_simulation_ns'],report['simulation_end_ns']),
                random=report['random'], raw='raw',
                scope='configured_finite_moe_segment' if service['schema_version']=='moe-ub-configured-service/5' else 'configured_service',
                source=read(raw/'manifest.json'))


def export(raw, observation, ranks, max_ns, output, policy):
    from .bounded_export import export_bounded
    staging = output/'export-in-progress'
    result = export_bounded(raw, observation, ranks, max_ns, staging, policy)
    for name in ('observed', 'truth', 'metrics'):
        (staging/name).rename(output/name)
    staging.rmdir()
    return result


def worker(action, output):
    output = Path(output); config = read(output/'inputs/effective.json')
    manifest = read(output/'manifest.json')
    try:
        if action == 'export':
            if manifest['action'] == 'run':
                observation, max_ns = config['observation'], config['max_simulation_ns']
            else:
                source = read(Path(config['slice'])/'slice.json')
                observation, max_ns = config.get('observation',source['observation']), config.get('max_simulation_ns',source['capture_end_ns'])
            ranks = sorted(r for g in read(output/'raw/deployment.json')['groups'] for r in g['rank_ids'])
            result = export(output/'raw', observation, ranks, max_ns, output, config['export_policy'])
            value = dict(validation=result['passed'])
        elif action == 'run':
            value = service_run(output, config)
        else:
            from .research_slice import sample, replay
            value = (sample if action == 'sample' else replay)(output, config)
        manifest.update(value)
        manifest.setdefault('completed_phases', []).append(action)
    except BaseException as error:
        manifest.update(normal_exit=False, error=str(error), error_type=type(error).__name__)
        raise
    finally:
        save(output/'manifest.json', manifest)


def validate(run=None, observed=None, allow_partial_observation=False):
    from .research_storage import validate_observed_storage
    if observed is not None:
        # This code path only opens files rooted in observed. It never follows
        # a run manifest or reads a neighboring truth directory.
        return dict(passed=True, observed=validate_observed_storage(observed))
    run = Path(run).resolve(); manifest = read(run/'manifest.json')
    require(manifest.get('schema_version') == 'moe-ub-user-run/1' and manifest.get('normal_exit'),
            'Run is incomplete or has an unsupported manifest')
    verify_inventory(run, manifest['artifacts'])
    result = read(run/'metrics/validation.json')
    require(result.get('passed') is True, 'Export validation did not pass')
    coverage=manifest.get('coverage')
    if coverage is not None:
        require(coverage['execution_complete'],'Execution is incomplete')
        require(coverage['observation_complete'] or allow_partial_observation,
                'Observation does not cover complete execution; rerun with expanded observation, or explicitly validate partial observation')
    return dict(passed=True, scope='sealed_artifact_integrity_and_recorded_conservation',
                coverage=coverage,
                artifacts=len(manifest['artifacts']), observed=validate_observed_storage(run/'observed'))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Internal budgeted worker; use moe-ub for user operations')
    p.add_argument('action', choices=('run', 'sample', 'replay', 'export')); p.add_argument('output', type=Path)
    a = p.parse_args(); worker(a.action, a.output)
