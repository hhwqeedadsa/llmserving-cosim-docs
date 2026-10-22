"""Archive closed evidence with a file manifest; fail rather than expand budgets."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[3]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-root', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--index', required=True, type=Path)
    args = p.parse_args()
    source, archive = args.run_root.resolve(), args.output.resolve()
    assert not archive.is_relative_to(source) and not archive.exists() and not args.index.exists()
    manifest = source / 'archive-files.json'
    assert not manifest.exists()
    files = sorted(q for q in source.rglob('*') if q.is_file())
    assert all(not q.is_symlink() for q in files), 'Resolve evidence ownership before archiving symlinks'
    study = sum(q.stat().st_size for q in files if q.is_relative_to(source / 'research'))
    total = sum(q.stat().st_size for q in files)
    assert study <= 256 * 1024**2 and total - study <= 256 * 1024**2
    entries = {str(q.relative_to(source)): dict(bytes=q.stat().st_size, sha256=digest(q)) for q in files}
    save(manifest, dict(files=entries, count=len(entries), bytes=total,
                        scope='all regular closed evidence files; manifest self-checksum is in archive index'))
    entries['archive-files.json'] = dict(bytes=manifest.stat().st_size, sha256=digest(manifest))
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'w:gz', compresslevel=9) as tar:
        for name in sorted(entries):
            tar.add(source / name, arcname=name, recursive=False)
    assert archive.stat().st_size <= 32 * 1024**2, 'Preserve oversized artifact; no automatic budget expansion'
    verified = 0
    with tarfile.open(archive, 'r:gz') as tar:
        assert len(tar.getmembers()) == len(entries)
        for member in tar:
            expected = entries[member.name]
            assert member.isfile() and member.size == expected['bytes']
            h = hashlib.sha256()
            with tar.extractfile(member) as f:
                for block in iter(lambda: f.read(1024 * 1024), b''):
                    h.update(block)
            assert h.hexdigest() == expected['sha256'], member.name
            verified += 1
    save(args.index, dict(passed=True, archive=str(archive.relative_to(ROOT)), bytes=archive.stat().st_size,
                          sha256=digest(archive), verified_file_count=verified,
                          manifest_sha256=entries['archive-files.json']['sha256'],
                          study_bytes=study, other_evidence_bytes=total - study,
                          source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                          argv=[sys.executable, *sys.argv], cwd=str(ROOT),
                          raw_evidence_root=str(source), raw_evidence_preserved=True,
                          scope='committed implementation/study snapshot plus success/failure evidence; closure documents committed separately'))
    print(json.dumps(dict(passed=True, files=verified, archive_bytes=archive.stat().st_size)))


if __name__ == '__main__':
    main()
