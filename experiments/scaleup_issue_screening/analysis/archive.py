"""Lossless E-case archives with inventory verification before releasing files.

Only marked cases under runs/scaleup_issue_screening may be released. Restoring
creates a new directory; archives are not directly executable platform slices.
"""
import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile

from .workflow import ROOT, digest, host, read, save


def safe_name(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or str(p) != name:
        raise ValueError('Unsafe archive name: '+name)
    return name


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Only regular files/directories may be archived: '+str(path))
        result[path.relative_to(root).as_posix()] = dict(bytes=info.st_size, sha256=digest(path))
    return result


def verify(archive, entries, block_size=4096):
    seen = {}
    payload = allocated = links = 0
    directories = {'.'}
    with tarfile.open(archive, 'r|gz') as stream:
        for member in stream:
            name = safe_name(member.name)
            directories.update(str(p) for p in PurePosixPath(name).parents)
            if name in seen or name not in entries:
                raise ValueError('Unexpected or duplicate member: '+name)
            if member.islnk():
                links += 1
                target = safe_name(member.linkname)
                if target not in seen:
                    raise ValueError('Hardlink must refer to a preceding verified regular file')
                actual = seen[target]
            elif member.isfile():
                if member.size != entries[name]['bytes']:
                    raise ValueError('Member size mismatch: '+name)
                with stream.extractfile(member) as data:
                    actual = dict(bytes=member.size, sha256=hashlib.file_digest(data, 'sha256').hexdigest())
                payload += member.size
                allocated += ((member.size+block_size-1)//block_size)*block_size
            else:
                raise ValueError('Archive contains a special member')
            if actual != entries[name]:
                raise ValueError('Member digest mismatch: '+name)
            seen[name] = actual
    if seen != entries:
        raise ValueError('Archive is incomplete')
    # Budget unique payload once, while reserving a block for every directory
    # entry (including hardlinks) and directory. This intentionally overstates
    # directory storage; the live host policy remains the final limit.
    return dict(unique_payload_bytes=payload, hardlinks=links,
        restore_reserve_bytes=allocated+block_size*(len(entries)+len(directories)))


def pack(directory, output, release=False):
    directory, output = Path(directory).resolve(), Path(output).resolve()
    allowed = ROOT/'runs/scaleup_issue_screening'
    if not directory.is_relative_to(allowed) or directory == allowed:
        raise ValueError('Only this experiment owns releasable case directories')
    marker = read(directory/'case.json')
    if marker.get('owned_by') != 'scaleup_issue_screening' or marker.get('schema_version') != 'issue-screening-case/1':
        raise ValueError('Missing case ownership marker')
    if output.is_relative_to(directory) or output.exists() or Path(str(output)+'.json').exists():
        raise ValueError('Archive must be new and outside the case')
    entries = inventory(directory)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, 'x:gz', compresslevel=6, dereference=False) as stream:
        for name in entries:
            stream.add(directory/name, arcname=name, recursive=False)
    verify(output, entries)
    # Catch modifications during compression, including unregistered files.
    if inventory(directory) != entries:
        raise ValueError('Case changed during archiving; nothing released')
    report = dict(schema_version='issue-screening-archive/1', archive_sha256=digest(output),
        archive_bytes=output.stat().st_size, source_directory=str(directory), entries=entries,
        logical_bytes=sum(v['bytes'] for v in entries.values()), verified=True, expanded_released=False)
    save(str(output)+'.json', report)
    if release:
        for name in entries:
            (directory/name).unlink()
        for path in sorted(directory.rglob('*'), key=lambda p:len(p.parts), reverse=True):
            path.rmdir()
        directory.rmdir()
        report['expanded_released'] = True
        save(str(output)+'.json', report)
    return {k:v for k,v in report.items() if k != 'entries'}


def restore(archive, output):
    archive, output = Path(archive).resolve(), Path(output).resolve()
    report = read(str(archive)+'.json')
    if report['schema_version'] != 'issue-screening-archive/1' or digest(archive) != report['archive_sha256']:
        raise ValueError('Archive identity mismatch')
    entries = report['entries']
    for name in entries:
        safe_name(name)
    if output.exists():
        raise ValueError('Restore output must not exist')
    # Verify hardlinks before counting their shared payload only once. Still
    # charge file-block rounding and conservative directory-entry space.
    from moe_ub_cosim.host_probe import disk_usage
    import shutil
    policy = host()
    roots = sorted({Path(p).resolve() for p in policy['artifact_roots']} | {output.parent}, key=lambda p:len(p.parts))
    counted, seen = [], set()
    total = dict(apparent_bytes=0, allocated_bytes=0)
    for root in roots:
        if any(root.is_relative_to(parent) for parent in counted):
            continue
        counted.append(root)
        for key, value in disk_usage(root, seen).items():
            total[key] += value
    existing = output.parent
    while not existing.exists():
        existing = existing.parent
    footprint = verify(archive, entries, os.statvfs(existing).f_frsize)
    needed = footprint['restore_reserve_bytes']
    if max(total.values())+needed > policy['artifact_bytes'] or shutil.disk_usage(existing).free-needed < policy['min_free_bytes']:
        raise ValueError('Restoration would exceed the unchanged disk budget')
    output.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, 'r|gz') as stream:
        for member in stream:
            target = output/safe_name(member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.islnk():
                os.link(output/safe_name(member.linkname), target)
            else:
                with stream.extractfile(member) as data, target.open('xb') as dest:
                    while block := data.read(2**20):
                        dest.write(block)
                target.chmod(member.mode & 0o777)
    if inventory(output) != entries:
        raise ValueError('Restored content differs')
    return dict(restored=True, output=str(output), files=len(entries),
        logical_bytes=report['logical_bytes'], **footprint)


def release_restored(directory, archive, keep=()):
    """Release only byte-verified restored files; the original archive survives.

    Kept directories remain usable as sealed public runs/slices. An external
    journal binds subsequent cleanup to the same archive and exact live files.
    """
    directory,archive=Path(directory).resolve(),Path(archive).resolve()
    allowed=ROOT/'runs/scaleup_issue_screening'
    if not directory.is_relative_to(allowed) or directory==allowed or archive.is_relative_to(directory):
        raise ValueError('Only this experiment owns releasable restored copies')
    marker=read(directory/'case.json')
    if marker.get('owned_by')!='scaleup_issue_screening' or marker.get('schema_version')!='issue-screening-case/1':
        raise ValueError('Missing restored case ownership marker')
    report=read(str(archive)+'.json')
    if not report.get('verified') or digest(archive)!=report['archive_sha256']:
        raise ValueError('Restored source archive identity mismatch')
    verify(archive,report['entries'])
    journal=Path(str(directory)+'.release.json');released=set()
    if journal.exists():
        previous=read(journal)
        if previous['archive_sha256']!=report['archive_sha256'] or previous['status']!='completed':
            raise ValueError('Inspect incomplete or mismatched release journal')
        released=set(previous['released_files'])
    remaining={k:v for k,v in report['entries'].items() if k not in released}
    if inventory(directory)!=remaining:
        raise ValueError('Restored copy changed or contains unregistered files; nothing released')
    prefixes=[safe_name(k) for k in keep]
    if any(not (directory/k).is_dir() for k in prefixes):raise ValueError('Keep paths must be existing directories')
    selected=[k for k in remaining if not prefixes or
              (k!='case.json' and not any(k.startswith(p+'/') for p in prefixes))]
    proof=dict(schema_version='issue-screening-restored-release/1',archive=str(archive),
        archive_sha256=report['archive_sha256'],directory=str(directory),keep_directories=prefixes,
        status='verified_pending_release',released_files=sorted(released),pending_files=selected)
    save(journal,proof)
    for name in selected:(directory/name).unlink()
    for path in sorted(directory.rglob('*'),key=lambda p:len(p.parts),reverse=True):
        if path.is_dir() and not any(path.iterdir()):path.rmdir()
    if not any(directory.iterdir()):directory.rmdir()
    proof.update(status='completed',released_files=sorted(released|set(selected)),pending_files=[])
    save(journal,proof)
    return dict(passed=True,released_files=len(selected),kept_files=len(remaining)-len(selected),journal=str(journal))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('pack', 'restore', 'release-restored'))
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--release-expanded', action='store_true', help='Pack: release only registered case files after two digest checks')
    p.add_argument('--keep-directory',action='append',default=[],help='release-restored: keep this relative directory; output names the existing source archive')
    p.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    a = p.parse_args()
    if a.action != 'pack' and a.release_expanded:
        p.error('--release-expanded applies only to pack')
    if a.keep_directory and a.action!='release-restored':p.error('--keep-directory applies only to release-restored')
    if not a.worker:
        from moe_ub_cosim.host_probe import probe
        args = [sys.executable, '-m', 'experiments.scaleup_issue_screening.analysis.archive', a.action,
                '--input', str(a.input.resolve()), '--output', str(a.output.resolve()), '--worker']
        if a.release_expanded:
            args.append('--release-expanded')
        for name in a.keep_directory:args.extend(['--keep-directory',name])
        policy = host()
        policy['artifact_roots'] += [str(a.output.resolve().parent)]
        logs=str(a.input.resolve())+'-release-'+str(len(a.keep_directory))+'-resources' if a.action=='release-restored' else str(a.output.resolve())+'-resources'
        report = probe(args, logs, **policy)
        if report['status'] != 'completed':
            raise SystemExit('Archive operation failed: '+report['status'])
    else:
        result = (pack(a.input,a.output,a.release_expanded) if a.action=='pack' else
                  restore(a.input,a.output) if a.action=='restore' else release_restored(a.input,a.output,a.keep_directory))
        print(result)


if __name__ == '__main__':
    main()
