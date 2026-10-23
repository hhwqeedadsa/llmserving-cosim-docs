"""Bounded residual-study report and offline recovery; no network reexecution."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'experiments/paper_readiness'))
from package_multiconnection_visibility import render

AUTHORS = ['experiments/rail_channel_screening/'+name for name in
           ('residual.py', 'residual_check.py', 'residual_package.py', 'residual_protocol.md', 'README.md', 'run.py')]
AUTHORS += ['experiments/paper_readiness/package_multiconnection_visibility.py',
            'docs/reviews/packet_residual_20261023.md', 'deps.lock.json']


def load(path):
    return json.loads(path.read_text())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def report(repo, root, out):
    out.mkdir(parents=True, exist_ok=True)
    for name in ('conditions.json', 'scope.json'):
        shutil.copy2(root/'analysis-ties'/name, out/name)
    shutil.copy2(root/'verification-ties.json', out/'verification.json')
    shutil.copy2(root/'time-basis/sources.json', out/'sources.json')
    body = render((repo/'docs/reviews/packet_residual_20261023.md').read_text())
    def relink(match):
        target = match[1]
        if target.startswith(('https:', 'http:', '#')):
            return match[0]
        path, *fragment = target.split('#', 1)
        relative = os.path.relpath((repo/'docs/reviews'/path).resolve(), out.resolve())
        return 'href="'+relative+('#'+fragment[0] if fragment else '')+'"'
    body = re.sub(r'href="([^"]+)"', relink, body)
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>逐包完成链核查</title>
<style>body{background:#f2f5f8;color:#172c40;font:17px/1.9 system-ui,sans-serif;margin:0}
main{background:white;max-width:1080px;margin:28px auto;padding:30px 42px;border-radius:12px}
h1{font-size:29px}h2{font-size:23px;border-top:1px solid #dbe3ed;padding-top:20px;margin-top:32px}
table{border-collapse:collapse;width:100%;font-size:15px}td,th{border:1px solid #ccd8e5;padding:9px;text-align:left}
th{background:#edf3f9}.table{overflow:auto}a{color:#155396}code{overflow-wrap:anywhere}p{margin:18px 0}
@media(max-width:700px){main{margin:0;padding:18px}h1{font-size:25px}}</style><main>'''+body+'</main></html>'
    (out/'report.html').write_text(page)


def archive(repo, root, out):
    dest = root/'checkpoint'
    dest.mkdir()
    files = {}
    for path in root.rglob('*'):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root)
        if rel.parts[0] in ('checkpoint', 'recovery'):
            continue
        if rel.parts[0].endswith('-monitor') and not (root/rel.parts[0]/'probe.json').exists():
            continue
        files['raw/'+str(rel)] = path
    for name in AUTHORS:
        files['source/'+name] = repo/name
    for name in ('sources.json', 'conditions.json', 'scope.json', 'verification.json', 'report.html'):
        files['report/'+name] = out/name
    members = {name: dict(bytes=path.stat().st_size, sha256=sha(path)) for name, path in sorted(files.items())}
    size = sum(row['bytes'] for row in members.values())
    assert size < 64*2**20 and len(members) < 512
    path = dest/'checkpoint.tar.gz'
    with tarfile.open(path, 'w:gz') as tf:
        for name, source in sorted(files.items()):
            tf.add(source, arcname=name, recursive=False)
    assert path.stat().st_size < 24*2**20
    with tarfile.open(path, 'r:gz') as tf:
        assert len(tf.getmembers()) == len(members)
        for member in tf:
            assert member.isfile()
            data = tf.extractfile(member).read()
            assert len(data) == members[member.name]['bytes']
            assert hashlib.sha256(data).hexdigest() == members[member.name]['sha256']
    save(dest/'members.json', members)
    summary = dict(passed=True, members=len(members), member_bytes=size, compressed_bytes=path.stat().st_size,
                   archive_sha256=sha(path), volatile=True, persistent_backup=False,
                   includes_native_binary=False, includes_native_libraries=False, network_reexecuted=False)
    save(dest/'archive.json', summary)
    save(out/'archive.json', summary)
    print(json.dumps(summary))


def recover(repo, root, out):
    del repo
    dest = root/'recovery'
    dest.mkdir()
    manifest = load(root/'checkpoint/members.json')
    path = root/'checkpoint/checkpoint.tar.gz'
    assert sha(path) == load(root/'checkpoint/archive.json')['archive_sha256']
    with tarfile.open(path, 'r:gz') as tf:
        assert len(tf.getmembers()) == len(manifest)
        for member in tf:
            name = PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts
            data = tf.extractfile(member).read()
            assert hashlib.sha256(data).hexdigest() == manifest[member.name]['sha256']
            # Keep old graph bytes only in archive; input/network copies and new
            # graphs fit the unchanged aggregate storage budget.
            if member.name.startswith(('source/', 'raw/input/', 'raw/network/', 'raw/time-basis/')):
                target = dest/member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
    tool = dest/'source/experiments/rail_channel_screening'
    raw = dest/'raw'
    new = dest/'new'
    new.mkdir()
    cwd = dest/'empty-cwd'
    cwd.mkdir()
    env = dict(os.environ, PYTHONPATH='', PYTHONDONTWRITEBYTECODE='1')
    commands = []
    stages = [('analyze', [tool/'residual.py', 'analyze', '--inputs', raw/'input',
                           '--network', raw/'network', '--output', new/'analysis']),
              ('check', [tool/'residual_check.py', '--inputs', raw/'input', '--network', raw/'network',
                         '--analysis', new/'analysis', '--output', new/'verification.json'])]
    for name, argv in stages:
        command = [sys.executable, *map(str, argv)]
        process = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=30)
        (new/(name+'.stdout')).write_bytes(process.stdout)
        (new/(name+'.stderr')).write_bytes(process.stderr)
        commands.append(dict(command=command, cwd=str(cwd), exit_code=process.returncode, parent_pythonpath=''))
        save(new/'commands.json', commands)
        assert process.returncode == 0, process.stderr.decode()[-2000:]
    identical = []
    with tarfile.open(path, 'r:gz') as tf:
        for item in sorted((new/'analysis').glob('*.json')):
            data = tf.extractfile('raw/analysis-ties/'+item.name).read()
            assert data == item.read_bytes()
            identical.append(dict(path='analysis/'+item.name, sha256=sha(item)))
        assert tf.extractfile('raw/verification-ties.json').read() == (new/'verification.json').read_bytes()
        identical.append(dict(path='verification.json', sha256=sha(new/'verification.json')))
    summary = dict(passed=True, archive_members_checked=len(manifest), new_source_root=True, new_cwd=True,
                   empty_parent_pythonpath=True, research_commands=2, byte_identical_artifacts=identical,
                   network_reexecuted=False, binaries_required_for_offline_analysis=False)
    save(root/'recovery.json', summary)
    save(out/'recovery.json', summary)
    print(json.dumps(summary))


def final(repo, root, out):
    manifest = load(root/'checkpoint/members.json')
    for name in AUTHORS:
        assert sha(repo/name) == manifest['source/'+name]['sha256']
        if name.endswith('.py'):
            ast.parse((repo/name).read_text())
    old_runtime = load(root/'runtime-identity.json')['old_runtime']
    assert all(sha(Path(name)) == digest for name, digest in old_runtime.items())
    for record in load(root/'time-basis/sources.json'):
        path = root/'time-basis'/record['path']
        assert sha(path) == record['sha256']
        lines = path.read_text().splitlines()
        for excerpt in record['excerpts']:
            start = excerpt['first_line']-1
            assert lines[start:start+len(excerpt['lines'])] == excerpt['lines']
    complete = []
    for path in sorted(root.glob('*-monitor/probe.json')):
        data = load(path)
        assert data['status'] == 'completed', str(path)
        complete.append(dict(path=str(path.relative_to(root)), wall_seconds=data['wall_seconds'],
                             peak_sampled_group_rss_bytes=data['peak_sampled_group_rss_bytes']))
    assert sum(r['wall_seconds'] for r in complete) < 480
    size = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    platform = Path('/dev/shm/moe-wire-headers-20261023')
    platform_size = sum(p.stat().st_size for p in platform.rglob('*') if p.is_file())
    assert size < 128*2**20 and platform_size < 128*2**20 and size+platform_size < 256*2**20
    assert shutil.disk_usage(root).free >= 8*2**30
    commands = load(root/'network/commands.json')
    assert len(commands) == 8 and all(r['exit_code'] == 0 for r in commands)
    networks = []
    for path in sorted((root/'network').glob('*/execution/probe.json')):
        data = load(path)
        assert data['status'] == 'completed'
        networks.append(dict(path=str(path.relative_to(root)), wall_seconds=data['wall_seconds'],
                             peak_sampled_group_rss_bytes=data['peak_sampled_group_rss_bytes']))
    assert len(networks) == 6 and sum(r['wall_seconds'] for r in networks) <= 360
    recovered = load(root/'recovery.json')
    assert recovered['passed'] and len(recovered['byte_identical_artifacts']) == 9
    # Resolve all file links in this report and its HTML, ignoring anchors.
    link_count = 0
    md = repo/'docs/reviews/packet_residual_20261023.md'
    for path, targets in [(md, re.findall(r'\]\(([^)]+)\)', md.read_text())),
                          (out/'report.html', re.findall(r'href="([^"]+)"', (out/'report.html').read_text()))]:
        for target in targets:
            if target.startswith(('https:', 'http:', '#')):
                continue
            resolved = (path.parent/target.split('#')[0]).resolve()
            if resolved != (out/'closeout.json').resolve():
                assert resolved.exists(), (path, target)
            link_count += 1
    summary = dict(passed=True, author_snapshots_match=len(AUTHORS), old_runtime_hashes_unchanged=len(old_runtime),
                   native_source_files=5, local_links_checked=link_count,
                   independent_verification=load(root/'verification-ties.json')['passed'],
                   public_network_commands=8, observation_conditions=6,
                   original_result_fields_identical=True, independent_new_performance_samples=False,
                   offline_identical_json_artifacts=9, stage_monitors=complete, network_monitors=networks,
                   elapsed_stage_seconds=sum(r['wall_seconds'] for r in complete),
                   research_bytes=size, platform_bytes=platform_size, combined_bytes=size+platform_size,
                   tmpfs_free_bytes=shutil.disk_usage(root).free,
                   root_report_bytes=sum(p.stat().st_size for p in out.glob('*') if p.is_file()),
                   root_volume_free_bytes=shutil.disk_usage(repo).free,
                   storage='tmpfs_volatile', persistent_backup=False, paper_contribution_established=False)
    save(root/'closeout.json', summary)
    save(out/'closeout.json', summary)
    assert (out/'closeout.json').is_file()
    print(json.dumps(summary))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('report', 'archive', 'recover', 'final'))
    for name in ('repo', 'root', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    globals()[args.phase](args.repo.resolve(), args.root.resolve(), args.output.resolve())
