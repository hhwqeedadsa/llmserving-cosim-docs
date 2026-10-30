"""Execute exactly six registered Clos128 functional networks via public CLI."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def load(p):
    return json.loads(p.read_bytes())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('stage', 'old-build', 'build'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    stage = a.stage.resolve()
    network = stage/'network'
    network.mkdir(exist_ok=False)
    new = load(a.build/'backend.json')
    old = load(a.old_build/'backend.json')
    provenance = load(a.build/'provenance.json')
    for name, h in provenance['sources'].items():
        assert sha(ROOT/name) == h, name
    assert new['passed'] and old['passed']
    inp = stage/'input'
    frozen = dict(platform_compiled_sources_sha256=provenance['sources'],
        input_sha256={str(x.relative_to(inp)): sha(x) for x in inp.rglob('*') if x.is_file()},
        native_binaries_sha256={r['stream_probe']: sha(Path(r['stream_probe'])) for r in (old, new)},
        cases=['normal-old', 'normal-new', 'stale-down', 'ordered-local', 'physical-before-report', 'inflight'],
        max_new_networks=6, workload='artificial persistent bytes, no GPU or full model', topology='Clos128')
    (stage/'frozen.json').write_text(json.dumps(frozen, indent=2)+'\n')
    receipts = []
    for name in frozen['cases']:
        protocol = 'normal' if name.startswith('normal') else name
        backend = old if name == 'normal-old' else new
        command = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', 'rdma-stream-probe',
            '--topology', str(inp/'topology'), '--protocol', str(inp/(protocol+'.json')), '--config', str(inp/'work.json'),
            '--backend', backend['stream_probe'], '--output', str(network/name)]
        monitored = [sys.executable, '-B', str(ROOT/'fixtures/temporary_link_restore/monitor.py'),
                     '--root', str(stage), '--stage', 'network-'+name, '--', *command]
        print('Starting '+name, flush=True)
        status = subprocess.run(monitored, cwd=ROOT).returncode
        receipts.append(dict(case=name, command=command, exit_code=status))
        (stage/'network-receipts.json').write_text(json.dumps(receipts, indent=2)+'\n')
        if status:
            raise SystemExit(status)
    print('Six registered networks completed once each', flush=True)


if __name__ == '__main__':
    main()
