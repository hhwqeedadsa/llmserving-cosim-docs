"""Independent call-order oracle; does not import the extractor or harness."""
import argparse
from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path

MODES = ('success', 'null_window', 'initial_shadow_error', 'table_shadow_error',
         'table_memset_error', 'window_shadow_error', 'local_deregister_error', 'stream_sync_error')


def need(ok, why):
    if not ok:
        raise AssertionError(why)


def oracle(row):
    refs, segments, connections = (row[k] for k in ('refs', 'segments', 'connections'))
    mode = row['mode'];fs = row['failure_segment'];fc = row['failure_slot']
    need(all(x in (1, 2, 3) for x in (refs, segments, connections)), 'layout')
    need(mode in MODES + ('provider_error', 'metadata_error'), 'mode')
    if mode in ('provider_error', 'metadata_error'):
        need(0 <= fs < segments and 0 <= fc < connections, 'failure location')
    else:
        need(fs == fc == -1, 'unexpected failure location')
    calls = [];retired = [];ignored = [];events = ['comm_check']
    begun = mode != 'null_window';dropped = begun and mode != 'initial_shadow_error'
    if begun:
        events += ['capture_exchange', 'get_device', 'set_device', 'stream_create', 'shadow_1']
    if dropped:
        if refs == 1:
            for segment in range(segments):
                for connection in range(connections):
                    identity = segment * 8 + connection
                    calls.append(identity);events.append('mr_' + str(identity))
                    failed = segment == fs and connection == fc and mode in ('provider_error', 'metadata_error')
                    if not (failed and mode == 'provider_error'):
                        retired.append(identity)
                    if failed:
                        ignored.append(identity)
                        break
            events += ['unbind_le', 'unmap', 'space_free']
            events += ['release_allocation_handle'] * segments + ['memory_free']
        events.append('shadow_2')
        if mode != 'table_shadow_error':
            events.append('table_memset')
            if mode != 'table_memset_error':
                events.append('window_shadow_free')
                if mode != 'window_shadow_error':
                    events.append('local_deregister')
        events += ['sorted_remove', 'map_remove', 'window_free']
    if begun:
        events += ['stream_sync', 'stream_destroy', 'set_device', 'capture_exchange']
    expected = dict(public_result=1 if mode in ('initial_shadow_error', 'table_shadow_error',
                    'table_memset_error', 'window_shadow_error', 'local_deregister_error') else 0,
                    remaining_refs=refs - int(dropped), memory_freed=dropped and refs == 1,
                    window_freed=dropped, remaining_sorted_windows=0 if dropped else 1,
                    mr_calls=calls, retired=retired, ignored_provider_results=ignored, events=events,
                    real_provider_executed=False)
    for key, value in expected.items():
        need(row[key] == value, 'oracle ' + key)


def verify(inputs, build, records, summary, evidence):
    sources = {}
    manifest = json.loads((inputs / 'sources.json').read_text())
    need(len(manifest) == 12, 'source coverage')
    for row in manifest:
        raw = (inputs / 'sources' / row['project'] / row['path']).read_bytes()
        need(hashlib.sha256(raw).hexdigest() == row['sha256'] and len(raw) == row['bytes'], 'source content')
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        tree = json.loads((inputs / 'provenance' / row['project'] / 'tree.json').read_text())
        need(blob == row['git_blob_sha'] == next(x['sha'] for x in tree['tree'] if x['path'] == row['path']), 'tree/blob')
        if row['project'] == 'nccl':
            commit = json.loads((inputs / 'provenance/nccl/commit.json').read_text())
            need(commit['sha'] == row['commit'] and commit['tree']['sha'] == tree['sha'], 'commit/tree')
        else:
            ref = json.loads((inputs / 'provenance/deepep/ref.json').read_text())
            need(ref['object']['sha'] == row['commit'] == tree['sha'], 'fixed tree request binding')
        sources[row['project'] + '/' + row['path']] = raw.decode()
    generated = (build / 'selected.cc').read_text();spec = json.loads((build / 'build.json').read_text())
    need(hashlib.sha256(generated.encode()).hexdigest() == spec['generated_sha256'], 'generated hash')
    need(len(spec['functions']) == 4 and len({r['name'] for r in spec['functions']}) == 4, 'compiled function coverage')
    need(len(evidence) == 14 and len({r['name'] for r in evidence}) == 14, 'static excerpt coverage')
    for row in spec['functions'] + evidence:
        selected = '\n'.join(sources[row['source']].splitlines()[row['line'] - 1:row['end_line']])
        need(hashlib.sha256(selected.encode()).hexdigest() == row['sha256'], 'exact source interval')
        need(all(x in selected for x in row.get('required', [])), 'required source text')
        if row in spec['functions']:
            need(generated.count(selected) == 1, 'exact original function present once')
    runtime = sources['nccl/src/dev_runtime.cc']
    need('0 == --mem->refCount' in runtime and 'candidateRegistryId' in runtime and
         'm->winFlags != winFlags' in runtime, 'backing sharing constraints')
    host_barrier = sources['deepep/csrc/comm/barrier.hpp']
    need(host_barrier.count('cudaDeviceSynchronize()') == 2 and 'launch_barrier(' in host_barrier, 'host barrier guard')
    requests = json.loads((inputs / 'requests.json').read_text())
    need(len(requests) == 6 and len({r['url'] for r in requests}) == 6, 'request coverage/no retries')
    for row in requests:
        raw = (inputs / row['response_file']).read_bytes()
        need(row['exit_code'] == 0 and row['elapsed_seconds'] <= 8.5 and len(raw) <= 2**20 and
             hashlib.sha256(raw).hexdigest() == row['response_sha256'], 'bounded source response')
    need(sum(r['bytes'] for r in manifest if r['method'] != 'fixed-cache') <= 2**20, 'new source byte limit')
    rows = [json.loads(x) for x in records.read_text().splitlines()];keys = set()
    for row in rows:
        oracle(row)
        key = tuple(row[k] for k in ('refs', 'segments', 'connections', 'mode', 'failure_segment', 'failure_slot'))
        need(key not in keys, 'duplicate condition');keys.add(key)
    expected = set()
    for refs, segments, connections in itertools.product((1, 2, 3), repeat=3):
        expected.update((refs, segments, connections, mode, -1, -1) for mode in MODES)
        expected.update((refs, segments, connections, mode, s, c) for mode in ('provider_error', 'metadata_error')
                        for s in range(segments) for c in range(connections))
    need(keys == expected and len(rows) == 432, 'all synthetic conditions')
    counts = dict(public_success_without_mr_calls=0, public_success_with_lower_error=0,
                  public_success_with_unretired_channels=0, public_success_all_retired_but_lower_error=0,
                  public_error_after_all_mrs_retired=0, public_success_ignores_sync_error=0)
    for row in rows:
        success = row['public_result'] == 0
        counts['public_success_without_mr_calls'] += int(success and row['refs'] > 1 and row['mode'] == 'success' and not row['mr_calls'])
        if success and row['ignored_provider_results']:
            counts['public_success_with_lower_error'] += 1
            key = 'public_success_all_retired_but_lower_error' if len(row['retired']) == row['segments'] * row['connections'] else 'public_success_with_unretired_channels'
            counts[key] += 1
        counts['public_error_after_all_mrs_retired'] += int(not success and len(row['retired']) == row['segments'] * row['connections'])
        counts['public_success_ignores_sync_error'] += int(success and row['mode'] == 'stream_sync_error')
    need(summary['counts'] == counts and summary['cases'] == len(rows) and
         summary['records_sha256'] == hashlib.sha256(records.read_bytes()).hexdigest(), 'independent summary')
    for key in ('legal_failure_concurrency_proven', 'production_vulnerability_proven', 'performance_benefit_proven', 'paper_innovation_established'):
        need(summary[key] is False, 'overclaim ' + key)
    rejected = []
    conditions = [
        ('success_means_all_retired', lambda r: r['mode'] == 'provider_error' and r['refs'] == 1,
         lambda r: r.update(retired=[s * 8 + c for s in range(r['segments']) for c in range(r['connections'])])),
        ('window_success_means_no_other_reference', lambda r: r['mode'] == 'success' and r['refs'] == 2,
         lambda r: r.update(remaining_refs=0, memory_freed=True)),
        ('error_means_no_mr_retired', lambda r: r['mode'] == 'table_shadow_error' and r['refs'] == 1,
         lambda r: r.update(retired=[])),
        ('provider_error_propagates_to_public', lambda r: r['mode'] == 'provider_error' and r['refs'] == 1,
         lambda r: r.update(public_result=1)),
    ]
    for kind, choose, mutate in conditions:
        row = deepcopy(next(r for r in rows if choose(r)));mutate(row)
        try:
            oracle(row)
        except AssertionError:
            rejected.append(kind)
        else:
            raise AssertionError('semantic tampering accepted: ' + kind)
    return dict(schema_version='window-lifecycle-verification/1', source_files=12, source_functions=4,
                source_excerpts=14, independent_conditions=432, independently_counted=counts,
                rejected_semantic_tampering=rejected, real_provider_executed=False,
                production_vulnerability_proven=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('inputs', 'build', 'records', 'summary', 'evidence', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.inputs, args.build, args.records, json.loads(args.summary.read_text()), json.loads(args.evidence.read_text()))
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps(result))
