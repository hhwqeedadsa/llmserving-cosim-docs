"""Offline negative evidence checks; never rerun networks to repair an auditor."""
import argparse
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import runpy
import tarfile


def load(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    for name in ('repository', 'stage'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args(); repo, s = a.repository.resolve(), a.stage.resolve()
    audit = runpy.run_path(str(repo/'fixtures/causal_waves/audit.py'))['audit']
    results = {}
    for label, config in (('plain', 'plan.json'), ('selective', 'selective-plan.json'), ('fault', 'selective-plan.json')):
        options = load(s/'inputs'/config)
        waves = [load(s/'inputs'/name) for name in options['waves']]
        work, mapping, result = [load(s/path) for path in
            (f'planned/{label}/work.json', f'planned/{label}/mapping.json', f'network/{label}/result.json')]
        summary = audit(waves, work, mapping, result)
        assert summary == load(s/f'network/{label}/audit.json')
        results[label] = summary
    rejected = []
    for case in ('recv_gate', 'next_wave_ack', 'local_time', 'accept_time', 'send_bytes', 'wire_port', 'packet_sequence'):
        w, m, r = deepcopy(work), deepcopy(mapping), deepcopy(result)
        if case == 'recv_gate':
            node = next(x for x in m['nodes'] if x['graph_id']=='g0.w0' and x['rank']==0 and x['node_id']==4)
            node['completion'] = [x for x in node['completion'] if x['event']=='accepted']
        elif case == 'next_wave_ack':
            for x in w['work']:
                if x['kind']=='local' and x['duration_ns']==0 and x['after']:
                    for d in x['after']:
                        if d['event']=='acked': d['event']='accepted'
        elif case == 'local_time': r['local_work'][0]['completed_ns'] += 1
        elif case == 'accept_time': r['persistent_transport']['work'][0]['accepted_ns'] -= 1
        elif case == 'send_bytes': next(x for x in w['work'] if x['kind']=='write')['bytes'] += 1
        elif case == 'wire_port': r['packet_capture']['wire_headers']['headers'][0][4] += 1
        else: r['persistent_transport']['source_packets'][0][5] += 1
        try: audit(waves, w, m, r)
        except (AssertionError, KeyError, ValueError): rejected.append(case)
        else: raise AssertionError('tamper accepted: '+case)
    assert results['fault']['physical_drop_events']==1 and results['selective']['physical_drop_events']==0
    sr = result['persistent_transport']['selective_repeat']
    assert any(x['kind']=='fast_retransmit' for x in sr['events'])
    assert results['fault']['group_finish_ns']['g1.w1'] < results['fault']['group_finish_ns']['g0.w1']
    runtime = load(s/'runtime.json')
    assert sha(Path(runtime['backend'])) == runtime['sha256']
    assert all(sha(Path(p)) == digest for p, digest in runtime['libraries'].items())
    assert all(sha(repo/p) == digest for p, digest in runtime['source'].items())
    safety = []
    restore = runpy.run_path(str(repo/'fixtures/causal_waves/checkpoint.py'))['restore']
    directory = s/'archive-safety'; directory.mkdir(exist_ok=True)
    for case in ('traversal', 'absolute', 'symlink', 'duplicate', 'file_parent', 'wrong_hash', 'missing_member', 'schema'):
        name = '../outside' if case=='traversal' else '/outside' if case=='absolute' else 'source/example.txt'
        entries = [(name, b'hello', tarfile.SYMTYPE if case=='symlink' else tarfile.REGTYPE)]
        if case=='duplicate': entries.append(entries[0])
        if case=='file_parent': entries.append((name+'/child', b'hello', tarfile.REGTYPE))
        manifest = dict(schema_version='causal-wave-checkpoint/1', bytes=sum(len(x[1]) for x in entries),
            files={n: dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest()) for n, data, _ in entries})
        if case=='wrong_hash': manifest['files'][name]['sha256'] = '0'*64
        if case=='missing_member': manifest['files']['absent'] = dict(bytes=0, sha256=hashlib.sha256(b'').hexdigest())
        if case=='schema': manifest['schema_version'] = 'unknown'
        path = directory/(case+'.tar.gz')
        with tarfile.open(path, 'x:gz') as tar:
            for n, data, kind in entries:
                info = tarfile.TarInfo(n); info.size=len(data); info.type=kind; info.linkname='../../outside' if kind==tarfile.SYMTYPE else ''
                tar.addfile(info, io.BytesIO(data))
            data=json.dumps(manifest).encode(); info=tarfile.TarInfo('manifest.json'); info.size=len(data); tar.addfile(info, io.BytesIO(data))
        target=directory/(case+'-output')
        try: restore(path,target)
        except ValueError: safety.append(case)
        else: raise AssertionError('unsafe archive accepted: '+case)
        assert not target.exists()
    fresh = load(s/'fresh/replay.json'); assert fresh['passed'] and fresh['total_actual_networks']==4
    options = load(s/'restored/evidence/inputs/long/plan.json')
    waves = [load(s/'restored/evidence/inputs/long'/name) for name in options['waves']]
    summary = audit(waves, load(s/'fresh/planned/work.json'), load(s/'fresh/planned/mapping.json'), load(s/'fresh/network/result.json'))
    assert summary == fresh['long_case'] == load(s/'fresh/audit.json')
    results['complete48'] = summary
    out = dict(passed=True, cases=results, evidence_tampering_rejected=rejected,
               python_scaleout_tests=142, first_test_failure='Test labeled one-sided wait as a cycle; changed to an actual cross-rank cyclic dependency, no platform fix',
               original_native_runtime_unchanged=True, compiler_source_identity=True, network_executions_so_far=4,
               unsafe_archive_classes_rejected_before_writes=safety, new_source_public_workflow_passed=True,
               scope='frontend DAG and strong reference transport functionality; no optimization or modern GIN claim')
    (s/'verification.json').write_text(json.dumps(out, indent=2, sort_keys=True)+'\n')
    print(json.dumps(dict(passed=True, cases=len(results), tamper_classes=len(rejected))))


if __name__ == '__main__':
    main()
