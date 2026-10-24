"""Independent stdlib audit of native control times, accepted bytes and memory."""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import struct


def read(path):return json.loads(path.read_text())
def check(ok,why):
    if not ok:raise AssertionError(why)


def audit(folder,tamper=None):
    r=read(folder/'result.json');initial=read(folder/'initial.json')
    transcript=[json.loads(line) for line in (folder/'control.jsonl').read_text().splitlines()]
    if tamper=='alarm_time':r['session']['control_alarms'][0]['fired_ns']+=1
    if tamper=='output':
        w=next(w for w in r['transactions']['windows'] if w['owner']==4 and w['key']==9)
        data=bytearray.fromhex(w['hex']);data[32]^=1;w['hex']=data.hex()
    if tamper=='duplicate_callback':
        message=next(row['message'] for row in transcript if row['direction']=='native' and
                     any('alarm' in e for e in row['message']['events']))
        message['events'].append(next(e.copy() for e in message['events'] if 'alarm' in e))
    check(len(transcript)%2==0,'unpaired native/control transcript')
    alarms={};operations={};events=[];seen=set();registrations=[]
    for n in range(len(transcript)//2):
        native,front=transcript[2*n],transcript[2*n+1]
        check(native['direction']=='native' and front['direction']=='frontend','transcript directions')
        e,c=native['message'],front['message']
        check(e['decision_id']==c['decision_id']==n and e['now_ns']==c['now_ns'],'command identity/time')
        for event in e['events']:
            check(event['time_ns']<=e['now_ns'],'future callback')
            if 'alarm' in event:
                identity=event['alarm'];key=('alarm',identity,event['event'])
                check(identity in alarms and event['event']=='fired','unknown control callback')
                a=alarms[identity]
                check(a['fired_ns']==a['cancelled_ns']==-1 and event['time_ns']==a['deadline_ns'], 'false or duplicate alarm trigger')
                a['fired_ns']=event['time_ns']
            else:
                key=('operation',event['operation'],event['event'])
                check(event['operation'] in operations,'callback before registration')
            check(key not in seen,'duplicate callback');seen.add(key);events.append(event)
        for identity in c.get('cancel_alarms',[]):
            check(identity in alarms and alarms[identity]['fired_ns']==alarms[identity]['cancelled_ns']==-1,'cancelled nonpending timer')
            alarms[identity]['cancelled_ns']=c['now_ns']
        for declaration in c.get('alarms',[]):
            identity=declaration['id'];check(identity not in alarms,'timer identity reused')
            alarms[identity]=dict(id=identity,armed_ns=c['now_ns'],deadline_ns=c['now_ns']+declaration['delay_ns'],fired_ns=-1,cancelled_ns=-1)
        for operation in c['submissions']:
            check(operation['id'] not in operations,'operation identity reused')
            operations[operation['id']]=operation
            registrations.append(dict(operation=operation['id'],time_ns=c['now_ns'],decision_id=n))
        if c['finish']:
            check(e['drained'] and not c['submissions'] and all(a['fired_ns']>=0 or a['cancelled_ns']>=0 for a in alarms.values()),'early finish')
    check(events==r['session']['events'],'callbacks differ from native history')
    check(registrations==r['session']['registrations'],'registrations differ from submitted work')
    check(r['session'].get('control_alarms',[])==[alarms[i] for i in sorted(alarms)],'alarm report differs from actual callbacks/cancellations')
    t,transport,cap=r['transactions'],r['transactions']['transport'],r['packet_capture']
    check(cap['complete'] and cap['omitted_events']==0,'incomplete packet capture')
    packets=[dict(zip(transport['source_packet_columns'],row)) for row in transport['source_packets']]
    payloads=transport.get('source_payload_packets',[]);check(len(packets)==len(payloads),'missing source payload capture')
    by_uid={}
    for p,body in zip(packets,payloads):
        raw=bytes.fromhex(body['hex'])
        check(all(p[k]==body[k] for k in ('time_ns','stream','sequence')) and len(raw)==p['payload_bytes'],'source payload association')
        check(p['packet_uid'] not in by_uid,'source uid repeated');by_uid[p['packet_uid']]=(p,raw)
    capture=[dict(zip(cap['columns'],row)) for row in cap['events']]
    connections={c['id']:c for c in initial['connections']}
    buffers=defaultdict(bytearray);consumed=Counter();accepted={};source_tx=Counter();port_tx=Counter()
    for e in capture:
        if e['event']==0:
            port_tx[e['node'],e['port']]+=e['model_bytes']
            if e['ip_protocol']==17 and e['node']<8:
                p,raw=by_uid[e['packet_uid']]
                check((e['node'],e['time_ns'],e['sequence'],e['data_payload_bytes'])==(p['src'],p['time_ns'],p['sequence'],len(raw)),'source TX not backed by bytes')
                source_tx[e['packet_uid']]+=1
        if e['event']!=1 or e['ip_protocol']!=17:continue
        p,raw=by_uid[e['packet_uid']];stream=p['stream'];connection=connections[stream//2]
        if e['node']!=connection['dst']:continue
        check(stream%2==0,'unexpected response stream in put-only case')
        buf=buffers[stream]
        if p['sequence']<len(buf):continue
        if p['sequence']>len(buf):continue
        buf.extend(raw)
        offset=consumed[stream]
        while len(buf)-offset>=32:
            magic,verb,sequence,address,key,size=struct.unpack('>IIQQII',buf[offset:offset+32])
            check(magic==0x4d525431 and verb==1,'non-MRT1 put source bytes')
            if len(buf)-offset<32+size:break
            identity=stream//2,sequence;check(identity not in accepted,'duplicate accepted transaction')
            accepted[identity]=dict(connection=stream//2,sequence=sequence,verb=verb,address=address,key=key,
                                    time_ns=e['time_ns'],body_hex=bytes(buf[offset+32:offset+32+size]).hex(),duplicate=False)
            offset+=32+size
        consumed[stream]=offset
    check(len(source_tx)==len(packets) and all(v==1 for v in source_tx.values()),'source packet TX missing/repeated')
    for port in r['ports']:check(port_tx[port['node'],port['port']]==port['model_tx_bytes'],'port service mismatch')
    check(len(accepted)==len(t['target_events']),'missing/extra target effect')
    for effect in t['target_events']:
        actual=accepted[effect['connection'],effect['sequence']]
        check(all(effect[k]==v for k,v in actual.items()),'target application not backed by continuous received bytes')
    # Replay actual remote writes, local snapshots and output commits in the
    # recorded total memory order; compare all dense windows byte-for-byte.
    memory={(w['owner'],w['key']):bytearray.fromhex(w['hex']) for w in initial['windows']}
    bases={(w['owner'],w['key']):w['address'] for w in initial['windows']}
    snapshots={};memory_events=sorted([*t['target_events'],*t['local_memory_events']],key=lambda e:e['order'])
    check(len({e['order'] for e in memory_events})==len(memory_events),'memory order repeated')
    for event in memory_events:
        if 'connection' in event:
            location=connections[event['connection']]['dst'],event['key']
            offset=event['address']-bases[location];body=bytes.fromhex(event['body_hex'])
            check(0<=offset<=len(memory[location])-len(body),'wire range outside registered window')
            memory[location][offset:offset+len(body)]=body
        elif event['kind']=='captured':
            source=event['source'];location=source['owner'],source['key'];offset=source['address']-bases[location]
            body=bytes(memory[location][offset:offset+source['bytes']])
            check(body==bytes.fromhex(event['hex']),'copy snapshot differs from actually received memory')
            snapshots[event['operation']]=body
        elif event['kind']=='committed':
            target=event['target'];location=target['owner'],target['key'];offset=target['address']-bases[location]
            check(event['operation'] in snapshots and bytes.fromhex(event['hex'])==snapshots[event['operation']],'commit changed captured bytes')
            memory[location][offset:offset+len(snapshots[event['operation']])]=snapshots[event['operation']]
    check(len(memory)==len(t['windows']),'memory windows missing')
    for window in t['windows']:
        check(bytes(memory[window['owner'],window['key']])==bytes.fromhex(window['hex']),'final memory differs from real write/copy replay')
    remotes={op['id']:op for op in t['operations']};locals_={op['id']:op for op in r['local_work']}
    names=dict(submitted='ready_ns',prepared='prepared_ns',published='published_ns',applied='applied_ns',request_acked='request_acked_ns',response='response_ns',
               local_admitted='ready_ns',local_started='start_ns',completed='completed_ns')
    for event in events:
        if 'alarm' in event:continue
        source=(locals_ if operations[event['operation']]['kind']=='local' else remotes)[event['operation']]
        check(source[names[event['event']]]==event['time_ns'],'operation callback time differs from progress')
    for operation in remotes.values():
        if operation['applied_ns']>=0:
            check(operation['applied_ns']==accepted[operation['connection'],operation['sequence']]['time_ns'],'application before actual RX')
    return dict(case=folder.name,passed=r['passed'],finish_ns=r['finish_ns'],source_packets=len(packets),target_effects=len(accepted),
        control_alarms=len(alarms),alarm_callbacks=sum('alarm' in e for e in events),
        local_completion_ns={str(i):op['completed_ns'] for i,op in locals_.items()},
        retained_source_leases=t['active_source_leases'],all_port_model_bytes=sum(port_tx.values()))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--functional',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();folders=sorted(p for p in a.functional.iterdir() if p.is_dir() and not p.name.startswith('native-'))
    results=[audit(p) for p in folders]
    baseline=read(a.functional/'default/result.json')
    for name in ('legacy_gpu','control_held','control_cancel','zero_alarm'):
        r=read(a.functional/name/'result.json')
        check(r['transactions']['transport']['source_packets']==baseline['transactions']['transport']['source_packets'],'control timer altered source traffic')
        check(r['ports']==baseline['ports'],'control timer altered native port service')
    raw_invalid=sorted(a.functional.glob('native-*/result.json'))
    check(len(raw_invalid)==22,'missing native rejection condition')
    for path in raw_invalid:
        r=read(path)
        check(not r['passed'] and not r['session']['registrations'] and not r['transactions']['operations'] and
              not r['transactions']['transport']['source_packets'],'invalid batch executed partial prefix')
    rejected=[]
    for kind in ('alarm_time','output','duplicate_callback'):
        try:audit(a.functional/'control_held',kind)
        except AssertionError as error:rejected.append(dict(kind=kind,rejected=True,reason=str(error)))
        else:raise AssertionError('tampering accepted: '+kind)
    output=dict(independent_stdlib=True,cases=results,tamper_rejections=rejected,
                native_invalid_no_work_or_packets=len(raw_invalid),unchanged_port_service_in_control_comparisons=True,
                source_packet_transmissions=sum(r['source_packets'] for r in results),target_effects=sum(r['target_effects'] for r in results))
    a.output.write_text(json.dumps(output,indent=2,sort_keys=True)+'\n')
    print(json.dumps(dict(cases=len(results),rejections=len(raw_invalid),tamper_rejections=len(rejected))))


if __name__=='__main__':main()
