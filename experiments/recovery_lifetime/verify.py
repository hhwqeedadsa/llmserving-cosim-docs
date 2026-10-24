"""Independent raw-result audit; no import from the analysis implementation."""
import argparse
from collections import defaultdict
import copy
import gzip
import hashlib
import json
from pathlib import Path


def check(inputs,summary):
    meta=json.loads((inputs/'inputs.json').read_text())
    assert summary['new_network_executions']==0 and not summary['hardware_memory_measured']
    rows={r['case']:r for r in summary['cases']}
    assert set(rows)=={r['name'] for r in meta['results']}
    totals=defaultdict(int)
    for item in meta['results']:
        compressed=(inputs/'raw'/(item['name']+'.json.gz')).read_bytes()
        assert hashlib.sha256(compressed).hexdigest()==item['compressed_sha256']
        raw=gzip.decompress(compressed)
        assert len(raw)==item['raw_bytes'] and hashlib.sha256(raw).hexdigest()==item['sha256']
        result=json.loads(raw);t=result['transactions'];r=rows[item['name']]
        byop=defaultdict(list)
        for e in t['source_events']:byop[e['operation']].append(e)
        body=0;capture_times=set()
        for op in t['operations']:
            captured=[e for e in byop[op['id']] if e['kind']=='captured']
            assert len(captured)==1 and captured[0]['time_ns']==op['published_ns']
            body+=len(bytes.fromhex(captured[0]['hex']));capture_times.add(captured[0]['time_ns'])
            if op['completed_ns']>=0:
                assert any(e['kind']=='released' and e['time_ns']==op['completed_ns'] for e in byop[op['id']])
            else:assert not any(e['kind']=='released' for e in byop[op['id']])
        sent=sum(w['bytes'] for w in t['transport']['work'])
        saved=sum(32+len(bytes.fromhex(e['body_hex'])) for e in t['target_events'] if not e['duplicate'])
        assert r['operation_body_content_bytes']==body
        assert r['carrier_work_payload_content_bytes']==sent==body+32*len(t['operations'])
        assert r['target_saved_frame_content_bytes']==saved
        assert r['three_container_content_bytes']==body+sent+saved
        assert r['source_lease_bytes']==sum(e['bytes'] for e in t['active_source_leases'])==14336
        assert r['source_capture_times_ns']==sorted(capture_times)
        pending=[op for op in t['operations'] if op['completed_ns']<0]
        assert len(pending)==1 and pending[0]['id']==t['active_source_leases'][0]['operation']
        assert r['failed_operation_applied_ns']==pending[0]['applied_ns']
        assert (pending[0]['applied_ns']>=0)==(item['fault']=='ack_tail')
        packets=defaultdict(list)
        for p in t['transport']['source_payload_packets']:
            packets[p['stream'],p['sequence']].append(bytes.fromhex(p['hex']))
        repeats=0
        for versions in packets.values():
            assert len(set(versions))==1
            repeats+=len(versions)-1
        assert r['repeated_source_packet_offsets']==repeats
        totals['repeated_source_packet_offsets']+=repeats
        totals['failed_but_target_applied']+=pending[0]['applied_ns']>=0
        totals['retained_source_lease_bytes']+=r['source_lease_bytes']
        totals['three_container_content_bytes']+=body+sent+saved
    for source in meta['sources']:
        assert hashlib.sha256((inputs/'source'/source['path']).read_bytes()).hexdigest()==source['sha256']
    return dict(cases=len(rows),**totals)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','summary','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();summary=json.loads(a.summary.read_text());totals=check(a.inputs,summary)
    rejected=[]
    for key in ('source_lease_bytes','three_container_content_bytes'):
        changed=copy.deepcopy(summary);changed['cases'][0][key]+=1
        try:check(a.inputs,changed)
        except AssertionError:rejected.append(key)
        else:raise AssertionError('tampered accounting accepted')
    with a.output.open('x') as stream:
        stream.write(json.dumps(dict(passed=True,independent_raw_result_audit=True,
            totals=totals,tamper_rejections=rejected,scope='functional model and code-derived contents only'),
            indent=2,sort_keys=True)+'\n')


if __name__=='__main__':main()
