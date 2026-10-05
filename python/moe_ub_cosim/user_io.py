"""Shared public command contracts; no frontend or native backend imports."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from .plan import integer, require
from .plan_io import file_sha256

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    path = Path(path)
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f'{path}: cannot read JSON: {error}') from error


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def fields(value, required, optional=(), *, label='config'):
    require(isinstance(value, dict), f'{label}: expected an object')
    missing, unknown = set(required)-set(value), set(value)-set(required)-set(optional)
    require(not missing and not unknown,
            f'{label}: missing fields {sorted(missing)}, unknown fields {sorted(unknown)}')


def relative(base, value, *, kind='file'):
    require(isinstance(value, str) and value, 'Path must be a nonempty string')
    path = (Path(base) / value).resolve()
    require(path.is_dir() if kind == 'directory' else path.is_file(), f'Missing {kind}: {path}')
    return path


def inside(root, name):
    require(isinstance(name, str) and name and not Path(name).is_absolute(), 'Expected relative artifact path')
    root = Path(root).resolve(); path = root / name
    require(path.resolve().is_relative_to(root), f'Artifact escapes directory: {name}')
    return path


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def inventory(directory, *, exclude=()):
    root = Path(directory)
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_file() and str(path.relative_to(root)) not in exclude:
            require(not path.is_symlink(), f'Unportable artifact symlink: {path}')
            result[str(path.relative_to(root))] = dict(bytes=path.stat().st_size, sha256=file_sha256(path))
    return result


def verify_inventory(root, entries):
    for name, expected in entries.items():
        path = inside(root, name)
        require(path.is_file() and not path.is_symlink(), f'Missing regular artifact: {path}')
        require(path.stat().st_size == expected['bytes'] and file_sha256(path) == expected['sha256'],
                f'Changed artifact: {path}')


def versions():
    def git(*args):
        result = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True)
        require(result.returncode == 0, 'Use an editable installation in a complete repository checkout')
        return result.stdout.strip()
    return dict(project_commit=git('rev-parse', 'HEAD'),
                dependencies=git('submodule', 'status', '--recursive').splitlines(),
                source_hashes={str(p.relative_to(ROOT)): file_sha256(p)
                    for folder in ('python/moe_ub_cosim', 'backend')
                    for p in sorted((ROOT/folder).rglob('*'))
                    if p.is_file() and p.suffix in ('.py', '.cc', '.hh', '.cmake')})


HOST_FIELDS = {'rss_bytes', 'address_bytes', 'artifact_bytes', 'wall_seconds', 'min_free_bytes', 'artifact_roots'}


def host_policy(value, base):
    fields(value, HOST_FIELDS, label='host')
    for name in HOST_FIELDS - {'artifact_roots'}:
        integer(value[name], 'host.'+name, 1)
    require(value['address_bytes'] >= value['rss_bytes'], 'host.address_bytes must be >= rss_bytes')
    require(isinstance(value['artifact_roots'], list), 'host.artifact_roots must be a list')
    result = dict(value)
    result['artifact_roots'] = [str((Path(base)/p).resolve()) for p in value['artifact_roots']]
    return result
