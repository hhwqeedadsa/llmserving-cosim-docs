"""Small complete E tooling check without real traces or old run artifacts."""
import argparse
from pathlib import Path
import sys
import yaml

from .analyze import analyze
from .archive import pack, restore
from .workflow import CONFIG, cli, digest, host, read, save


def check(output, backend, control_path=None):
    output.mkdir(parents=True, exist_ok=False)
    case = output/'case'
    case.mkdir()
    config = CONFIG/'check'
    scenario = yaml.safe_load((config/'scenario.yaml').read_text())
    for key in ('service','layer'):
        scenario[key] = str(config/scenario[key])
    if control_path is not None:
        from .controls import apply
        control = read(control_path)
        source, layers = read(scenario['service']), read(scenario['layer'])
        wrapped, selected = apply(dict(service=source), layers['groups'][0]['layers'],
            dict(case_id='synthetic-tooling-check',first_layer=2,layer_count=2),control)
        layers['groups'][0]['layers'] = selected
        save(case/'source-service.json',wrapped['service'])
        save(case/'source-layers.json',layers)
        scenario['service'], scenario['layer'] = str(case/'source-service.json'),str(case/'source-layers.json')
    scenario['system'] = str(CONFIG/'system.json')
    scenario['backend']['reference_file'] = str(CONFIG/'backend-reference.yaml')
    scenario['network']['fabric_file'] = str(config/'fabric.json')
    (case/'scenario.yaml').write_text(yaml.safe_dump(scenario,sort_keys=False))
    cli(case,'resolve','resolve','--config',case/'scenario.yaml','--backend',backend,'--output',case/'resolved')
    save(case/'source-run.json',dict(schema_version='moe-ub-run/1',host=host(extra_roots=[case]),
        inputs=dict(service=str(case/'resolved/service.json'),layer=str(case/'resolved/layer.json'),
            system=str(case/'resolved/system.json'),network=str(case/'resolved/network')),
        observation=str(config/'observation.json'),record_policy=str(CONFIG/'record-policy.json'),
        export_policy=str(CONFIG/'export-policy.json'),max_simulation_ns=1000000,backend=str(backend),backend_timeout_seconds=240))
    save(case/'select.json',dict(schema_version='moe-ub-segment-prepare/1',source_run='source-run.json',
        first_layer=2,layer_count=2,decode_step=0))
    cli(case,'prepare','segment','prepare','--config',case/'select.json','--output',case/'prepared')
    save(case/'case.json',dict(schema_version='issue-screening-case/1',owned_by='scaleup_issue_screening',
        case_id='synthetic-tooling-check',analyzed_layers=[2,3],source_kind='synthetic',backend_sha256=digest(backend)))
    cli(case,'run','run','--config',case/'prepared/run.json','--output',case/'run')
    analyze(case,case/'analysis')
    cli(case,'freeze','segment','freeze','--from-run',case/'run','--output',case/'slice','--storage','hardlink')
    cli(case,'inspect','slice','inspect','--slice',case/'slice','--verify')
    summary=read(case/'analysis/summary.json')
    if len(summary['operations']) != 4 or summary['requests'] != 3:
        raise ValueError('Small analysis failed to cover the declared operations')
    archived = pack(case, output/'case.tar.gz', release=True)
    restored = output/'restored'
    restore(output/'case.tar.gz',restored)
    cli(restored,'restore-validate','validate','--run',restored/'run')
    cli(restored,'restore-inspect','slice','inspect','--slice',restored/'slice','--verify')
    save(output/'summary.json',dict(passed=True,synthetic_only=True,requests=3,operations=4,
        simulation_end_ns=summary['simulation_end_ns'],archive=archived,
        public_run_and_freeze=True,restored_run_and_slice_validated=True,
        control=None if control_path is None else read(control_path)))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--backend',type=Path,required=True)
    p.add_argument('--control',type=Path,help='Optional synthetic-tooling-check one-factor control JSON')
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args()
    if a.worker:
        check(a.output.resolve(),a.backend.resolve(),a.control)
    else:
        from moe_ub_cosim.host_probe import probe
        report=probe([sys.executable,'-m','experiments.scaleup_issue_screening.check_small','--worker',
            '--output',str(a.output.resolve()),'--backend',str(a.backend.resolve())] +
            (['--control',str(a.control.resolve())] if a.control else []),str(a.output.resolve())+'-resources',**host(extra_roots=[a.output]))
        if report['status']!='completed':
            raise SystemExit('Small workflow failed: '+report['status'])


if __name__=='__main__':
    main()
