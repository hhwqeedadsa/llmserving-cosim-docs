"""Serial public platform runs; explicit shared budgets, no failed-run retry."""
from pathlib import Path
import os
import shutil
import sys

from .common import ARTIFACT_ROOTS, bounded, command, digest, host, read, require, save, sources
from .inputs import BATCHES, SEEDS, verified_case

RESERVE_PER_CASE = 768*2**20


def storage(roots):
    seen = set()
    apparent = allocated = 0
    for root in roots:
        for directory, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(directory)/d).is_symlink()]
            for path in [Path(directory), *(Path(directory)/n for n in names)]:
                info = path.lstat()
                key = info.st_dev, info.st_ino
                if path.is_symlink() or key in seen:
                    continue
                seen.add(key)
                allocated += info.st_blocks*512
                if path.is_file():
                    apparent += info.st_size
    return dict(apparent_bytes=apparent, allocated_bytes=allocated, charged_bytes=max(apparent,allocated))


def preflight(batch, output):
    batch, output = Path(batch).resolve(), Path(output).resolve()
    require(not output.exists(), 'Preflight output exists')
    cases = read(batch/'batch.json')['cases']
    for c in cases:
        verified_case(batch,c['case_id'])
    remaining = sum(not (batch/'runs'/c['case_id']/'complete.json').exists() for c in cases)
    usage = storage([*ARTIFACT_ROOTS,batch,output.parent])
    free = shutil.disk_usage(batch).free
    reserve = remaining*RESERVE_PER_CASE
    result = dict(schema_version='scaleup-preflight/1', simulation_executed=False,
        cumulative_storage=usage, remaining_cases=remaining, reserve_per_case_bytes=RESERVE_PER_CASE,
        additional_reserve_bytes=reserve, artifact_limit_bytes=48*2**30, min_free_bytes=20*2**30,
        free_filesystem_bytes=free, ready=usage['charged_bytes']+reserve <= 48*2**30 and free-reserve >= 20*2**30,
        reserve_basis='Per case: 128 MiB raw records, 128 MiB export, 512 MiB graph/config/overview/log allowance; runtime cumulative cap remains enforced')
    save(output,result)
    require(result['ready'], f'Budget preflight failed: {output}; no limits changed')
    return result


def overview(run, output, expected_service=None):
    run, output = Path(run).resolve(), Path(output).resolve()
    require(not output.exists(), f'Overview exists: {output}')
    output.mkdir(parents=True)
    extra = [Path(expected_service).resolve()] if expected_service else []
    bounded([sys.executable,'-m','experiments.scaleup_observability.overview',run,output,*extra],
            output/'commands','overview',output)
    return read(output/'summary.json')


def plot(batch, output):
    batch, output = Path(batch).resolve(), Path(output).resolve()
    require(not output.exists(), 'Figure directory exists')
    output.mkdir(parents=True)
    bounded([sys.executable,'-m','experiments.scaleup_observability.figures',batch,output],
            output/'commands','plot',output)
    return dict(passed=True,output=str(output))


def run_case(batch, case_id, backend):
    batch, backend = Path(batch).resolve(), Path(backend).resolve()
    case, config = verified_case(batch,case_id)
    require(backend.is_file(), f'Missing backend: {backend}')
    if 'baseline' in case:
        from .slots import verify_reference, verify_pair_inputs
        _, reference_folder, done, _ = verify_reference(case['baseline'])
        verify_pair_inputs(reference_folder,config)
        require(done['backend_sha256']==digest(backend), 'Paired baseline backend differs')
    output = batch/'runs'/case_id
    require(not output.exists(), f'Run exists: {output}; failed runs are not retried automatically')
    output.mkdir(parents=True)
    save(output/'case.json',case)
    preflight(batch,output/'preflight.json')
    save(output/'implementation.json',sources())
    # Resolve performs model, capacity and backend checks before network execution.
    bounded([sys.executable,'-m','moe_ub_cosim','resolve','--config',config/'scenario.yaml',
             '--backend',backend,'--output',output/'resolved'],output/'commands','01-resolve',batch)
    run = dict(schema_version='moe-ub-run/1', host=host(batch),
        inputs={k:str(output/'resolved'/('network' if k=='network' else k+'.json')) for k in ('service','layer','system','network')},
        backend=str(backend), observation=str(config/'observation.json'),
        record_policy=str(config/'record-policy.json'), export_policy=str(config/'export-policy.json'),
        max_simulation_ns=1_000_000, backend_timeout_seconds=1700)
    save(output/'run.json',run)
    command([sys.executable,'-m','moe_ub_cosim','run','--config',output/'run.json','--output',output/'run'],
            output/'commands','02-run')
    bounded([sys.executable,'-m','moe_ub_cosim','validate','--run',output/'run'],output/'commands','03-validate',batch)
    expected_service = None
    if 'port_service' in case:
        require(read(batch/'batch.json').get('study')=='port-service','Unexpected service case')
        expected_service = output/'expected-service.json'
        save(expected_service,dict(schema_version='moe-ub-port-service/1',execution_end_ns=1_000_000,
                                  intervals=case['port_service']))
    summary = overview(output/'run',output/'overview',expected_service)
    require(summary['request_count'] == case['request_count'], 'Unexpected executed workload')
    result = dict(case_id=case_id, passed=True, run_manifest_sha256=digest(output/'run/manifest.json'),
        overview_sha256=digest(output/'overview/summary.json'), backend_sha256=digest(backend),
        case_sha256=digest(output/'case.json'))
    save(output/'complete.json',result)
    return result


def verify_complete(batch, case):
    verified_case(batch,case['case_id'])
    root = Path(batch)/'runs'/case['case_id']
    done = read(root/'complete.json')
    require(done['passed'] and done['run_manifest_sha256'] == digest(root/'run/manifest.json') and
            done['overview_sha256'] == digest(root/'overview/summary.json') and
            done['case_sha256'] == digest(root/'case.json') and read(root/'case.json') == case,
            'Completed case evidence changed')
    summary = read(root/'overview/summary.json')
    for name,sha in summary['tables'].items():
        require(Path(name).name == name and digest(root/'overview'/name) == sha, 'Overview table changed')
    return done,summary


def run_batch(batch, backend):
    batch = Path(batch).resolve()
    cases = read(batch/'batch.json')['cases']
    for case in cases:
        name = case['case_id']
        root = batch/'runs'/name
        if root.exists():
            done,_ = verify_complete(batch,case)
            require(done['backend_sha256'] == digest(backend), 'Backend changed within batch')
            print(f'{name}: verified complete; reused',flush=True)
        else:
            print(f'{name}: starting',flush=True)
            run_case(batch,name,backend)
            print(f'{name}: complete',flush=True)
    return dict(passed=True,cases=len(cases))


def report(batch, output):
    batch, output = Path(batch).resolve(), Path(output).resolve()
    require(not output.exists(), 'Report exists')
    rows = []
    platforms = []
    backend = set()
    versions = set()
    cases = read(batch/'batch.json')['cases']
    expected = {(f's{s}-b{b}',s,b,8*b) for s in SEEDS for b in BATCHES}
    require(len(cases) == 9 and
            {(c['case_id'],c['input_set'],c['requests_per_rank'],c['request_count']) for c in cases} == expected,
            'Need nine distinct declared workload combinations')
    for case in cases:
        done,summary = verify_complete(batch,case)
        backend.add(done['backend_sha256'])
        versions.add(summary['analysis_version'])
        m = read(batch/'runs'/case['case_id']/'run/manifest.json')
        platforms.append(m['software']['source_hashes'])
        rows.append(dict(case=case, overview=summary, completion=done))
    require(len(rows)==9 and len(backend)==len(versions)==1 and all(p==platforms[0] for p in platforms),
            'Need nine cases under consistent implementation')
    result = dict(schema_version='scaleup-normal-report/1', cases=rows,
        independent_input_sets=3, nested_batch_sizes_per_rank=[2,4,8],
        note='Nine workload combinations, not nine independent random repetitions; no causal labels assigned')
    save(output,result)
    return dict(passed=True,output=str(output),cases=len(rows))
