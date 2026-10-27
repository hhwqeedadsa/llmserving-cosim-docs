"""Independent offline check of cached Git identities and source references."""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import re

PINS = {
    'clem': ('aliyun/SimAI-CLEM', '4a91f8b655224c0b1d1c300aa09c7058f4da2707'),
    'network': ('aliyun/ns-3-alibabacloud', '434e4f2fe4bc2fb803a1dcda690869f66802f8b8'),
}
EXPECTED = {
    'clem': {'README.md', 'nccl_patch', 'nccl_tests_patch', 'run_nccl_16A100.sh', 'LICENSE', 'NOTICE'},
    'network': {'simulation/LICENSE', 'simulation/scratch/QpReuseSimInfra.cc',
                *('simulation/src/applications/model/' + name for name in
                  ('ibv-interface.cc', 'ibv-interface.h', 'ibv-qp.cc', 'ibv-qp.h', 'ibv-cq.cc', 'ibvcore.h')),
                'simulation/src/point-to-point/model/rdma-hw.cc',
                'simulation/src/point-to-point/model/rdma-queue-pair.cc'},
}
REFERENCE_IDS = set('host_hook verbs_dispatch post_send command_fields p2p_fifo poll_cq gather_address reduce_address scatter_address allocation allocation_size host_identity launch interface_defaults submit_latency request_opcodes flush_read persistent_qp wr_completed fifo_visibility completion_opcodes message_enqueue message_front message_finish ack_condition ack_handling completion_callback parallel_hook install_interfaces'.split())


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git_object(kind, raw):
    return hashlib.sha1(kind.encode() + b' ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def check_tree(tree):
    assert not tree.get('truncated')
    rows = tree['tree']
    assert len({r['path'] for r in rows}) == len(rows)
    directories = {'': []}
    expected = {'': tree['sha']}
    for row in rows:
        if row['type'] == 'tree':
            directories[row['path']] = []
            expected[row['path']] = row['sha']
    for row in rows:
        parent, _, name = row['path'].rpartition('/')
        assert parent in directories
        directories[parent].append((name, row))
    for parent, children in directories.items():
        payload = b''
        for name, row in sorted(children, key=lambda x: (x[0] + ('/' if x[1]['type'] == 'tree' else '')).encode()):
            payload += row['mode'].lstrip('0').encode() + b' ' + name.encode() + b'\0' + bytes.fromhex(row['sha'])
        assert git_object('tree', payload) == expected[parent], parent
    return len(directories)


def target_coordinate(lines, number):
    """Count post-image lines from the nearest hunk, independently of study.py."""
    at = number - 1
    start = max(i for i in range(at + 1) if lines[i].startswith('@@ '))
    file_start = max(i for i in range(start) if lines[i].startswith('+++ b/'))
    target = lines[file_start][6:]
    match = re.search(r'\+(\d+)(?:,\d+)? @@', lines[start])
    assert match
    coordinate = int(match.group(1))
    for line in lines[start+1:at]:
        assert not line.startswith(('diff --git ', '@@ '))
        if line.startswith((' ', '+')):
            coordinate += 1
    assert lines[at].startswith((' ', '+'))
    return target, coordinate


def check(root, evidence, overrides=None):
    overrides = overrides or {}

    def read(relative):
        return overrides[relative] if relative in overrides else (root / relative).read_bytes()

    def js(relative):
        return json.loads(read(relative))

    requests = js('cache/http.json')
    assert len(requests) <= 24 and len({r['url'] for r in requests}) == len(requests)
    assert len(requests) == evidence['requests'] == 20
    total_bytes = sum(r['bytes'] for r in requests)
    total_time = sum(r['elapsed_seconds'] for r in requests)
    assert total_bytes <= 8*2**20 and total_bytes == evidence['response_bytes']
    assert total_time < 180 and total_time == evidence['http_elapsed_seconds']
    assert all(0 <= r['bytes'] <= 4*2**20 for r in requests)
    violations = [dict(path=r['path'], elapsed_seconds=r['elapsed_seconds'], status=r['status'], error=r['error'])
                  for r in requests if r['elapsed_seconds'] > 10.5]
    assert len(violations) == 2 and violations == evidence['deadline_violations']
    assert {r['path'] for r in violations} == {'clem/sources/README.md', 'network/sources/simulation/src/applications/model/ibv-interface.cc'}
    for row in requests:
        body = read('cache/' + row['path'])
        assert len(body) == row['bytes'] and digest(body) == row['sha256'], row['path']
    tree_count = 0
    trees = {}
    for label, (repo, revision) in PINS.items():
        identity = js(f'cache/{label}/identity.json')
        commit = js(f'cache/{label}/commit.json')
        tree = js(f'cache/{label}/tree.json')
        assert (identity['repo'], identity['commit']) == (repo, revision)
        assert commit['sha'] == revision and commit['commit']['tree']['sha'] == tree['sha'] == identity['tree']
        assert evidence['identities'][label] == identity
        tree_count += check_tree(tree)
        trees[label] = {r['path']: r for r in tree['tree']}
    sources = evidence['sources']
    assert len(sources) == 16
    assert len({(r['label'], r['path']) for r in sources}) == len(sources)
    for label, names in EXPECTED.items():
        assert {r['path'] for r in sources if r['label'] == label} == names
    for row in sources:
        label, name = row['label'], row['path']
        raw = read(row['cache_path'])
        expected = trees[label][name]
        assert len(raw) == row['bytes'] == expected['size']
        assert git_object('blob', raw) == row['git_blob'] == expected['sha']
        assert digest(raw) == row['sha256']
        if name == 'README.md':
            assert row['cache_path'] == 'cache/clem/fallback/README.md'
            assert read('cache/clem/sources/README.md') == b''
            assert js('cache/clem/fallback/receipt.json')['git_blob'] == expected['sha']
        else:
            response_path = f'{label}/responses/{name}.json'
            raw_path = f'{label}/sources/{name}'
            associated = [r for r in requests if r['path'] in (response_path, raw_path)]
            assert len(associated) == 1
            request = associated[0]
            assert request['status'] == 200 and not request['error']
            if request['path'] == response_path:
                blob_response = js('cache/' + response_path)
                assert blob_response['sha'] == expected['sha'] and blob_response['encoding'] == 'base64'
                assert base64.b64decode(blob_response['content']) == raw
    refs = evidence['references']
    assert len(refs) == len(REFERENCE_IDS) and {r['id'] for r in refs} == REFERENCE_IDS
    for row in refs:
        raw = read(f"cache/{row['label']}/sources/{row['path']}")
        lines = raw.decode().splitlines()
        assert row['needle'] in lines[row['line']-1]
        assert '\n'.join(lines[row['excerpt_start']-1:row['excerpt_end']]) == row['excerpt']
        assert row['excerpt_start'] <= row['line'] <= row['excerpt_end']
        if row['target_path']:
            assert target_coordinate(lines, row['line']) == (row['target_path'], row['target_line'])
        else:
            assert row['target_line'] is None
        repo, revision = PINS[row['label']]
        assert row['url'] == f"https://github.com/{repo}/blob/{revision}/{row['path']}#L{row['line']}"
    assert evidence['scope'] == dict(source_audit=True, network_simulations=0, builds=0, GPU_runs=0,
                                     platform_changes=0, runtime_equivalence_proven=False,
                                     research_innovations_established=False)
    return dict(sources=len(sources), references=len(refs), reconstructed_git_trees=tree_count,
                HTTP_requests=len(requests), response_bytes=total_bytes,
                preserved_deadline_violations=len(violations))


def execute(root, output):
    evidence = json.loads((root / 'analysis/evidence.json').read_bytes())
    counts = check(root, evidence)
    mutations = []
    broken = copy.deepcopy(evidence)
    broken['sources'][0]['sha256'] = '0'*64
    mutations.append(('source_digest', broken, {}))
    broken = copy.deepcopy(evidence)
    broken['sources'].pop()
    mutations.append(('source_coverage', broken, {}))
    broken = copy.deepcopy(evidence)
    broken['references'][0]['line'] += 1
    mutations.append(('source_line', broken, {}))
    broken = copy.deepcopy(evidence)
    broken['references'][0]['target_line'] += 1
    mutations.append(('patch_coordinate', broken, {}))
    broken = copy.deepcopy(evidence)
    broken['deadline_violations'] = []
    mutations.append(('hidden_deadline_failure', broken, {}))
    raw = bytearray((root / 'cache/clem/tree.json').read_bytes())
    tree = json.loads(raw)
    tree['tree'][0]['sha'] = '0'*40
    mutations.append(('tree_tamper', evidence, {'cache/clem/tree.json': json.dumps(tree).encode()}))
    path = 'cache/clem/responses/nccl_patch.json'
    response = json.loads((root / path).read_bytes())
    response['content'] = base64.b64encode(b'altered patch').decode()
    mutations.append(('response_body', evidence, {path: json.dumps(response).encode()}))
    rejected = []
    for name, value, overrides in mutations:
        try:
            check(root, value, overrides)
        except (AssertionError, ValueError, IndexError):
            rejected.append(name)
        else:
            raise AssertionError('corrupt evidence accepted: ' + name)
    markdown = (root / 'analysis/report.md').read_text()
    page = (root / 'analysis/report.html').read_text()
    assert '<table>' in page and '{{' not in markdown
    for ref in evidence['references']:
        assert ref['url'] in markdown and ref['url'] in page
    result = dict(passed=True, **counts, rejected_corruption_categories=rejected,
                  runtime_performance_validated=False, source_semantics_manually_reviewed=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    execute(args.run.resolve(), args.output.resolve())
