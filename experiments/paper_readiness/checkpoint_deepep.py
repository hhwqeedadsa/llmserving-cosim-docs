"""Save and hash-verify a bounded local offline-audit checkpoint; no publication."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / 'docs/review_artifacts/deepep_semantics_20261022'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-dir', required=True, type=Path)
    p.add_argument('--graphs', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    a.output = a.output.resolve()
    a.output.relative_to(ROOT)  # Checkpoint indexes use repository-relative paths.
    assert not a.output.exists()
    audit = json.loads((REVIEW / 'audit.json').read_text())
    files = {}

    def add(path, name=None):
        path = path.resolve()
        name = name or str(path.relative_to(ROOT))
        assert path.is_file() and not Path(name).is_absolute() and '..' not in Path(name).parts
        assert name not in files
        files[name] = path

    for s in audit['sources']:
        add(ROOT / s['local_snapshot'])
    for name in ('tree.json', 'tree-source.json', 'file-sources.json'):
        add(a.source_dir / name)
    ref = audit['accounting']['manifest']
    add(ROOT / ref['path'])
    manifest = json.loads((ROOT / ref['path']).read_text())
    for item in manifest['files']:
        add(ROOT / item['file']['path'])
    add(a.graphs / 'input.json', 'saved_run/input.json')
    for wave in audit['saved_graph_comparison']['waves']:
        add(a.graphs / wave['path'], 'saved_run/' + wave['path'])
    for path in REVIEW.iterdir():
        if path.is_file() and path.name != 'archive.json':
            add(path)
    for path in (ROOT / 'experiments/paper_readiness').iterdir():
        if path.suffix in ('.py', '.md'):
            add(path)
    for name in ('docs/reviews/DeepEP低延迟通信语义与建模修正_20261022.md',
                 'docs/model_validation.md', 'docs/tasks/current.md',
                 'docs/review_artifacts/paper_readiness_20261022/claims.json'):
        add(ROOT / name)
    total = sum(p.stat().st_size for p in files.values())
    assert total <= 32*2**20
    assert shutil.disk_usage(a.output.parent).free >= 8*2**30 + 32*2**20
    a.output.mkdir()
    dest = a.output / 'checkpoint.tar.gz'
    identities = {name: dict(bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                  for name, p in sorted(files.items())}
    with tarfile.open(dest, 'w:gz') as archive:
        for name, path in sorted(files.items()):
            archive.add(path, arcname=name, recursive=False)
    with tarfile.open(dest, 'r:gz') as archive:
        assert set(archive.getnames()) == set(identities)
        for item in archive.getmembers():
            assert item.isfile()
            raw = archive.extractfile(item).read()
            assert len(raw) == identities[item.name]['bytes']
            assert hashlib.sha256(raw).hexdigest() == identities[item.name]['sha256']
    result = dict(path=str(dest.relative_to(ROOT)), bytes=dest.stat().st_size,
                  sha256=hashlib.sha256(dest.read_bytes()).hexdigest(), uncompressed_bytes=total,
                  files=identities, verified_files=len(files),
                  scope='local static-audit recovery; not full simulator, hardware calibration or paper completion',
                  graphs_restore_subdirectory='saved_run',
                  data_restore='Restore data/ paths relative to repository root; do not overwrite unknown files')
    (a.output / 'archive.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'files'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
