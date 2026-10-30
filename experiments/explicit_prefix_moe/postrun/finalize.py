"""Finalize immutable source/results proof; compute file hashes before opening receipt.

Retains the frozen original closeout and records its rejected self-hash failure.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from study import NEW, checked, load, sha


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--delivery', type=Path, required=True)
    a = p.parse_args()
    stage, out = a.stage.resolve(), a.delivery.resolve()
    root = Path(__file__).resolve().parents[3]
    f = checked(stage/'study')
    analysis, raw, delivery = (load(out/n) for n in ('analysis.json', 'verification.json', 'delivery_verification.json'))
    assert raw['passed'] and delivery['passed'] and len(delivery['tamper_checks']) == 4
    assert sha(out/'analysis.json') == raw['analysis_sha256']
    assert 'delivery_verification.json' not in delivery['delivery_sha256']
    for name, h in delivery['delivery_sha256'].items():
        assert sha(out/name) == h
    assert sha(root/'experiments/explicit_prefix_moe/verify.py') == raw['verifier_sha256']
    assert sha(root/'experiments/explicit_prefix_moe/delivery_check.py') == delivery['verifier_sha256']
    for name, case in analysis['cases'].items():
        assert sha(Path(case['directory'])/'result.json') == case['result_sha256']
    for name in NEW:
        assert load(stage/'study'/(name+'-command.json'))['exit_code'] == 0
    for name in ('normal', 'local-w60'):
        assert load(out/(name+'-compatible.json'))['passed']
    diagnosis = load(out/'path_diagnosis.json')
    assert diagnosis['source_sha256'] == sha(Path(__file__).with_name('diagnose_paths.py'))
    for path, h in diagnosis['timer_sources_sha256'].items():
        assert sha(root/path) == h
    for path, h in load(out/'postrun_sources.json')['sources_sha256'].items():
        assert sha(root/path) == h
    assert load(out/'verification_revision.json')['wrapper_sha256'] == sha(Path(__file__).with_name('verify_delivery.py'))
    probes = {p.parent.name: load(p) for p in (stage/'monitors').glob('*/probe.json')}
    assert probes and all(name in probes for name in NEW)
    assert all(p['status'] == 'completed' and p['child_exit_code'] == 0 for p in probes.values())
    seconds = sum(p['wall_seconds'] for p in probes.values())
    peak = max(p['peak_sampled_artifact_bytes'] for p in probes.values())
    assert seconds < 1800 and peak < 2*2**30 and shutil.disk_usage(stage).free > 8*2**30
    assert 'PASS: checked 13 dependency checkouts' in (stage/'dependency-check-final.txt').read_text()
    assert not (out/'receipt.json').exists()
    # Snapshot first: a receipt cannot include its own unfinished byte hash.
    identity = {p.name: sha(p) for p in out.iterdir() if p.is_file()}
    value = dict(passed=True, method_commit=f['platform_commit'], platform_behavior='43a5dc08b8',
        frozen_sha256=sha(stage/'study/frozen.json'), execution_sources_sha256=f['source_sha256'],
        postrun_sources_sha256={str(p.relative_to(root)): sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
        compiled_sources_sha256=f['compiled_sources_sha256'], backend_sha256=f['backend_sha256'],
        libraries_sha256=f['libraries_sha256'], new_Clos128_networks=4, reused_conditions=5,
        successful_network_reruns=0, monitored_stages=len(probes), monitored_seconds=seconds,
        peak_group_rss_bytes=max(p['peak_sampled_group_rss_bytes'] for p in probes.values()),
        peak_artifact_bytes=peak, budget=f['budget'], dependency_checks=13,
        physical_hops_checked=sum(x['physical_hops'] for x in raw['per_case'].values()),
        source_ACK_witnesses=sum(x['messages'] for x in raw['per_case'].values()),
        unique_RX_increments_checked=delivery['unique_RX_increments_checked'], tamper_checks=delivery['tamper_checks'],
        retained_failures=[load(stage/'launch-failure.json'), load(out/'verification_revision.json')['archived_failure']],
        finalization_revision='frozen closeout rejected v1 self hash; same input/result checks; snapshot before receipt creation',
        new_builds=0, installs=0, downloads=0, GPU_execution=False,
        browser_rendering_verified=False, figures_visually_inspected=True, delivery_sha256=identity)
    with (out/'receipt.json').open('x') as s:
        json.dump(value, s, indent=2)
        s.write('\n')
    proof = load(out/'receipt.json')
    assert 'receipt.json' not in proof['delivery_sha256']
    for name, h in proof['delivery_sha256'].items():
        assert sha(out/name) == h
    print(json.dumps(dict(passed=True, monitored_stages=len(probes), monitored_seconds=seconds,
        peak_artifact_bytes=peak, receipt_sha256=sha(out/'receipt.json'))))


if __name__ == '__main__':
    main()
