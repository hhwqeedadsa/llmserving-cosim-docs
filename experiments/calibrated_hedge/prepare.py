"""Freeze a complete-call hedge deadline using the old normal control only."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]

def load(path):
    return json.loads(path.read_bytes())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')

def files(path):
    return {str(p.relative_to(path)): sha(p) for p in sorted(path.rglob('*')) if p.is_file()}

def normalized(config):
    config = deepcopy(config)
    config['route_replay']['manifest'] = None
    config['whole_call_replication']['calls'][0]['hedge_after_ns'] = None
    return config

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-study', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    previous = args.previous_study.resolve()
    old = load(previous/'frozen.json')
    normal = Path(old['cases']['control-normal']['output'])
    # No fault result is used to derive this threshold.
    assert load(normal/'verification.json')['passed']
    report = load(normal/'whole_call_report.json')
    call = report['calls']['target']
    assert report['decode_steps'] == 2 and call['winner'] == 'primary'
    assert call['attempts']['replica']['submitted_ns'] is None
    duration = call['result_ready_ns']
    deadline = ((duration + 999)//1000)*1000
    assert deadline < 4000000
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    calibration = dict(rule='ceil_complete_normal_target_to_1000ns/1',
        complete_normal_target_ns=duration, hedge_after_ns=deadline,
        normal_samples=1, grid_ns=1000, result_tuned=False,
        independent_holdout=False, production_percentile=False,
        source=str(normal/'whole_call_report.json'),
        source_sha256=sha(normal/'whole_call_report.json'))
    save(out/'calibration.json', calibration)
    source = previous/'source-manifest.json'
    manifest = load(source)
    for entry in manifest['files']:
        path = (ROOT/entry['file']['path']).resolve()
        assert sha(path) == entry['file']['sha256']
    backend = old['backend']
    assert sha(Path(backend['path'])) == backend['sha256']
    for path, digest in old['loaded_ns3_libraries'].items():
        assert sha(Path(path)) == digest
    prepared = out/'input'
    subprocess.run([sys.executable, '-B', str(ROOT/'examples/scaleout_multicall/prepare.py'),
        '--source-manifest', str(source), '--target-files', '4', '--decode-steps', '2',
        '--target-deadline-ns', str(deadline), '--healthy-deadline-ns', '4000000',
        '--control-deadline-ns', '4000000', '--output', str(prepared)], cwd=ROOT, check=True)
    old_config = load(Path(old['prepared'])/'service-control.json')
    new_config = load(prepared/'service-multicall.json')
    assert normalized(old_config) == normalized(new_config)
    assert new_config['whole_call_replication']['calls'][0]['hedge_after_ns'] == deadline
    assert load(prepared/'multicall-manifest.json') == load(Path(old['prepared'])/'multicall-manifest.json')
    for name in ('rdma-multicall-normal.json', 'rdma-multicall-local.json', 'topology/topology.json'):
        assert sha(prepared/name) == sha(Path(old['prepared'])/name)
    inherited = {name: dict(output=spec['output'], sha256=files(Path(spec['output'])))
                 for name, spec in old['cases'].items()}
    cases = {condition: dict(output=str(out/('calibrated-'+condition)),
                config=str(prepared/'service-multicall.json'),
                protocol=str(prepared/('rdma-multicall-'+condition+'.json')),
                topology=str(prepared/'topology')) for condition in ('normal', 'local')}
    frozen = dict(passed=True, network_executed=False, calibration=calibration,
        platform_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        previous_study=str(previous), previous_frozen_sha256=sha(previous/'frozen.json'),
        source_manifest_sha256=sha(source), source_lineage=old['source_lineage'],
        backend=backend, loaded_ns3_libraries=old['loaded_ns3_libraries'],
        method_sha256=sha(Path(__file__).with_name('protocol.md')),
        preparer_sha256=sha(Path(__file__)),
        independent_inspector_sha256=sha(ROOT/'experiments/sequential_externality/analyze.py'),
        prepared=str(prepared), prepared_sha256=files(prepared),
        inherited_cases=inherited, new_cases=cases, new_networks_planned=2,
        simulated_endpoints=128, ep_sizes=[32]*4, decode_steps=2)
    save(out/'frozen.json', frozen)
    print(json.dumps(dict(passed=True, calibration=calibration, new_networks_planned=2)))

if __name__ == '__main__':
    main()
