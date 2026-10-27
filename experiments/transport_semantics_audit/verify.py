"""Independent byte-stream reconstruction and feedback-direction checks."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def check(result, frontend, report):
    t = result['transactions']['transport']
    frames = {(f['stream'], f['sequence']): f for f in report['frames']}
    assert len(frames) == len(report['frames']) == len(t['work'])
    received = defaultdict(dict)
    for p in t['source_payload_packets']:
        body = bytes.fromhex(p['hex'])
        for index, value in enumerate(body, p['sequence']):
            assert index not in received[p['stream']], 'unexpected source retransmission in frozen healthy cases'
            received[p['stream']][index] = value
    original_packets = {(r[1], r[5]): r for r in t['source_packets']}
    assert len(original_packets) == len(t['source_packets']) == len(t['source_payload_packets'])
    parsed = []
    for stream, byte_map in sorted(received.items()):
        assert set(byte_map) == set(range(len(byte_map)))
        raw = bytes(byte_map[i] for i in range(len(byte_map)))
        offset = 0
        while offset < len(raw):
            assert raw[offset:offset+4] == b'MRT1'
            verb = int.from_bytes(raw[offset+4:offset+8], 'big')
            seq = int.from_bytes(raw[offset+8:offset+16], 'big')
            length = int.from_bytes(raw[offset+28:offset+32], 'big')
            end = offset+32+length
            assert 0 < length <= 32768 and end <= len(raw)
            saved = frames[stream, seq]
            work, = [w for w in t['work'] if w['stream'] == stream and w['begin'] == offset]
            assert end == work['end'] and saved['work'] == work['id']
            assert saved['body_bytes'] == length and saved['framed_bytes'] == end-offset and saved['verb'] == verb
            for a, b in [('published_ns','published_ns'),('accepted_ns','accepted_ns'),('acked_ns','acked_ns')]:
                assert saved[a] == work[b]
            op, = [o for o in result['transactions']['operations'] if o['connection'] == stream//2 and o['sequence'] == seq]
            assert saved['native_operation_completed_ns'] == op['completed_ns']
            assert saved['operation'] == frontend['native_tasks'][str(op['id'])]['modern_operation']
            payload = raw[offset+32:end]
            if verb == 3:
                assert stream % 2 == 1 and length == 8
                assert int.from_bytes(payload, 'big') == op['returned_value']
                assert op['response_ns'] == work['accepted_ns'] and op['completed_ns'] >= op['response_ns']
                assert saved['category'] == 'atomic_reply'
            else:
                assert stream % 2 == 0 and verb in (1,2)
                target, = [e for e in result['transactions']['target_events'] if e['connection'] == stream//2 and e['sequence'] == seq]
                assert bytes.fromhex(target['body_hex']) == payload and target['verb'] == verb
                assert op['request_acked_ns'] == work['acked_ns'] and op['applied_ns'] == work['accepted_ns']
                assert saved['category'] in (('atomic_request',) if verb == 2 else ('token_data','put_control'))
            packets = [p for p in t['source_packets'] if p[1] == stream and offset <= p[5] < end]
            assert len(packets) == saved['source_packets']
            mtu=report['summary']['actual_payload_MTU']
            assert saved['body_only_packet_count']==(length+mtu-1)//mtu
            assert len(packets)==(end-offset+mtu-1)//mtu
            assert sorted(p[7] for p in packets) == sorted(saved['source_packet_uids'])
            assert sum(p[6] for p in packets) == end-offset
            for p in packets:
                assert p[5]+p[6] <= end
                source = next(v for v in t['source_payload_packets'] if (v['stream'],v['sequence']) == (stream,p[5]))
                assert source['time_ns'] == p[0] and len(bytes.fromhex(source['hex'])) == p[6]
            parsed.append((stream, seq, verb, length, saved, work))
            offset = end
    cap = result['packet_capture']
    headers = cap['wire_headers']
    identities = {}
    for index, event in enumerate(cap['events']):
        h = headers['headers'][headers['event_header_indices'][index]]
        assert h[0] == event[4] and h[3] == event[6]
        identities[event[4]] = h
    total = Counter()
    categories = defaultdict(Counter)
    streams = {s['id']: s for s in t['streams']}
    for stream, seq, verb, length, saved, work in parsed:
        ids = set(saved['source_packet_uids'])
        for uid in ids:
            h = identities[uid]
            assert h[1:3] == [streams[stream]['source_ipv4'], streams[stream]['destination_ipv4']]
        for uid in saved['ACK_packet_uids']:
            h = identities[uid]
            assert h[1:3] == [streams[stream]['destination_ipv4'], streams[stream]['source_ipv4']] and h[3] == 252
        assert len(saved['ACK_packet_uids']) == len(ids)
        data = [e for e in cap['events'] if e[3] == 0 and e[4] in ids]
        ack = [e for e in cap['events'] if e[3] == 0 and e[4] in saved['ACK_packet_uids']]
        for key, value in dict(physical_data_TX=len(data),physical_data_TX_bytes=sum(e[8] for e in data),
                               physical_ACK_TX=len(ack),physical_ACK_TX_bytes=sum(e[8] for e in ack)).items():
            assert saved[key] == value
        c = categories[saved['category']]
        c.update({k:saved[k] for k in ('body_bytes','framed_bytes','source_packets','physical_data_TX','physical_data_TX_bytes','physical_ACK_TX','physical_ACK_TX_bytes')})
        c['frames'] += 1
        total.update(body_bytes=length,framed_bytes=length+32,frames=1,function_header_bytes=32,source_packets=len(ids))
    for name, values in categories.items():
        assert dict(values) == report['summary']['categories'][name]
    for key,value in total.items():
        assert report['summary'][key] == value
    summary=report['summary']
    assert summary['framing_only_source_packet_reduction'] == 0 and summary['body_only_packet_count'] == total['source_packets']
    assert summary['function_header_fraction_of_framed_bytes'] == 32*len(parsed)/total['framed_bytes']
    app = max(e['time_ns'] for e in frontend['events'] if e['kind']=='application_stage_complete')
    assert summary['application_stage_complete_ns'] == app
    assert summary['all_operations_complete_ns'] == max(o['completed_ns'] for o in result['transactions']['operations'])
    replies = [row for row in parsed if row[2] == 3]
    assert summary['reply_ACK_after_application'] == sum(row[5]['acked_ns'] > app for row in replies)
    assert summary['reply_ACK_after_own_operation'] == sum(row[5]['acked_ns'] > row[4]['native_operation_completed_ns'] for row in replies)
    assert summary['latest_reply_ACK_ns'] == max(row[5]['acked_ns'] for row in replies) == summary['physical_drained_ns'] == result['finish_ns']
    assert summary['atomic_origin_data_and_ACK_packets'] == 2*sum(row[4]['source_packets'] for row in parsed if row[2] in (2,3))
    assert summary['all_origin_data_and_ACK_packets'] == 2*total['source_packets']
    assert summary['CNP_physical_TX']==sum(e[3]==0 and e[6]==255 for e in cap['events'])
    assert summary['physical_gpu_count']==result['gpu_count']
    assert summary['active_network_GPU_ids']==sorted({p[2] for p in t['source_packets']}|{p[3] for p in t['source_packets']})
    return dict(frames=len(parsed),source_packets=total['source_packets'],feedback_directions_checked=True)


def execute(root, output):
    inputs=json.loads((root/'inputs.json').read_bytes())
    for row in inputs['files']+inputs['sources']:
        assert hashlib.sha256((root/row['path']).read_bytes()).hexdigest()==row['sha256']
    analysis=json.loads((root/'analysis/analysis.json').read_bytes());conditions={};rejected=[]
    for name in ('single','quad'):
        base=root/f'truth/batch4/{name}/modern'
        result=json.loads((base/'result.json').read_bytes());front=json.loads((base/'modern-execution.json').read_bytes())
        report=analysis['conditions'][name];conditions[name]=check(result,front,report)
        if name=='single':
            for label,field in [('body','body_bytes'),('work_boundary','work'),('stream','stream'),('time','acked_ns')]:
                broken=deepcopy(report);broken['frames'][0][field]+=1
                try:check(result,front,broken)
                except (AssertionError,KeyError,ValueError):rejected.append(label)
                else:raise AssertionError('corruption accepted: '+label)
            broken=deepcopy(result);body=broken['transactions']['transport']['source_payload_packets'][0]
            raw=bytearray.fromhex(body['hex']);raw[-1]^=1;body['hex']=raw.hex()
            try:check(broken,front,report)
            except (AssertionError,KeyError,ValueError):rejected.append('actual_payload')
            else:raise AssertionError('payload corruption accepted')
    value=dict(passed=True,conditions=conditions,rejected_corruption_categories=rejected,
               new_networks=0,hardware_or_RoCE_equivalence_proven=False)
    output.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    print(json.dumps(value,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();execute(a.run.resolve(),a.output.resolve())
