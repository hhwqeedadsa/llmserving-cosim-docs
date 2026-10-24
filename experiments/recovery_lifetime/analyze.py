"""Prepare hashed old results or audit snapshot/lease lifetimes; no simulation."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

SOURCES = ('backend/rdma/remote_transactions.cc', 'backend/rdma/persistent_streams.cc',
           'backend/rdma/persistent_streams.hh', 'backend/rdma/remote_memory.hh')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    with path.open('x') as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True)+'\n')


def prepare(a):
    a.output.mkdir()
    rawdir = a.output/'raw'
    rawdir.mkdir()
    original = json.loads((a.original/'manifest.json').read_text())
    assert len(original['conditions']) == 16
    members = []
    for condition in original['conditions']:
        name = condition['name']
        assert Path(name).name == name
        source = a.original/'cases'/name/'result.json'
        assert source.stat().st_size < 8*2**20
        raw = source.read_bytes()
        target = rawdir/(name+'.json.gz')
        target.write_bytes(gzip.compress(raw, compresslevel=6, mtime=0))
        members.append(dict(name=name, fault=condition['fault'], strategy=condition['strategy'],
                            raw_bytes=len(raw), sha256=digest(raw), compressed_sha256=digest(target.read_bytes())))
    snapshots = []
    for rel in SOURCES:
        raw = (a.repo/rel).read_bytes()
        committed = subprocess.check_output(['git','show','726ab7d:'+rel],cwd=a.repo)
        assert raw == committed
        target = a.output/'source'/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(raw)
        snapshots.append(dict(path=rel,sha256=digest(raw)))
    save(a.output/'inputs.json',dict(results=members,sources=snapshots,platform_commit='726ab7d',
                                  original_manifest_sha256=digest((a.original/'manifest.json').read_bytes())))


def analyze(a):
    inputs = json.loads((a.inputs/'inputs.json').read_text())
    rows = []
    for item in inputs['results']:
        raw = gzip.decompress((a.inputs/'raw'/(item['name']+'.json.gz')).read_bytes())
        assert digest(raw) == item['sha256']
        r = json.loads(raw)
        t, transport = r['transactions'], r['transactions']['transport']
        operations = {op['id']:op for op in t['operations']}
        captures = [e for e in t['source_events'] if e['kind']=='captured']
        assert Counter(e['operation'] for e in captures) == Counter({i:1 for i in operations})
        assert all(len(bytes.fromhex(e['hex'])) == e['bytes'] for e in captures)
        target = [e for e in t['target_events'] if not e['duplicate']]
        assert len({(e['connection'],e['sequence']) for e in target}) == len(target)
        streams = {s['id']:s for s in transport['streams']}
        repeated, payload = 0, {}
        for packet in transport['source_payload_packets']:
            key = packet['stream'],packet['sequence']
            if key in payload:
                assert payload[key] == packet['hex']
                repeated += 1
            else:
                payload[key] = packet['hex']
        leases = t['active_source_leases']
        assert len(leases)==1
        failed = operations[leases[0]['operation']]
        assert failed['completed_ns'] == -1
        body_bytes = sum(e['bytes'] for e in captures)
        carrier_bytes = sum(s['submitted_bytes'] for s in streams.values())
        assert carrier_bytes == body_bytes+32*len(captures)
        cache_bytes = sum(len(bytes.fromhex(e['body_hex']))+32 for e in target)
        rows.append(dict(case=item['name'],fault=item['fault'],strategy=item['strategy'],
            captured_operations=len(captures),source_capture_times_ns=sorted({e['time_ns'] for e in captures}),
            source_lease_bytes=sum(e['bytes'] for e in leases),failed_operation_applied_ns=failed['applied_ns'],
            failed_operation_completed_ns=failed['completed_ns'],repeated_source_packet_offsets=repeated,
            repeated_packets_equal_original_payload=True,operation_body_content_bytes=body_bytes,
            carrier_work_payload_content_bytes=carrier_bytes,target_saved_frame_content_bytes=cache_bytes,
            three_container_content_bytes=body_bytes+carrier_bytes+cache_bytes,
            accounting_scope='code-derived retained vector contents at report time; not capacity/RSS/HBM or network volume'))
    output = dict(cases=rows,new_network_executions=0,hardware_memory_measured=False,
                  source_lifetime_contract='capture once at publication; lease release on transaction completion',
                  finite_buffer_performance_gate='not_ready_without_explicit_buffer_and_old_access_exit_model')
    save(a.output,output)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    s=p.add_subparsers(dest='command',required=True)
    prep=s.add_parser('prepare')
    for name in ('repo','original','output'):
        prep.add_argument('--'+name,type=Path,required=True)
    run=s.add_parser('analyze')
    for name in ('inputs','output'):
        run.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    dict(prepare=prepare,analyze=analyze)[a.command](a)


if __name__=='__main__':main()
