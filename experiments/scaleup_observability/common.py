"""Small experiment utilities and evidence-preserving process execution."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ARTIFACT_ROOTS = tuple(ROOT / p for p in (
    'build/v04-platform', 'runs/v04-platform', 'build/m10-resource-efficiency',
    'runs/v03-resource-efficiency', 'build/m10-research-slice',
    'runs/v03-research-slice', 'runs/mapping_value', 'runs/scaleup_observability',
    'runs/task-sync-20261008', 'build/m17-send-state', 'runs/m17-send-state'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def sources():
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(HERE.rglob('*'))
            if p.is_file() and p.suffix in ('.py', '.json', '.yaml')}


def host(output):
    return dict(rss_bytes=8*2**30, address_bytes=12*2**30,
                artifact_bytes=48*2**30, wall_seconds=1800, min_free_bytes=20*2**30,
                artifact_roots=[str(p) for p in (*ARTIFACT_ROOTS, Path(output).resolve())])


def command(argv, directory, label):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (label + '.command.json')
    require(not target.exists(), f'Command evidence exists: {target}; no automatic retry')
    record = dict(argv=list(map(str, argv)), cwd=str(ROOT),
                  started_utc=datetime.now(timezone.utc).isoformat(),
                  stdout=label+'.stdout.log', stderr=label+'.stderr.log')
    save(target, record)
    with (directory/record['stdout']).open('w') as out, (directory/record['stderr']).open('w') as err:
        result = subprocess.run(record['argv'], cwd=ROOT, stdout=out, stderr=err, check=False)
    record.update(exit_code=result.returncode, finished_utc=datetime.now(timezone.utc).isoformat())
    save(target, record)
    require(result.returncode == 0, f'{label} failed ({result.returncode}); see {directory/record["stderr"]}')


def bounded(argv, directory, label, artifact_root):
    policy = host(artifact_root)
    args = [sys.executable, '-m', 'moe_ub_cosim.host_probe', '--output-dir',
            Path(directory)/(label+'.resources')]
    for key in ('rss_bytes', 'address_bytes', 'artifact_bytes', 'wall_seconds', 'min_free_bytes'):
        args += ['--'+key.replace('_', '-'), str(policy[key])]
    for root in policy['artifact_roots']:
        args += ['--artifact-root', root]
    command([*args, '--', *argv], directory, label)
