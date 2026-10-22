"""Archive the fixed parameter study, original routes, runtime and citation evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('task', 'report', 'platform-checkpoint', 'baseline', 'papers'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    task, report, baseline = args.task.resolve(), args.report.resolve(), args.baseline.resolve()
    checkpoint = task / 'checkpoint'
    assert not checkpoint.exists()
    monitors = []
    for path in sorted(task.glob('*-monitor/probe.json')):
        if path.parent.name == 'package-monitor':
            continue
        record = load(path)
        assert record['status'] == 'completed' and not record['remaining_live_group_pids']
        monitors.append(dict(stage=path.parent.name, record=record))
    assert sum(m['record']['wall_seconds'] for m in monitors) < 240
    execution = load(task / 'run/execution.json')
    assert execution['completed'] and execution['new_network_runs'] == 6 and execution['reused_successful_runs'] == 2
    assert load(task / 'checked/verification.json')['verified']
    assert (task / 'compat/verification.json').read_bytes() == (baseline / 'checked-refined/verification.json').read_bytes()
    binary = Path(execution['backend_path'])
    assert sha(binary) == execution['backend_sha256']
    prior = args.platform_checkpoint.resolve()
    platform_archive = prior / 'checkpoint.tar.gz'
    platform_members = load(prior / 'members.json')
    assert sha(platform_archive) == '4932e8bdb9597821b3e5437f43120eb10f4b29e6efe8ee437c48fac9f4c09cfd'
    verified_platform = set()
    with tarfile.open(platform_archive, 'r:gz') as tar:
        for member in tar:
            if member.name not in platform_members:
                assert member.name == 'members.json'
                continue
            data = tar.extractfile(member).read()
            entry = platform_members[member.name]
            assert len(data) == entry['bytes'] and hashlib.sha256(data).hexdigest() == entry['sha256']
            verified_platform.add(member.name)
    assert len(verified_platform) == len(platform_members) == 648
    entries = {}

    def add(path, name):
        assert name not in entries and path.is_file() and not path.is_symlink()
        entries[name] = dict(source=str(path), bytes=path.stat().st_size, sha256=sha(path))

    for path in sorted(task.rglob('*')):
        if path.is_file() and 'package-monitor' not in path.relative_to(task).parts:
            add(path, 'raw/' + str(path.relative_to(task)))
    save(report / 'maintenance.json', dict(monitors=monitors,
        monitored_seconds_before_packaging=sum(m['record']['wall_seconds'] for m in monitors),
        raw_path=str(task), raw_volatile=True, persistent_backup=False))
    for path in sorted(report.iterdir()):
        if path.is_file():
            add(path, 'report/' + path.name)
    # Public platform sources and research helpers are separate namespaces in the archive.
    files = [*sorted((ROOT / 'python/moe_ub_cosim').rglob('*.py')),
             *sorted((ROOT / 'backend').rglob('*.cc')), *sorted((ROOT / 'backend').rglob('*.hh')),
             ROOT / 'backend/CMakeLists.txt', ROOT / 'deps.lock.json', ROOT / 'AGENTS.md']
    names = ('feedback_parameters.py', 'check_feedback_parameters.py', 'build_feedback_parameter_review.py',
             'package_feedback_parameters.py', 'check_feedback_parameter_delivery.py',
             'feedback_parameter_protocol.md', 'check_feedback_factorial.py',
             'check_feedback_overlap.py', 'feedback_overlap.py', 'feedback_overlap_protocol.md',
             'build_feedback_overlap_review.py', 'prefill_pressure.py', 'analyze_dispatch_validation.py', 'README.md')
    files += [ROOT / 'experiments/paper_readiness' / name for name in names]
    files += [ROOT / 'examples/scaleout_rdma/small-clos-100g.json', ROOT / 'examples/scaleout_rdma/rdma.json',
              ROOT / 'backend/rdma/fabric.cc', ROOT / 'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/point-to-point/model/switch-mmu.cc']
    for path in sorted(set(files)):
        add(path, 'source/' + str(path.relative_to(ROOT)))
    for item in load(task / 'input/truth/manifest.json')['files']:
        path = ROOT / item['file']['path']
        assert sha(path) == item['file']['sha256']
        add(path, 'source/' + item['file']['path'])
    # Four old conditions allow the read-only oracle compatibility check without old network runs.
    old_paths = ['input/observed/topology.json', 'input/observed/protocol.json', 'input/truth/manifest.json',
                 'input/truth/provenance.json', 'run/execution.json', 'run/topology/topology.json',
                 'analysis/summary.json', 'checked-refined/verification.json']
    for case in load(baseline / 'run/execution.json')['executed']:
        name = case['name']
        old_paths += ['input/observed/' + name + '.json', 'run/' + name + '/result.json', 'run/' + name + '/inputs.json']
    for name in old_paths:
        add(baseline / name, 'prior-overlap/' + name)
    for name in ('members.json', 'archive.json'):
        add(baseline / 'checkpoint' / name, 'prior-overlap/historical-checkpoint/' + name)
    old_archive = baseline / 'checkpoint/checkpoint.tar.gz'
    assert sha(old_archive) == load(report / 'erratum.json')['historical_archive']['sha256']
    add(platform_archive, 'prior-platform/checkpoint.tar.gz')
    add(prior / 'members.json', 'prior-platform/members.json')
    add(binary, 'runtime/bin/' + binary.name)
    libraries = []
    for name in load(task / 'run/d8-ai5-p1/result.json')['loaded_libraries']:
        path = Path(name)
        if not path.is_absolute():
            continue
        actual = path.resolve()
        if path.name.startswith('libns3.'):
            member = 'raw/runtime/' + path.name
            assert sha(actual) == platform_members[member]['sha256']
            archive_path = 'prior-platform/checkpoint.tar.gz::' + member
        else:
            archive_path = 'runtime/lib/' + path.name
            add(actual, archive_path)
        libraries.append(dict(original_path=name, archive_path=archive_path, sha256=sha(actual)))
    expected_papers = {'dcqcn2015.pdf': '879074a33b78ceb93f9f66d59b217fba9e7621320cc68bc47914747cf5cf31f8',
                       'roce_hoti2022.pdf': '0c9648a0d229a2c0badea8c36671447cd46e2b457581b8dc3bb91891b17cc75d'}
    for name, expected in expected_papers.items():
        assert sha(args.papers / name) == expected
        for filename in (name, name.replace('.pdf', '.txt')):
            add(args.papers / filename, 'papers/' + filename)
    member_bytes = sum(e['bytes'] for e in entries.values())
    assert member_bytes <= 96 * 2**20
    checkpoint.mkdir()
    save(checkpoint / 'members.json', entries)
    archive = checkpoint / 'checkpoint.tar.gz'
    with tarfile.open(archive, 'w:gz', dereference=True) as tar:
        for name, entry in sorted(entries.items()):
            tar.add(entry['source'], arcname=name, recursive=False)
        tar.add(checkpoint / 'members.json', arcname='members.json', recursive=False)
    assert archive.stat().st_size <= 48 * 2**20
    verified = set()
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            if member.name == 'members.json':
                assert json.load(tar.extractfile(member)) == entries
                continue
            assert member.name not in verified
            data = tar.extractfile(member).read()
            entry = entries[member.name]
            assert len(data) == entry['bytes'] and hashlib.sha256(data).hexdigest() == entry['sha256']
            verified.add(member.name)
    assert verified == entries.keys()
    receipt = dict(path=str(archive), bytes=archive.stat().st_size, sha256=sha(archive),
        verified_members=len(verified), member_bytes=member_bytes, nested_platform_members_verified=len(verified_platform),
        runtime=libraries, binary_sha256=sha(binary), source_head=subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), source_worktree_has_research_edits=True,
        volatile=True, persistent_backup=False, runtime_portability='Original ELF RPATH remains; not a portable runtime image',
        prior_overlap_scope='Four old successful conditions plus citation snapshots and receipts; old full archive is referenced by hash, not embedded',
        excluded='Active package monitor and archive itself; receipts/final checks added after packaging are external to this snapshot',
        new_network_runs=6, reused_successful_runs=2, no_additional_network_execution=True)
    save(report / 'archive.json', receipt)
    save(checkpoint / 'archive.json', receipt)
    print(json.dumps(dict(archive=True, verified_members=len(verified), bytes=receipt['bytes'], sha256=receipt['sha256'])))


if __name__ == '__main__':
    main()
