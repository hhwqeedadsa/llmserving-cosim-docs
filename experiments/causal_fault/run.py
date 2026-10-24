"""Four public network conditions; explicit monitors, frozen inputs, no retry."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def load(p): return json.loads(p.read_text())
def save(p, v): p.write_text(json.dumps(v, indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for k in ('repository', 'stage', 'backend'): p.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args(); repo, s, backend = a.repository.resolve(), a.stage.resolve(), a.backend.resolve()
    assert not (s/'commands.json').exists() and (s/'inputs/observed/index.json').is_file()
    obs = s/'inputs/observed'; idx = load(obs/'index.json')
    subprocess.run(['git', 'diff', '--exit-code', idx['platform_commit'], '--', 'python', 'backend', 'deps.lock.json', '.gitmodules'], cwd=repo, check=True)
    for name, digest in idx['files'].items(): assert sha(obs/name) == digest
    build = load(backend.parent.parent/'build.json'); assert sha(backend) == build['binaries']['stream-probe']
    libs = {str(p): sha(p) for p in sorted((backend.parent.parent/'lib').glob('libns3*.so'))}
    assert len(libs) == 8 and all(build['inputs'][p] == h for p, h in libs.items())
    save(s/'runtime.json', dict(backend=str(backend), sha256=sha(backend), libraries=libs, platform_commit=idx['platform_commit']))
    (s/'planned').mkdir(); (s/'network').mkdir(); commands = []
    cli = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout']
    def run(label, tail, network=False):
        assert sum(c['network'] for c in commands) < 4 or not network
        monitors = [load(p) for p in s.rglob('probe.json')]
        assert all(m['status'] == 'completed' and not m['remaining_live_group_pids'] for m in monitors)
        assert sum(m['wall_seconds'] for m in monitors)+60+10 < 480
        assert sum(p.stat().st_size for p in s.rglob('*') if p.is_file()) < 96*2**20
        assert shutil.disk_usage(s).free > 8*2**30
        cmd = [sys.executable, '-B', '-m', 'moe_ub_cosim.host_probe', '--output-dir', str(s/(label+'-monitor')),
            '--wall-seconds', '60', '--rss-bytes', str(2**30), '--address-bytes', str(2**31),
            '--artifact-bytes', str(96*2**20), '--artifact-root', str(s), '--min-free-bytes', str(8*2**30), '--', *map(str, cli+tail)]
        row = dict(label=label, command=cmd, cwd=str(repo), network=network, status='started'); commands.append(row)
        save(s/'commands.json', commands)
        done = subprocess.run(cmd, cwd=repo, timeout=65)
        row.update(exit_code=done.returncode, status='terminal'); save(s/'commands.json', commands)
        assert done.returncode == 0, label+': stop, no retry'
    run('topology', ['topology', '--config', obs/'topology.json', '--output', s/'topology'])
    for case in idx['cases']:
        name = case['name']; plan = s/'planned'/name
        run(name+'-plan', ['rdma-causal-plan', '--config', obs/case['plan'], '--topology', s/'topology',
                          '--protocol', obs/case['protocol'], '--output', plan])
        run(name+'-network', ['rdma-stream-probe', '--topology', s/'topology', '--config', plan/'work.json',
            '--protocol', obs/case['protocol'], '--backend', backend, '--output', s/'network'/name], True)
        r = load(s/'network'/name/'result.json'); t = r['persistent_transport']; c = r['packet_capture']
        assert r['passed'] and r['drained'] and t['closed'] and t['physical_drained']
        assert c['complete'] and not c['omitted_events'] and c['selection']['nodes'] == []
        assert {str(Path(p).resolve()) for p in r['loaded_libraries'] if '/libns3.' in p} == set(libs)
    for mode in ('gbn', 'selective'):
        assert (s/'planned'/('healthy-'+mode)/'work.json').read_bytes() == (s/'planned'/('failed-'+mode)/'work.json').read_bytes()
    assert sha(backend) == build['binaries']['stream-probe'] and all(sha(Path(p)) == h for p, h in libs.items())
    save(s/'run.json', dict(passed=True, actual_networks=4, phase_closed=True, no_rerun=True, no_platform_change=True))


if __name__ == '__main__': main()
