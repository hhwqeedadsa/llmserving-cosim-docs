"""Verify relocation, record completed stages, and pack this fixed read-only audit."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
CODE = (
    'dcqcn_validity_protocol.md', 'dcqcn_reference_requirements.md',
    'collect_dcqcn_papers.py', 'audit_dcqcn_validity.py',
    'check_dcqcn_validity.py', 'build_dcqcn_validity_review.py',
    'package_dcqcn_validity.py', 'README.md',
)


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def stream_sha(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024*1024), b''):
        digest.update(block)
    return digest.hexdigest()


def sha(path):
    with path.open('rb') as stream:
        return stream_sha(stream)


def normalized_feedback(path, task):
    result = read(path)
    for row in result['input_identities']:
        row['path'] = str(Path(row['path']).relative_to(task))
    return result


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.extend(v for k, v in attrs if k == 'href')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', type=Path, required=True)
    parser.add_argument('--original-task', type=Path, required=True)
    parser.add_argument('--prior-members', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    args = parser.parse_args()
    task = args.task.resolve()
    out = args.output.resolve()
    checkpoint = args.checkpoint.resolve()
    assert task.is_relative_to(Path('/dev/shm'))
    assert out.is_relative_to(ROOT/'docs/review_artifacts')
    assert checkpoint.is_relative_to(task) and not checkpoint.exists()
    assert not out.exists()
    # Only already-finished monitors are admitted. Never archive a live monitor.
    names = ('collect', 'audit', 'audit-reviewed', 'check', 'review',
             'relocation-audit', 'relocation-check')
    monitors = []
    for name in names:
        path = task/(name+'-monitor')/'probe.json'
        record = read(path)
        assert not record['remaining_live_group_pids']
        expected = 1 if name == 'audit' else 0
        assert record['status'] == ('child_failed' if expected else 'completed')
        assert record['child_exit_code'] == expected
        monitors.append(dict(stage=name, probe=str(path), record=record))
    assert sum(m['record']['wall_seconds'] for m in monitors) < 180
    for name in ('source-audit.json', 'feedback-events.json'):
        assert (task/'audit-reviewed'/name).read_bytes() == (task/'relocated-audit'/name).read_bytes()
    assert normalized_feedback(task/'audit-reviewed/feedback-audit.json', args.original_task.resolve()) == normalized_feedback(
        task/'relocated-audit/feedback-audit.json', task/'relocated-task')
    for name in ('verification.json', 'witness.json'):
        assert (task/'check'/name).read_bytes() == (task/'relocated-check'/name).read_bytes()
    checks = read(task/'check/verification.json')
    assert checks['passed'] and checks['new_network_runs'] == 0
    prior = read(ROOT/'docs/review_artifacts/prefill_pressure_20261022/archive.json')
    assert sha(task/'prior-prefill.tar.gz') == prior['sha256']

    out.mkdir(parents=True)
    for path in (task/'review').iterdir():
        assert path.is_file()
        shutil.copy2(path, out/path.name)
    shutil.copy2(ROOT/'experiments/paper_readiness/dcqcn_validity_protocol.md', out/'protocol.md')
    shutil.copy2(ROOT/'experiments/paper_readiness/dcqcn_reference_requirements.md', out/'reference-requirements.md')
    maintenance = dict(
        platform_commit=subprocess.check_output(['git', 'rev-parse', '68ad3fe'], cwd=ROOT, text=True).strip(),
        cwd=str(ROOT), stages=monitors,
        measured_stage_wall_seconds=sum(m['record']['wall_seconds'] for m in monitors),
        peak_waited_child_rss_kib=max(m['record']['max_waited_child_rss_kib'] for m in monitors),
        network_executions=0, gpu_executions=0, builds=0, dependency_changes=0,
        downloads=dict(primary_paper_urls=2, retries=0),
        failures=[dict(stage='audit', reason='Comment text GetMicroSeconds was mistaken for a function call; corrected token/comment handling only.',
                       preserved_source='raw/failed-audit-source.py', simulator_modified=False)],
        portability=dict(source_and_event_bytes_identical=True, check_and_witness_bytes_identical=True,
                         feedback_summary_equal_except_input_root=True, execution_cwd=str(task)),
        raw_conversion=dict(commands=[['pdftotext', '-raw', '-nopgbrk', str(task/'papers'/(key+'.pdf')),
                                      str(task/'papers'/(key+'.raw.txt'))] for key in ('dcqcn2015', 'roce_hoti2022')],
                            scope='Previously completed extraction; not rerun during packaging',
                            files=[dict(path=str(p), sha256=sha(p)) for p in sorted((task/'papers').glob('*.raw.txt'))]),
        storage=dict(kind='tmpfs_volatile', persistent_backup=False, root_free_bytes=shutil.disk_usage(ROOT).free,
                     tmpfs_free_bytes=shutil.disk_usage(task).free, reserve_bytes=8*2**30),
        limits=dict(per_stage_wall_seconds=60, cumulative_stage_seconds=180, rss_bytes=2**30,
                    address_bytes=2**31, cumulative_artifact_bytes=64*2**20, archive_bytes=32*2**20),
        packaging='Packaging monitor and post-pack archive/final-check manifests are external; no live logs or self-manifests in archive.')
    save(out/'maintenance.json', maintenance)

    checkpoint.mkdir()
    members = []
    def admit(path, arcname):
        assert path.is_file() and not path.is_symlink()
        members.append(dict(path=path, name=arcname, bytes=path.stat().st_size, sha256=sha(path)))
    # Enumerate explicit raw directories, including the failed partial output.
    for name in ('papers', 'audit', 'audit-reviewed', 'check', 'relocated-task', 'relocated-audit', 'relocated-check'):
        for path in sorted((task/name).rglob('*')):
            if path.is_file():
                admit(path, 'raw/'+str(path.relative_to(task)))
    for name in ('failed-audit-source.py', 'prior-prefill.tar.gz', 'relocation-preparation.json'):
        admit(task/name, 'raw/'+name)
    admit(args.prior_members.resolve(), 'raw/prior-prefill-members.json')
    for name in names:
        for path in sorted((task/(name+'-monitor')).iterdir()):
            admit(path, 'raw/'+str(path.relative_to(task)))
    for name in CODE:
        admit(ROOT/'experiments/paper_readiness'/name, 'research/'+name)
    for name in ('docs/tasks/current.md', 'docs/model_validation.md'):
        admit(ROOT/name, 'repository/'+name)
    for path in sorted(out.iterdir()):
        admit(path, 'report/'+path.name)
    assert len({m['name'] for m in members}) == len(members)
    assert sum(m['bytes'] for m in members) < 32*2**20
    archive = checkpoint/'checkpoint.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for member in members:
            tar.add(member['path'], arcname=member['name'], recursive=False)
    assert archive.stat().st_size < 32*2**20
    # Reopen the compressed stream and independently check every recorded file.
    with tarfile.open(archive, 'r:gz') as tar:
        actual = tar.getmembers()
        assert len(actual) == len(members) and all(m.isfile() for m in actual)
        for m in members:
            assert stream_sha(tar.extractfile(m['name'])) == m['sha256']
        # Embedded prior checkpoint integrity is verified member-by-member too.
        with tarfile.open(fileobj=tar.extractfile('raw/prior-prefill.tar.gz'), mode='r:gz') as embedded:
            old_members = read(args.prior_members)
            assert len(embedded.getnames()) == len(old_members) == prior['files']
            assert set(embedded.getnames()) == set(old_members)
            for name, m in old_members.items():
                assert stream_sha(embedded.extractfile(name)) == m['sha256']
    public_members = [{k: v for k, v in m.items() if k != 'path'} for m in members]
    save(out/'archive-members.json', dict(members=public_members))
    save(out/'archive.json', dict(path=str(archive), sha256=sha(archive), bytes=archive.stat().st_size,
                                 members=len(members), verified=True, storage='tmpfs_volatile', persistent_backup=False,
                                 prior_archive_sha256=prior['sha256'], nested_prior_members_verified=len(old_members)))
    links = Links()
    links.feed((out/'report.html').read_text())
    local = [link for link in links.links if not link.startswith(('http:', 'https:', '#'))]
    assert all((out/link).is_file() for link in local), local
    save(out/'final-check.json', dict(passed=True, local_html_links=local, relocated_analysis_checked=True,
                                    archive_members_checked=len(members), embedded_prior_members_checked=len(old_members),
                                    new_network_runs=0, hardware_calibrated=False,
                                    scope='Fixed read-only audit; not research contribution or hardware accuracy validation'))
    print(json.dumps(read(out/'archive.json')))


if __name__ == '__main__':
    main()
