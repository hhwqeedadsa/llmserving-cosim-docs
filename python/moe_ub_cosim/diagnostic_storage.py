"""Lossless storage of closed backend diagnostic logs, outside simulation."""
import gzip
import hashlib
from pathlib import Path
import shutil
import stat

from .plan import require
from .plan_io import file_sha256
from .user_io import save


def compress_closed_logs(execution):
    """Call only after the backend has exited; never touch structured records.

    Each source survives until its gzip stream has been fully read back and
    its original size and digest have been verified. Interrupted operations
    retain a journal and fail the public run rather than silently succeeding.
    """
    execution = Path(execution)
    directory = execution/'log'
    require(not directory.is_symlink(), 'Diagnostic log directory must not be a symlink')
    journal = execution/'diagnostic-storage.json'
    require(not journal.exists(), 'Diagnostic storage already has a journal')
    paths = sorted(directory.iterdir()) if directory.exists() else []
    for path in paths:
        require(stat.S_ISREG(path.lstat().st_mode) and path.suffix == '.log',
                'Expected regular diagnostic log: '+str(path))
        require(not path.with_name(path.name+'.gz').exists(), 'Compressed diagnostic already exists')
    report = dict(schema_version='moe-ub-diagnostic-storage/1', storage='gzip',
                  completed=False, files={})
    save(journal, report)
    for path in paths:
        target = path.with_name(path.name+'.gz')
        before = dict(bytes=path.stat().st_size, sha256=file_sha256(path))
        with target.open('xb') as dest:
            with gzip.GzipFile(filename='', mode='wb', fileobj=dest, mtime=0) as zipped:
                with path.open('rb') as source:
                    shutil.copyfileobj(source, zipped, length=2**20)
        digest = hashlib.sha256(); length = 0
        with gzip.open(target, 'rb') as source:
            while block := source.read(2**20):
                length += len(block); digest.update(block)
        require(dict(bytes=length, sha256=digest.hexdigest()) == before,
                'Compressed diagnostic verification failed; original retained')
        require(dict(bytes=path.stat().st_size, sha256=file_sha256(path)) == before,
                'Diagnostic changed during compression; original retained')
        name = path.relative_to(execution).as_posix()
        report['files'][name] = dict(source=before,
            compressed=dict(path=target.relative_to(execution).as_posix(),
                            bytes=target.stat().st_size, sha256=file_sha256(target)))
        save(journal, report)
        path.unlink()
    report['completed'] = True
    save(journal, report)
    return report
