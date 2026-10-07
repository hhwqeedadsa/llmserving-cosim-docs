"""Experiment execution through public moe-ub commands, with command evidence."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys

from .common import digest, read, require, save

ROOT = Path(__file__).resolve().parents[2]
SMALL = Path(__file__).resolve().parent / "configs/small"
ARTIFACT_ROOTS = [ROOT / p for p in (
    "build/v04-platform", "runs/v04-platform", "build/m10-resource-efficiency",
    "runs/v03-resource-efficiency", "build/m10-research-slice",
    "runs/v03-research-slice", "runs/mapping_value")]


def host():
    return dict(rss_bytes=8*2**30, address_bytes=12*2**30, artifact_bytes=32*2**30,
                wall_seconds=1800, min_free_bytes=20*2**30,
                artifact_roots=[str(p) for p in ARTIFACT_ROOTS])


def command(arguments, log_dir, label):
    """Record actual argv/cwd/exit/logs; no shell and no automatic retry."""
    log_dir = Path(log_dir); log_dir.mkdir(parents=True, exist_ok=True)
    record = dict(argv=[str(a) for a in arguments], cwd=str(ROOT),
                  started_utc=datetime.now(timezone.utc).isoformat(),
                  stdout=f"{label}.stdout.log", stderr=f"{label}.stderr.log")
    save(log_dir / f"{label}.command.json", record)
    with (log_dir/record['stdout']).open('w') as out, (log_dir/record['stderr']).open('w') as err:
        result = subprocess.run(record['argv'], cwd=ROOT, stdout=out, stderr=err, check=False)
    record.update(exit_code=result.returncode, finished_utc=datetime.now(timezone.utc).isoformat())
    save(log_dir / f"{label}.command.json", record)
    require(result.returncode == 0, f"{label} exited {result.returncode}; see {log_dir/record['stderr']}")


def platform(arguments, output, label):
    command([sys.executable, '-m', 'moe_ub_cosim', *map(str, arguments)], output/'commands', label)


def small_baseline(output, backend):
    output = Path(output).resolve(); backend = Path(backend).resolve()
    require(not output.exists(), f"Output exists: {output}")
    require(backend.is_file(), f"Missing backend: {backend}")
    output.mkdir(parents=True)
    # Independent versioned inputs are copied; no runtime dependency on a fixture.
    shutil.copytree(SMALL, output/'configuration')
    config_dir = output/'configuration'
    save(output/'source.json', dict(schema_version='mapping-value-source/1',
        platform_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        backend_sha256=digest(backend),
        inputs={p.name:digest(p) for p in sorted(config_dir.iterdir()) if p.is_file()}))
    platform(['resolve', '--config', config_dir/'scenario.yaml', '--backend', backend,
              '--output', output/'resolved'], output, '01-resolve')
    config = dict(schema_version='moe-ub-run/1', host=host(),
        inputs={key:str(output/'resolved'/('network' if key=='network' else key+'.json'))
                for key in ('service','layer','system','network')},
        observation=str(config_dir/'observation.json'), record_policy=str(config_dir/'record-policy.json'),
        export_policy=str(config_dir/'export-policy.json'), max_simulation_ns=1_000_000,
        backend=str(backend), backend_timeout_seconds=1700)
    save(output/'run.json', config)
    platform(['run', '--config', output/'run.json', '--output', output/'r000'], output, '02-run')
    platform(['validate', '--run', output/'r000'], output, '03-validate')
    platform(['slice', 'create', '--from-run', output/'r000', '--output', output/'slice',
              '--storage', 'hardlink'], output, '04-slice')
    return dict(passed=True, output=str(output), run='r000', slice='slice')
