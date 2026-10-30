"""Finalize source, outcome and bounded-resource receipts without network reruns."""
import argparse
import json
from pathlib import Path
import shutil

from study import NEW, checked, load, sha


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--delivery', type=Path, required=True)
    a = p.parse_args()
    stage, out = a.stage.resolve(), a.delivery.resolve()
    f = checked(stage/'study')
    analysis, raw, delivery = (load(out/n) for n in ('analysis.json', 'verification.json', 'delivery_verification.json'))
    assert raw['passed'] and delivery['passed'] and len(delivery['tamper_checks']) == 4
    assert sha(out/'analysis.json') == raw['analysis_sha256']
    for name, h in delivery['delivery_sha256'].items():
        assert sha(out/name) == h
    for name, case in analysis['cases'].items():
        assert sha(Path(case['directory'])/'result.json') == case['result_sha256']
    for name in NEW:
        assert load(stage/'study'/(name+'-command.json'))['exit_code'] == 0
    for name in ('normal', 'local-w60'):
        assert load(out/(name+'-compatible.json'))['passed']
    probes = {p.parent.name: load(p) for p in (stage/'monitors').glob('*/probe.json')}
    assert probes and all(p['status'] == 'completed' and p['child_exit_code'] == 0 for p in probes.values())
    seconds = sum(p['wall_seconds'] for p in probes.values())
    peak = max(p['peak_sampled_artifact_bytes'] for p in probes.values())
    assert seconds < 1800 and peak < 2*2**30 and shutil.disk_usage(stage).free > 8*2**30
    assert 'PASS: checked 13 dependency checkouts' in (stage/'dependency-check.txt').read_text()
    with (out/'receipt.json').open('x') as s:
        json.dump(dict(passed=True, method_commit=f['platform_commit'], platform_behavior='43a5dc08b8',
            frozen_sha256=sha(stage/'study/frozen.json'), sources_sha256=f['source_sha256'],
            compiled_sources_sha256=f['compiled_sources_sha256'], backend_sha256=f['backend_sha256'],
            libraries_sha256=f['libraries_sha256'], new_Clos128_networks=4, reused_conditions=5,
            successful_network_reruns=0, monitored_stages=len(probes), monitored_seconds=seconds,
            peak_group_rss_bytes=max(p['peak_sampled_group_rss_bytes'] for p in probes.values()),
            peak_artifact_bytes=peak, budget=f['budget'], dependency_checks=13,
            physical_hops_checked=sum(x['physical_hops'] for x in raw['per_case'].values()),
            source_ACK_witnesses=sum(x['messages'] for x in raw['per_case'].values()),
            tamper_checks=delivery['tamper_checks'], new_builds=0, installs=0, downloads=0, GPU_execution=False,
            browser_rendering_verified=False, figures_visually_inspected=True,
            delivery_sha256={p.name: sha(p) for p in out.iterdir() if p.is_file()}), s, indent=2)
        s.write('\n')
    print(json.dumps(dict(passed=True, monitored_seconds=seconds, peak_artifact_bytes=peak)))


if __name__ == '__main__':
    main()
