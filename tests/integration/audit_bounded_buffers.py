"""Independently reconstruct pool capacity, ACK releases and actual source bytes."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
import json
from pathlib import Path
import struct

from audit_session_control_alarms import audit as memory_and_wire_audit


def read(path):return json.loads(path.read_text())
def check(ok,why):
    if not ok:raise AssertionError(why)


def pool_audit(folder,tamper=None):
    result=read(folder/'result.json');initial=read(folder/'initial.json')
    t=result['transactions'];transport=t['transport'];pool=transport['payload_pool'];cap=pool['capacity_bytes_per_source']
    if tamper=='early_release':next(e for e in pool['events'] if e['kind']=='released')['time_ns']-=1
    if tamper=='peak':pool['owners'][0]['peak_bytes']+=1
    model=initial['buffer_model'];check(cap==model['transport_bytes_per_source'],'pool cap differs from input')
    connections={c['id']:c for c in initial['connections']}
    streams={s['id']:s for s in transport['streams']};works={w['id']:w for w in transport['work']}
    ops={(op['connection'],op['sequence']):op for op in t['operations']}
    captures={e['operation']:e for e in t['source_events'] if e['kind']=='captured'}
    capture_count=Counter(e['operation'] for e in t['source_events'] if e['kind']=='captured')
    check(all(n==1 for n in capture_count.values()),'source captured more than once')
    source_memory={(w['owner'],w['key']):(w['address'],bytes.fromhex(w['hex'])) for w in initial['windows']}
    for e in captures.values():
        address,body=source_memory[e['owner'],e['key']];offset=e['address']-address
        check(body[offset:offset+e['bytes']]==bytes.fromhex(e['hex']),'source snapshot differs from initial unchanged source memory')
    source_bytes=defaultdict(dict)
    for packet in transport['source_payload_packets']:
        stream=source_bytes[packet['stream']]
        for offset,byte in enumerate(bytes.fromhex(packet['hex']),packet['sequence']):
            if offset in stream:check(stream[offset]==byte,'retransmission changed source payload')
            stream[offset]=byte
    caplog=result['packet_capture'];headers=caplog['wire_headers'];ack_receives=[]
    check(caplog['complete'] and caplog['omitted_events']==0,'packet capture incomplete')
    for row,index in zip(caplog['events'],headers['event_header_indices']):
        event=dict(zip(caplog['columns'],row));header=dict(zip(headers['columns'],headers['headers'][index]))
        if event['event']==1 and event['ip_protocol'] in (252,253):ack_receives.append((event,header))
    for w in works.values():
        payload=bytes(source_bytes[w['stream']][i] for i in range(w['begin'],w['end']))
        magic,verb,seq,address,key,length=struct.unpack('>IIQQII',payload[:32])
        check(magic==0x4d525431 and verb==1 and len(payload)==length+32,'invalid source frame')
        op=ops[w['stream']//2,seq];captured=captures[op['id']]
        check(captured['time_ns']==op['published_ns']==w['ready_ns'],'captured before pool publication')
        check(payload[32:]==bytes.fromhex(captured['hex']),'carrier snapshot differs from captured source')
    for op in t['operations']:
        check((op['published_ns']>=0)==(op['id'] in captures),'waiting operation was captured')
    held={};used=Counter();peak=Counter();acquired=set();released=set()
    for event in pool['events']:
        source,identity=event['source'],event['work'];w=works[identity];s=streams[w['stream']]
        check(event['bytes']==w['bytes'] and event['source']==s['src'] and event['stream']==w['stream'],'pool owner/work mismatch')
        if event['kind']=='acquired':
            check(identity not in acquired and event['time_ns']==w['ready_ns'],'false pool acquisition')
            acquired.add(identity);held[identity]=dict(source=source,stream=w['stream'],work=identity,bytes=w['bytes'])
            used[source]+=w['bytes'];peak[source]=max(peak[source],used[source])
            check(used[source]<=cap,'source pool overcommitted')
        else:
            check(event['kind']=='released' and identity in held and identity not in released,'duplicate or unknown release')
            check(w['acked_ns']>=0 and event['time_ns']==w['acked_ns'],'payload released before full cumulative ACK')
            matching=[e for e,h in ack_receives if e['node']==source and e['port']==s['source_nic'] and
                e['time_ns']==event['time_ns'] and e['sequence']>=w['end'] and h['source_port']==s['wire_port'] and h['destination_port']==s['wire_port']]
            check(bool(matching),'pool release has no actual bound source ACK receive')
            released.add(identity);del held[identity];used[source]-=w['bytes']
        check(used[source]==event['used_bytes'] and used[source]>=0,'false running pool usage')
    check(acquired==set(works),'missing pool acquisition')
    check(released=={i for i,w in works.items() if w['acked_ns']>=0},'ACKed work not reclaimed or failed work released')
    check(sorted(held.values(),key=lambda x:x['work'])==sorted(pool['held'],key=lambda x:x['work']),'remaining payload owners differ')
    check({r['source']:(r['used_bytes'],r['peak_bytes']) for r in pool['owners']}==
          {s:(used[s],peak[s]) for s in used},'false pool peak/terminal usage')
    registered=Counter();cached=Counter();lease=Counter();local_peak=Counter();active_local={}
    for w in initial['windows']:registered[w['owner']]+=len(bytes.fromhex(w['hex']))
    for e in t['target_events']:
        if not e['duplicate']:cached[connections[e['connection']]['dst']]+=32+len(bytes.fromhex(e['body_hex']))
    for e in t['active_source_leases']:lease[e['owner']]+=e['bytes']
    for e in t['local_memory_events']:
        if e['kind']=='captured':
            size=len(bytes.fromhex(e['hex']));active_local[e['operation']]=(e['source']['owner'],size)
            local_peak[e['source']['owner']]=max(local_peak[e['source']['owner']],size)
        if e['kind']=='released':active_local.pop(e['operation'])
    current=Counter()
    for owner,size in active_local.values():current[owner]+=size
    retention=t['buffer_retention'];check(retention['config']==model and retention['retained_operation_body_bytes']==0,'hidden operation body copy')
    for row in retention['owners']:
        owner=row['owner'];scratch=max((e['bytes'] for e in captures.values() if e['owner']==owner),default=0)
        check(row['registered_bytes']==registered[owner]<=model['registered_bytes_per_owner'],'registration count/cap')
        check(row['target_cache_bytes']==cached[owner]<=model['target_cache_bytes_per_owner'],'target cache count/cap')
        check(row['source_lease_referenced_bytes']==lease[owner],'source lease references miscounted')
        check(row['local_copy_snapshot_bytes']==current[owner] and row['local_copy_snapshot_peak_bytes']==local_peak[owner]<=model['local_copy_bytes_per_owner'],'local copy snapshot not reclaimed')
        check(row['source_capture_scratch_peak_bytes']==scratch,'source capture scratch omitted')
    return dict(case=folder.parent.name+'/'+folder.name,acquisitions=len(acquired),releases=len(released),held_payload_bytes=sum(r['bytes'] for r in held.values()),
                actual_source_captures=len(captures),source_lease_referenced_bytes=sum(lease.values()),target_cache_bytes=sum(cached.values()),
                native_full_result_passed=result['passed'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--functional',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--additional-functional',type=Path,action='append',default=[])
    a=p.parse_args();folders=sorted(p for p in a.functional.iterdir() if p.is_dir() and not p.name.startswith('native-'))
    for extra in a.additional_functional:
        folders+=sorted(p for p in extra.iterdir() if p.is_dir() and not p.name.startswith('native-'))
    wire=[]
    for folder in folders:
        row=memory_and_wire_audit(folder);row['case']=folder.parent.name+'/'+folder.name;wire.append(row)
    pools=[pool_audit(p) for p in folders if 'buffer_model' in read(p/'initial.json')]
    rejected=[]
    for tamper in ('early_release','peak'):
        try:pool_audit(a.functional/'ample',tamper)
        except AssertionError as error:rejected.append(dict(kind=tamper,reason=str(error)))
        else:raise AssertionError('tampered pool accepted')
    try:memory_and_wire_audit(a.functional/'ample','output')
    except AssertionError as error:rejected.append(dict(kind='output',reason=str(error)))
    else:raise AssertionError('corrupted output accepted')
    report=dict(passed=True,wire_and_memory=wire,pools=pools,tamper_rejections=rejected,
        source_transmissions=sum(r['source_packets'] for r in wire),actual_target_effects=sum(r['target_effects'] for r in wire),
        scope='independent stdlib pool/actual ACK/source bytes plus separate wire/memory replay; named contents only, not total RSS/HBM')
    with a.output.open('x') as stream:stream.write(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(dict(wire_cases=len(wire),pool_cases=len(pools),source_transmissions=report['source_transmissions'])))


if __name__=='__main__':main()
