"""Independent small default-stream receive/ACK/physical completion audit."""
import argparse
from collections import defaultdict, deque
from pathlib import Path
from fixtures.nic_wakeup.stage import load,save
from fixtures.nack_grace_edges.wire_fifo import audit as fifo_audit


def audit(r,w,libraries):
    t=r['persistent_transport'];c=r['packet_capture'];assert r['passed'] and r['drained'] and t['closed'] and t['physical_drained']
    assert c['complete'] and not c['omitted_events'] and 'selective_repeat' not in t
    assert len([x for x in r['loaded_libraries'] if 'libns3' in Path(x).name])==8
    assert all(Path(x).parent.resolve()==libraries.resolve() for x in r['loaded_libraries'] if 'libns3' in Path(x).name)
    streams={s['id']:s for s in t['streams']};ports={(p['node'],p['port']):p for p in r['ports']}
    by_port={s['wire_port']:s for s in streams.values()};wire=c['wire_headers']
    headers=[dict(zip(wire['columns'],row)) for row in wire['headers']]
    physical=defaultdict(deque);receive=defaultdict(list);acks=defaultdict(list);prefix=defaultdict(int);source_bytes=0
    for row,index in zip(c['events'],wire['event_header_indices']):
        e=dict(zip(c['columns'],row));n=e['node'];p=e['port'];peer=ports[n,p]['peer'];kind=e['event'];now=e['time_ns']
        identity=e['packet_uid'],e['ip_protocol'],e['sequence'],e['model_bytes']
        if kind==0:physical[n,peer,identity].append((now,ports[n,p]))
        if kind in (1,2):
            assert kind==1 and physical[peer,n,identity]
            sent,port=physical[peer,n,identity].popleft()
            assert now==sent+round(e['model_bytes']*8*10**9/port['rate_bps'])+port['delay_ns']
        assert kind!=5
        if e['ip_protocol'] not in (17,252):continue
        # Default (non-recovery) captures do not project a stream message_id.
        # Bind the actual serialized ports, rather than dropping those frames.
        h=headers[index];assert h['packet_uid']==e['packet_uid'] and h['ip_protocol']==e['ip_protocol']
        s=by_port[h['source_port']];sid=s['id'];assert h['destination_port']==s['wire_port']
        source,destination=(s['src'],s['dst']) if e['ip_protocol']==17 else (s['dst'],s['src'])
        assert (h['ip_src'],h['ip_dst'])==(0x0b000001+32*source,0x0b000001+32*destination)
        if e['ip_protocol']==17:
            if kind==0 and n==s['src']:source_bytes+=e['data_payload_bytes']
            if kind==1 and n==s['dst']:
                assert e['sequence']==prefix[sid];prefix[sid]+=e['data_payload_bytes'];receive[sid].append((now,prefix[sid]))
        elif kind==1 and n==s['src']:acks[sid].append((now,e['sequence']))
    assert not any(physical.values()) and source_bytes==10000 and prefix=={2:7000,8:3000}
    remote={x['id']:x for x in t['work']};assert set(remote)=={0,1,3}
    for x in remote.values():
        assert x['accepted_ns']==next(now for now,p in receive[x['stream']] if p>=x['end'])
        assert x['acked_ns']==next(now for now,p in acks[x['stream']] if p>=x['end'])
    local=next(x for x in r['local_work'] if x['id']==2)
    assert local['start_ns']==remote[0]['accepted_ns'] and local['completed_ns']==local['start_ns']+100
    assert remote[3]['ready_ns']==max(remote[0]['acked_ns'],local['completed_ns'])
    assert sum(x['bytes'] for x in remote.values())==sum(x.get('bytes',0) for x in w['work'])==10000
    return dict(passed=True,source_payload_bytes=source_bytes,remote_writes=3,local_operations=1,
        physical_pairing=True,actual_receive_and_ack_dependencies=True,all_eight_libraries_loaded_from_restored_root=True,
        fifo_and_serialization=fifo_audit(r),scope='small public default stream executed once from fresh source/runtime root; not a clean build or performance calibration')


def main():
    p=argparse.ArgumentParser()
    for k in ('result','work','libraries','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();save(a.output,audit(load(a.result),load(a.work),a.libraries))


if __name__=='__main__':main()
