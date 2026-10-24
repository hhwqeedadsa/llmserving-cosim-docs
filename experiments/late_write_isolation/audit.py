"""Independent stdlib wire/receiver/memory/control/resource audit; no policy import."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct


def read(p):return json.loads(p.read_text())
def lines(p):return [json.loads(x) for x in p.read_text().splitlines()]
def check(ok,why):
    if not ok:raise AssertionError(why)
def table(columns,rows):return [dict(zip(columns,x)) for x in rows]


def audit(s,case,tamper=None):
    root=s/'cases'/case['name'];m=read(s/'manifest.json')
    r=read(root/'truth/result.json');obs=lines(root/'observed/decisions.jsonl')
    control=lines(root/'truth/control.jsonl');initial=read(s/'inputs/initial.json');config=read(s/case['policy'])
    t=r['transactions'];tr=t['transport'];slow=case['expected_partial'];mode=config['mode']
    if tamper=='failure_time':tr['recovery']['failures'][0]['time_ns']+=1
    if tamper=='output_memory':
        w=next(w for w in t['windows'] if w['owner']==4);w['hex']='ff'+w['hex'][2:]
    if tamper=='early_observation':
        e=next(e for row in obs for e in row['view']['events'] if e.get('event')=='applied');e['time_ns']-=1
    check('execution_error' not in r and r['native_network_executed'], 'native execution error')
    check(r['passed']==(not slow) and r['session']['finished']==(not slow),'native terminal class')
    if slow:check(r['finish_ns']==5000000,'native partial cutoff')
    terminal=read(root/'observed/terminal.json')
    check(terminal==dict(terminal='native_partial_exit_1' if slow else 'native_exit_0',native_exit_code=int(slow),policy_new_sent=True,policy_copy_sent=mode=='copy-out'),'driver terminal')
    check({q for q in r['loaded_libraries'] if Path(q).name.startswith('libns3')}=={q['path'] for q in m['libraries']},'loaded library identity')
    check(tr['recovery']['complete'] and r['packet_capture']['complete'] and r['packet_capture']['omitted_events']==0,'incomplete records')
    check(not r['fault_drops'],'unexpected physical drop')
    # Verify every policy input against its actual callback; independently derive
    # the submitted actions without reading the policy implementation.
    check(len(control)==2*len(obs),'callback/commit pairing')
    all_events=[];applied={};sent={};deadline=False;did_copy=False;completed=set()
    for i,row in enumerate(obs):
        native,frontend=control[2*i:2*i+2]
        check((native['direction'],frontend['direction'])==('native','frontend'),'transcript directions')
        v,c=native['message'],frontend['message'];all_events+=v['events']
        filtered=[e for e in v['events'] if 'alarm' in e or (e.get('operation') in (0,1) and e['event']=='applied') or (e.get('operation')==2 and e['event']=='completed')]
        check(row['view']==dict(decision_id=i,now_ns=v['now_ns'],events=filtered),'fabricated or forbidden observation')
        check(c['decision_id']==i and c['now_ns']==v['now_ns'],'commit time/id')
        for e in v['events']:
            check(e['time_ns']<=v['now_ns'],'future callback')
            if e.get('event')=='completed' and 'operation' in e:completed.add(e['operation'])
        for e in filtered:
            if 'alarm' in e:
                check(e==dict(alarm=99,event='fired',time_ns=100000),'alarm identity');deadline=True
            elif e['event']=='applied':applied[e['operation']]=e['time_ns']
        expected=[];alarms=None
        def put(identity,address):
            return dict(id=identity,kind='remote',connection=identity,verb='put',key=9,address=address,source=dict(key=7,address=4096,bytes=8),after=[])
        if i==0:
            check(v['now_ns']==0 and not filtered,'initial decision')
            expected=[put(0,8192)];alarms=[dict(id=99,delay_ns=100000)]
        elif deadline and 1 not in sent and (mode!='receive-order' or 0 in applied):
            expected=[put(1,config['new_address'])]
            if case.get('declared_effect_order'):expected[0]['after']=[dict(operation=0,event='applied')]
        if mode=='copy-out' and 1 in applied and not did_copy:
            expected.append(dict(id=2,kind='local',rank=4,duration_ns=0,
                copy=dict(source=dict(key=9,address=8192,bytes=8),target=dict(key=9,address=8208)),after=[dict(operation=1,event='applied')]))
            did_copy=True
        check(row['submissions']==expected==c['submissions'],'policy action does not follow visible events')
        check(row['alarms']==alarms and c.get('alarms')==alarms,'control alarm side effect')
        need={0,1,2} if mode=='copy-out' else {0,1}
        finish=deadline and need<=completed and v['drained']
        check(row['finish']==c['finish']==finish,'incorrect finish/hidden data use')
        for op in expected:
            check(op['id'] not in sent,'repeated operation');sent[op['id']]=(v['now_ns'],op)
    check(all_events==r['session']['events'] and set(applied)=={0,1},'callback completeness')
    check(sent[1][0]==(max(100000,applied[0]) if mode=='receive-order' else 100000),'new write trigger')
    # Pair actual source device transmissions with the exact encoded bytes.
    packets=table(tr['source_packet_columns'],tr['source_packets']);payload=tr['source_payload_packets'];by_uid={}
    check(len(packets)==len(payload),'source byte capture count')
    for p,b in zip(packets,payload):
        check(all(p[k]==b[k] for k in ('stream','sequence','time_ns')),'source byte capture order')
        raw=bytes.fromhex(b['hex']);check(len(raw)==40==p['payload_bytes'] and p['sequence']==0,'frame size/offset')
        identity=p['stream']//2;check(p['stream'] in (0,2) and p['src']==identity and p['dst']==4,'source identity')
        op=sent[identity][1]
        check(struct.unpack('>IIQQII',raw[:32])==(0x4d525431,1,0,op['address'],9,8),'MRT1 addressing')
        check(raw[32:]==bytes([0x11 if identity==0 else 0x22])*8,'source data identity')
        check(p['packet_uid'] not in by_uid,'duplicate packet UID');by_uid[p['packet_uid']]=(p,raw)
    cap=r['packet_capture'];capture=table(cap['columns'],cap['events']);headers=cap['wire_headers']
    hs=table(headers['columns'],headers['headers']);check(len(capture)==len(headers['event_header_indices']),'wire header coverage')
    tx=[e for e in capture if e['event']==0 and e['node'] in range(8) and e['ip_protocol']==17]
    check(len(tx)==len(packets),'actual source TX count')
    for e in tx:
        p,raw=by_uid[e['packet_uid']]
        check((e['time_ns'],e['node'],e['message_id'],e['sequence'],e['data_payload_bytes'])==(p['time_ns'],p['src'],p['stream'],0,len(raw)),'actual source TX mismatch')
    topo=read(s/case['topology']);peer={}
    for edge in topo['links']:
        peer[edge['src'],edge['src_port']]=edge['dst'];peer[edge['dst'],edge['dst_port']]=edge['src']
    paths=defaultdict(list);services=Counter();accepted={};duplicates=0;target_rx=[]
    for e,index in zip(capture,headers['event_header_indices']):
        if e['event']==0:services[e['node'],e['port']]+=e['model_bytes']
        if e['ip_protocol']!=17:continue
        p,raw=by_uid[e['packet_uid']];h=hs[index]
        check(h['source_port']==h['destination_port']==24000+p['stream'],'wire connection/ECMP port')
        if e['event']==0:paths[p['packet_uid']].append((e['node'],e['port']))
        if e['event']!=1 or e['node']!=4:continue
        target_rx.append((e,p));stream=p['stream']
        # One 40-byte request per connection. Repeated sequence zero is rejected
        # by continuous-prefix reception; it cannot execute a second write.
        if stream in accepted:duplicates+=1;continue
        check(e['data_payload_bytes']==40,'target frame truncation')
        _,verb,seq,address,key,size=struct.unpack('>IIQQII',raw[:32])
        accepted[stream]=dict(connection=stream//2,sequence=seq,verb=verb,address=address,key=key,body_hex=raw[32:].hex(),time_ns=e['time_ns'],duplicate=False)
    check(set(accepted)=={0,2} and len(t['target_events'])==2,'target effect coverage')
    for effect in t['target_events']:
        check(all(effect[k]==v for k,v in accepted[effect['connection']*2].items()),'effect not backed by accepted RX bytes')
    check(len(target_rx)==len(packets),'lost/unmatched target RX')
    for p in packets:
        route=[p['src']]
        for node,port in paths[p['packet_uid']]:
            check(node==route[-1],'disconnected path');route.append(peer[node,port])
        check(route[-1]==4,'wrong route target')
    for port in r['ports']:check(services[port['node'],port['port']]==port['model_tx_bytes'],'port byte accounting')
    op={o['id']:o for o in t['operations']}
    for identity in (0,1):
        check(op[identity]['applied_ns']==applied[identity]==accepted[2*identity]['time_ns'],'wire/application callback time')
        check(op[identity]['published_ns']==sent[identity][0],'publication time')
    failures=tr['recovery']['failures'];old_tx=[p for p in packets if p['stream']==0]
    if slow:
        recovery=initial['transport_recovery'];rto=recovery['initial_rto_ns']
        check(len(old_tx)==recovery['max_retries']+1,'old retransmission count')
        start=old_tx[0]['time_ns']
        check([p['time_ns'] for p in old_tx]==[start+i*rto for i in range(3)],'old retry deadlines')
        expected_failure=start+(recovery['max_retries']+1)*rto
        check(failures==[dict(source=0,stream=0,time_ns=expected_failure)],'failure time inconsistent with real retry exhaustion')
        check(all(p['time_ns']<expected_failure for p in old_tx) and applied[0]>expected_failure,'pre-failure TX/late RX causal ordering')
        check(op[0]['request_acked_ns']==op[0]['completed_ns']==-1,'failed transaction falsely completed')
        iso=tr['source_isolation'];check(iso['wire_idle'] and iso['healthy_registered_work_drained'] and not iso['failed_resources_released'],'source failure isolation')
        check(not tr['closed'],'failed session incorrectly closed')
    else:check(not failures and len(old_tx)==1,'fast control failed/retried')
    # Real cumulative ACK receipt is required for healthy source release.
    for identity in (0,1):
        if slow and identity==0:continue
        check(op[identity]['completed_ns']==op[identity]['request_acked_ns']>applied[identity],'healthy completion ordering')
        check(any(e['event']==1 and e['node']==identity and e['ip_protocol']==252 and e['time_ns']==op[identity]['completed_ns'] for e in capture),'completion without real ACK RX')
    # Replay ordered target writes and copy capture/commit from actual bytes.
    memory={(w['owner'],w['key']):bytearray.fromhex(w['hex']) for w in initial['windows']}
    base={(w['owner'],w['key']):w['address'] for w in initial['windows']};snap={}
    effects=[(e['order'],'remote',e) for e in t['target_events']]+[(e['order'],'local',e) for e in t['local_memory_events']]
    for _,kind,e in sorted(effects):
        if kind=='remote':
            offset=e['address']-8192;body=bytes.fromhex(accepted[e['connection']*2]['body_hex'])
            check(0<=offset<=24,'target range');memory[4,9][offset:offset+8]=body
        elif e['kind']=='captured':
            source=e['source'];key=source['owner'],source['key'];offset=source['address']-base[key]
            body=bytes(memory[key][offset:offset+source['bytes']]);check(e['hex']==body.hex(),'copy capture differs from actual memory');snap[e['operation']]=body
        elif e['kind']=='committed':
            body=snap[e['operation']];check(e['hex']==body.hex(),'copy commit changed snapshot')
            target=e['target'];key=target['owner'],target['key'];offset=target['address']-base[key]
            memory[key][offset:offset+len(body)]=body
    check(all(bytes(memory[w['owner'],w['key']]).hex()==w['hex'] for w in t['windows']),'final memory differs from ordered wire/copy replay')
    copies=t['local_memory_events']
    if mode=='copy-out':
        check([e['kind'] for e in copies]==['acquired','captured','committed','released'],'copy lifecycle')
        check(all(e['operation']==2 and e['time_ns']==applied[1] for e in copies),'copy timing')
    else:check(not copies,'unexpected local memory operation')
    # Independently account named data pools; leases are references, not bytes
    # allocated in addition to the registered window.
    pool=tr['payload_pool'];used=defaultdict(int);peak=defaultdict(int);held=set()
    for e in pool['events']:
        identity=e['source'];key=e['stream'],e['work'];check(e['bytes']==40,'pool charge excludes header')
        if e['kind']=='acquired':
            check(key not in held,'double acquire');held.add(key);used[identity]+=40
        else:
            check(e['kind']=='released' and key in held,'invalid pool release');held.remove(key);used[identity]-=40
            check(e['time_ns']==op[identity]['request_acked_ns'],'pool release without complete ACK')
        peak[identity]=max(peak[identity],used[identity]);check(0<=used[identity]<=40 and e['used_bytes']==used[identity],'pool capacity')
    check(pool['capacity_bytes_per_source']==40 and held==({(0,0)} if slow else set()),'held payload')
    check(pool['held']==([dict(source=0,stream=0,work=0,bytes=40)] if slow else []),'actual retained vector')
    for owner in pool['owners']:check((owner['used_bytes'],owner['peak_bytes'])==(used[owner['source']],peak[owner['source']]),'pool owner accounting')
    check(t['active_source_leases']==([dict(operation=0,owner=0,key=7,address=4096,bytes=8)] if slow else []),'source lease lifetime')
    check(not t['active_local_memory_leases'],'local lease not released')
    buffers=t['buffer_retention'];check(buffers['config']==initial['buffer_model'] and buffers['retained_operation_body_bytes']==0,'buffer model/retained duplicate body')
    owners={e['owner']:e for e in buffers['owners']}
    for identity in (0,1,4):
        e=owners[identity]
        check(e['registered_bytes']==(32 if identity==4 else 8),'registered capacity fairness')
        check(e['target_cache_bytes']==(80 if identity==4 else 0),'cumulative target request cache')
        check(e['source_lease_referenced_bytes']==(8 if slow and identity==0 else 0),'source lease referenced bytes')
        check(e['local_copy_snapshot_bytes']==0 and e['local_copy_snapshot_peak_bytes']==(8 if identity==4 and mode=='copy-out' else 0),'copy snapshot charge/reclaim')
    output=bytes(memory[4,9][config['output_address']-8192:config['output_address']-8192+8])
    stale=slow and mode=='shared' and applied[1]<applied[0] and output==b'\x11'*8
    check(output==(b'\x11'*8 if stale else b'\x22'*8),'output identity')
    return dict(name=case['name'],output_correct=output==b'\x22'*8,stale_overwrite=stale,
        old_applied_ns=applied[0],new_applied_ns=applied[1],new_submitted_ns=sent[1][0],
        output_ready_ns=applied[1],source_failure_ns=failures[0]['time_ns'] if failures else None,
        source_packets=len(packets),duplicate_target_rx=duplicates,target_effects=2,
        retained_source_payload_bytes=40 if slow else 0,retained_source_lease_bytes=8 if slow else 0,
        registered_target_bytes=32,target_cache_bytes=80,copy_snapshot_peak_bytes=8 if mode=='copy-out' else 0,
        total_model_port_tx_bytes=sum(services.values()),native_passed=r['passed'],native_finish_ns=r['finish_ns'],
        hardware_calibrated=False,full_inference=False,distributed_notification_modeled=False)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--gate-only',action='store_true');p.add_argument('--safe-amendment',action='store_true');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();s=a.stage.resolve();m=read(s/'manifest.json')
    for rel,digest in m['input_hashes'].items():check(hashlib.sha256((s/rel).read_bytes()).hexdigest()==digest,'input hash: '+rel)
    cases=read(s/'safe-amendment.json')['conditions'] if a.safe_amendment else m['conditions'][:2] if a.gate_only else m['conditions']
    results=[audit(s,c) for c in cases]
    tampering=[]
    for kind in ('failure_time','output_memory','early_observation'):
        try:audit(s,cases[1],kind)
        except AssertionError as e:tampering.append(dict(kind=kind,rejected=True,reason=str(e)))
        else:raise AssertionError('tamper accepted: '+kind)
    a.output.write_text(json.dumps(dict(schema_version='late-write-independent-audit/1',cases=results,tampering=tampering,complete_matrix=not a.gate_only,independent_stdlib=True),indent=2,sort_keys=True)+'\n')
    print(json.dumps(dict(cases=len(results),tampering_rejected=len(tampering))))


if __name__=='__main__':main()
