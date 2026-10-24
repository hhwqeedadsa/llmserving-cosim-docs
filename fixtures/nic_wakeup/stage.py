"""Bounded platform-check recorder; no execution on import."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def load(p): return json.loads(Path(p).read_text())
def save(p, x): Path(p).write_text(json.dumps(x, ensure_ascii=False, indent=2)+'\n')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(repo, stage, label, command, network=False, environment=None, cwd=None):
    rows = load(stage/'commands.json') if (stage/'commands.json').exists() else []
    assert label not in {r['label'] for r in rows}
    assert sum(r['network'] for r in rows) < 4 or not network
    assert sum(load(p)['wall_seconds'] for p in stage.rglob('probe.json') if 'restored' not in p.parts)+70 < 900
    command = list(map(str, command))
    cmd = [sys.executable, '-B', '-m', 'moe_ub_cosim.host_probe', '--output-dir', str(stage/(label+'-monitor')),
        '--wall-seconds', '60', '--rss-bytes', str(2**30), '--address-bytes', str(2**31),
        '--artifact-bytes', str(192*2**20), '--artifact-root', str(stage), '--min-free-bytes', str(8*2**30), '--', *command]
    env = dict(os.environ, PYTHONPATH=str(repo/'python')+':'+str(repo), PYTHONDONTWRITEBYTECODE='1')
    if environment: env.update(environment)
    rows.append(dict(label=label, command=cmd, cwd=str(cwd or repo), network=network, status='started')); save(stage/'commands.json', rows)
    r = subprocess.run(cmd, cwd=cwd or repo, env=env, timeout=65)
    rows[-1].update(status='terminal', exit_code=r.returncode); save(stage/'commands.json', rows)
    assert r.returncode == 0, label+': failure preserved; do not retry unchanged'
