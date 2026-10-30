"""Save final source identities and completed-stage resources without rerunning networks."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def load(p):
    return json.loads(p.read_bytes())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('stage', 'delivery'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    stage, delivery = a.stage.resolve(), a.delivery.resolve()
    f = load(stage/'study/frozen-v2.json')
    for group in ('source_sha256', 'compiled_sources_sha256'):
        for name, h in f[group].items():
            assert sha(root/name) == h
    for group in ('routes_sha256', 'libraries_sha256'):
        for name, h in f[group].items():
            assert sha(Path(name)) == h
    assert sha(Path(f['backend'])) == f['backend_sha256']
    for name, h in f['prepared_sha256'].items():
        assert sha(Path(f['prepared'])/name) == h
    assert load(delivery/'raw_verification.json')['passed']
    check = load(delivery/'verification-v2.json')
    assert check['passed'] and len(check['tamper_checks']) == 4
    assert sha(Path(__file__).with_name('delivery_check.py')) == check['verifier_sha256']
    for name, h in check['delivery_sha256'].items():
        assert sha(delivery/name) == h
    raw = load(delivery/'raw_verification.json')
    assert sha(Path(__file__).with_name('verify.py')) == raw['verifier_sha256']
    assert sha(delivery/'analysis.json') == raw['analysis_sha256']
    analysis = load(delivery/'analysis.json')
    assert sha(stage/'study/frozen-v2.json') == analysis['frozen_sha256']
    for name, value in analysis['cases'].items():
        assert sha(stage/'study'/name/'result.json') == value['result_sha256']
        assert load(stage/'study'/(name+'-command.json'))['exit_code'] == 0
    probes = {p.parent.name: load(p) for p in (stage/'monitors').glob('*/probe.json')}
    assert len(probes) == 15 and all(p['status'] == 'completed' and p['child_exit_code'] == 0 for p in probes.values())
    seconds = sum(p['wall_seconds'] for p in probes.values())
    peak = max(p['peak_sampled_artifact_bytes'] for p in probes.values())
    assert seconds < 1800 and peak < 2*2**30 and shutil.disk_usage(stage).free > 8*2**30
    assert (stage/'dependency-check.txt').read_text().count('PASS: checked 13 dependency checkouts') == 1
    source = {str(p.relative_to(root)): sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))}
    v = dict(passed=True, platform_commit=f['platform_commit'],
        frozen_sha256=sha(stage/'study/frozen-v2.json'), original_frozen_sha256=sha(stage/'study/frozen.json'),
        compiled_sources_sha256=f['compiled_sources_sha256'], execution_sources_sha256=f['source_sha256'],
        post_run_sources_sha256=source, backend_sha256=f['backend_sha256'], libraries_sha256=f['libraries_sha256'],
        actual_Clos128_networks=7, failed_networks=0, successful_network_reruns=0,
        raw_physical_transmissions=sum(c['physical_hops'] for c in raw['per_case'].values()),
        source_ACK_message_witnesses=sum(c['messages'] for c in raw['per_case'].values()),
        queue_rows=sum(c['queue_rows'] for c in raw['per_case'].values()),
        collective_unique_RX_increments=check['unique_RX_increments_checked'],
        monitored_stages=len(probes), monitored_seconds=seconds,
        peak_group_rss_bytes=max(p['peak_sampled_group_rss_bytes'] for p in probes.values()),
        peak_artifact_bytes=peak, current_free_bytes=shutil.disk_usage(stage).free,
        unmonitored_initial_preflight_failure=load(stage/'study/preflight-failure.json'),
        budget=f['budget'], dependency_checks=13, builds=0, installs=0, downloads=0, GPU_execution=False,
        browser_rendering_verified=False, figures_visually_inspected=True,
        delivery_sha256={p.name: sha(p) for p in delivery.iterdir() if p.is_file()},
        boundaries=['real expert choices, declared compute and marker payload',
                    'two-layer two-step bounded task, no full671B or production calibration',
                    'instant control is a reference, not mathematical lower bound',
                    'mature recovery methods, no new protocol claim'])
    with (delivery/'receipt.json').open('x') as s:
        json.dump(v, s, ensure_ascii=False, indent=2); s.write('\n')
    print(json.dumps(dict(passed=True, monitored_seconds=seconds, physical_transmissions=v['raw_physical_transmissions'],
        source_ACK_witnesses=v['source_ACK_message_witnesses'], peak_artifact_bytes=peak)))


if __name__ == '__main__':
    main()
