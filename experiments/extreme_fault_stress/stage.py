"""Bounded, research-owned command runner; only public CLI subprocesses."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def load(p): return json.loads(Path(p).read_text())
def save(p, d): Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=2) + '\n')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(repo, stage, label, command, network=False, allow_failure=False):
    repo, stage = Path(repo).resolve(), Path(stage).resolve()
    rows = load(stage/'commands.json') if (stage/'commands.json').exists() else []
    assert label not in {x['label'] for x in rows}, 'No reruns'
    assert not network or sum(x.get('native_network_started',False) for x in rows) < 6
    used = sum(load(p)['wall_seconds'] for p in stage.rglob('probe.json'))
    assert used + 70 < 600, 'Cumulative time budget'
    cmd = [sys.executable, '-B', '-m', 'moe_ub_cosim.host_probe', '--output-dir', str(stage/(label+'-monitor')),
           '--wall-seconds', '60', '--rss-bytes', str(2**30), '--address-bytes', str(2**31),
           '--artifact-bytes', str(192*2**20), '--artifact-root', str(stage),
           '--min-free-bytes', str(8*2**30), '--', *map(str, command)]
    rows.append(dict(label=label, command=cmd, cwd=str(repo), network=network, status='started'))
    stage.mkdir(parents=True, exist_ok=True); save(stage/'commands.json', rows)
    env = dict(os.environ, PYTHONPATH=str(repo/'python')+':'+str(repo), PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run(cmd, cwd=repo, env=env, timeout=65)
    report = load(stage/(label+'-monitor/probe.json'))
    rows = load(stage/'commands.json'); row = next(x for x in rows if x['label'] == label)
    row.update(status='terminal', exit_code=result.returncode, probe_status=report['status'])
    if network:
        target=Path(command[command.index('--output')+1])
        row['native_network_started']=(target/'inputs.json').exists()
    save(stage/'commands.json', rows)
    assert not report['remaining_live_group_pids']
    if allow_failure:
        assert report['status'] in ('completed', 'child_failed'), report['status']
    else:
        assert result.returncode == 0, label+': failure preserved; stop'
    return report
