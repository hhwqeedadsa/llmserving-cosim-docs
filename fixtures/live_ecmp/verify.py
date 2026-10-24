"""Close functional evidence; no native execution or network retries."""
import argparse
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tarfile

from fixtures.live_ecmp.audit import audit
from fixtures.live_ecmp.checkpoint import restore
from fixtures.live_ecmp.run import normalized_default


def load(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, x): p.write_text(json.dumps(x, indent=2)+'\n')


def archive_rejections(root):
    root.mkdir()
    # Every invalid archive must be rejected before creating its output root.
    def file(name, body=b'x'):
        info = tarfile.TarInfo(name); info.size = len(body); return info, body
    regular = file('evidence/file')
    good_manifest = dict(schema_version='moe-live-ecmp-checkpoint/1', files={
        'evidence/file': dict(bytes=1, sha256=hashlib.sha256(b'x').hexdigest())}, native_runtime_bundled=False)
    manifest = file('manifest.json', json.dumps(good_manifest).encode())
    symlink = tarfile.TarInfo('evidence/link'); symlink.type = tarfile.SYMTYPE; symlink.linkname = '/tmp'
    cases = {
        'absolute': [file('/unsafe'), manifest],
        'parent': [file('../unsafe'), manifest],
        'symlink': [(symlink, b''), manifest],
        'duplicate': [regular, regular, manifest],
        'parent-file-conflict': [file('evidence'), regular, manifest],
        'unlisted': [regular, file('extra'), manifest],
        'wrong-hash': [file('evidence/file', b'y'), manifest],
        'backslash': [file('evidence\\file'), manifest],
    }
    for name, rows in cases.items():
        path = root/(name+'.tar.gz'); target = root/(name+'-output')
        with tarfile.open(path, 'x:gz') as ar:
            for info, body in rows: ar.addfile(info, io.BytesIO(body))
        try: restore(SimpleNamespace(archive=path, output=target))
        except AssertionError: pass
        else: raise AssertionError('unsafe archive accepted: '+name)
        assert not target.exists(), 'unsafe archive wrote output before verification'
    return list(cases)


def main():
    p = argparse.ArgumentParser()
    for k in ('repository', 'stage', 'output'): p.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args(); repo = a.repository.resolve(); s = a.stage.resolve(); assert not a.output.exists()
    build = load(s/'build.json')
    for relative, digest in build['sources'].items(): assert sha(repo/relative) == digest
    for path, digest in {**build['inputs'], **build['libraries']}.items(): assert sha(Path(path)) == digest
    for name, digest in build['binaries'].items(): assert sha(s/'bin'/name) == digest
    inputs = s/load(s/'run.json')['inputs_directory']; topo = load(s/'topology/topology.json'); work = load(inputs/'work.json')
    primary = load(s/'audit.json'); assert primary['passed'] and load(s/'native-reject.json')['passed']
    comparison = normalized_default(load(s/'network/default-old/result.json'), load(s/'network/default-new/result.json'), build['libraries'])
    assert (s/'fresh/primary-audit.json').read_bytes() == (s/'audit.json').read_bytes()
    fresh = load(s/'fresh/replay.json'); assert fresh['passed'] and fresh['actual_networks_total'] == 6
    fresh_source = s/'restored/source/examples/scaleout_live_ecmp'
    cases = {name: (topo, load(inputs/filename), work, load(s/'network'/name/'result.json')) for name, filename in
             [('default-old', 'default.json'), ('default-new', 'default.json'), ('live', 'protocol.json'), ('overlap', 'overlap.json'), ('zero', 'zero.json')]}
    cases['fresh-empty'] = (load(s/'fresh/topology/topology.json'), load(fresh_source/'protocol.json'),
        load(fresh_source/'work-empty-route.json'), load(s/'fresh/network/result.json'))
    reports = {}; physical_sends = {}
    for name, args in cases.items():
        # JSON object keys deserialize as strings (stream IDs included).
        # Compare the complete serialized structure, not Python int/string keys.
        reports[name] = json.loads(json.dumps(audit(*args)))
        expected = fresh['fresh_case'] if name == 'fresh-empty' else primary['cases'][name]
        assert reports[name] == expected, name
        result = args[-1]; capture = result['packet_capture']; events = [dict(zip(capture['columns'], row)) for row in capture['events']]
        count = 0
        for failure in result['link_failures']['events']:
            if failure['kind'] != 'link_down': continue
            down = {(failure['src'], failure['src_port']), (failure['dst'], failure['dst_port'])}
            assert not any(e['event'] == 0 and e['time_ns'] >= failure['time_ns'] and (e['node'], e['port']) in down for e in events)
            count += sum(e['event'] == 0 for e in events)
        physical_sends[name] = dict(all_post_failure_tx_avoid_failed_ports=True, tx_examined_per_failure=count)
    assert reports['fresh-empty']['local_drops']['no_live_old_candidate'] == 31
    mutations = []
    def rejected(label, args, change):
        bad = deepcopy(args[-1]); change(bad)
        try: audit(*args[:-1], bad)
        except (AssertionError, KeyError): mutations.append(label)
        else: raise AssertionError('undetected evidence mutation: '+label)
    rejected('local-candidate-count', cases['live'], lambda r: r['link_failures']['local_failover']['events'][0].__setitem__('installed_next_hops', 999))
    rejected('global-overlap-exclusion', cases['overlap'], lambda r: next(v for v in r['link_failures']['events'] if v['kind']=='routes_updated').__setitem__('excluded_directed_ports', 4))
    # The route oracle reconstructs topology; compare the global exclusion
    # summary explicitly as it is not used as an input to path selection.
    def wrong_enqueue(r):
        c = r['packet_capture']; idx = c['columns'].index('port'); kind = c['columns'].index('event'); node = c['columns'].index('node')
        next(row for row in c['events'] if row[kind] == 3 and row[node] >= r['gpu_count'])[idx] = 127
    rejected('actual-enqueue-port', cases['live'], wrong_enqueue)
    rejected('actual-ack-prefix', cases['live'], lambda r: r['persistent_transport']['work'][0].__setitem__('acked_ns', -1))
    rejected('local-event-node', cases['live'], lambda r: r['link_failures']['local_failover']['events'][0].__setitem__('node', 17))
    rejected('physical-arrival-time', cases['fresh-empty'], lambda r: next(row for row in r['packet_capture']['events'] if row[r['packet_capture']['columns'].index('event')] == 1).__setitem__(r['packet_capture']['columns'].index('time_ns'), -1))
    safety = archive_rejections(s/'unsafe-archive-cases')
    save(a.output, dict(passed=True, actual_networks=6, network_reexecutions=0, reports=reports,
        default_comparison=comparison, physical_sends=physical_sends, native_preallocation_rejections=9,
        evidence_mutations_rejected=mutations, unsafe_archives_rejected_before_write=safety,
        fresh_primary_report_byte_identical=True, compiled_backend_sources_and_external_runtime_hashes_verified=True,
        platform_functional_acceptance_complete=True, overall_paper_goal_complete=False,
        scope='single-plane artificial local failover functionality; not modern inference, calibration or research gains'))


if __name__ == '__main__': main()
