"""Independent stage, memory and wire checks; imports no platform executor."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct


def load(p): return json.loads(Path(p).read_bytes())
def save(p, v): Path(p).write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def verify(result, config, topology, protocol, transcript):
    assert result['passed'] and result['native_network_executed'] and result['session']['finished']
    tx = result['transactions']; net = tx['transport']
    assert net['physical_drained'] and net['closed'] and net['inflight_packets'] == 0
    assert not tx['active_source_leases'] and not tx['active_local_memory_leases']
    assert not tx['hardware_calibrated'] and not tx['gpu_visibility_modeled'] and not tx['roce_wire_equivalent']
    specs = {o['id']: o for o in config['operations']}
    rows = {o['id']: o for o in tx['operations']}
    connections = {c['id']: c for c in config['connections']}
    assert set(specs) == set(rows) and result['completed_operations'] == len(rows)
    commands = [row['message'] for row in transcript if row['direction'] == 'frontend']
    prepares, publications, ends, first_publish = {}, [], {}, {}
    for command in commands:
        for identity in command.get('prepare_operations', []):
            assert identity not in prepares; prepares[identity] = command['now_ns']
        for identity in command.get('publish_operations', []):
            row = rows[identity]; c = row['connection']; previous = ends.get(c, 0)
            requested = row['ticket']+1; exclusive = max(previous, requested)
            assert not specs[identity]['aggregate_requests']
            publications.append(dict(connection=c, operation=identity, requested=requested,
                                     previous=previous, exclusive=exclusive, time_ns=command['now_ns']))
            for op in rows.values():
                if op['connection'] == c and previous <= op['ticket'] < exclusive:
                    assert op['id'] not in first_publish; first_publish[op['id']] = command['now_ns']
            ends[c] = exclusive
    assert tx['submission'] == dict(mode='staged_prefix/1', publications=publications)
    for c in connections.values():
        ordered = sorted((r for r in rows.values() if r['connection'] == c['id']), key=lambda r:r['ticket'])
        prefix = 0
        for index, row in enumerate(ordered):
            identity = row['id']; assert row['ticket'] == row['sequence'] == index
            release = ordered[index-c['queue_depth']]['completed_ns'] if index >= c['queue_depth'] else 0
            descriptor = max(prepares[identity], release)
            prefix = max(prefix, descriptor)
            assert row['descriptor_ready_ns'] == descriptor and row['prepared_ns'] == prefix
            assert row['published_ns'] == first_publish[identity] >= row['prepared_ns'] >= row['ready_ns']
            assert row['published_ns'] <= row['applied_ns'] < row['request_acked_ns'] <= row['completed_ns']
            assert row['completed_ns'] == max(row['request_acked_ns'],row['response_ns'],
                ordered[index-1]['completed_ns'] if index else 0)
    events = result['session']['events']; delivered = {}
    fields = dict(submitted='ready_ns',descriptor_ready='descriptor_ready_ns',prepared='prepared_ns',
                  published='published_ns',applied='applied_ns',request_acked='request_acked_ns',
                  response='response_ns',completed='completed_ns')
    for event in events:
        key = event['operation'],event['event']; assert key not in delivered
        assert event['time_ns'] == rows[key[0]][fields[key[1]]]; delivered[key] = event['time_ns']
    assert delivered == {(i,event):row[field] for i,row in rows.items() for event,field in fields.items() if row[field]>=0}
    for command, decision in zip(commands, [r['message'] for r in transcript if r['direction']=='native']):
        assert command['decision_id'] == decision['decision_id'] and command['now_ns'] == decision['now_ns']
    seen = set()
    for entry in transcript:
        message = entry['message']
        if entry['direction'] == 'native':
            seen.update((e['operation'],e['event']) for e in message['events'])
        else:
            assert all((i,'submitted') in seen for i in message.get('prepare_operations',[]))
            assert all((i,'prepared') in seen for i in message.get('publish_operations',[]))
    memory, base = {}, {}
    for w in config['windows']:
        key = w['owner'],w['key']; base[key]=w['address']; memory[key]=bytearray(w['bytes'])
        for s in w['segments']:
            body=bytes.fromhex(s['hex']);memory[key][s['offset']:s['offset']+len(body)] = body
    def read(owner,key,address,length):
        data=memory[owner,key];start=address-base[owner,key]
        assert 0<=start<start+length<=len(data);return bytes(data[start:start+length])
    def write(owner,key,address,body):
        read(owner,key,address,len(body));start=address-base[owner,key];memory[owner,key][start:start+len(body)]=body
    captured, active, expected, old_values = {}, set(), {}, {}
    bindings={(row['connection'],row['sequence']):i for i,row in rows.items()}
    changes=sorted([(e['order'],'source',e) for e in tx['source_events']]+
                   [(e['order'],'target',e) for e in tx['target_events']])
    assert [x[0] for x in changes] == list(range(len(changes)))
    for _,kind,e in changes:
        if kind=='source':
            i=e['operation'];op=specs[i];source=op['source'];owner=connections[op['connection']]['src']
            assert all(e[k]==v for k,v in dict(owner=owner,**source).items())
            if e['kind']=='acquired':
                assert i not in active and e['time_ns']==rows[i]['ready_ns'];active.add(i)
            elif e['kind']=='captured':
                assert i in active and i not in captured and e['time_ns']==rows[i]['published_ns']
                body=read(owner,source['key'],source['address'],source['bytes'])
                assert bytes.fromhex(e['hex'])==body;captured[i]=body
            else:
                assert e['kind']=='released' and i in active and e['time_ns']==rows[i]['completed_ns'];active.remove(i)
        else:
            i=bindings[e['connection'],e['sequence']];op=specs[i];owner=connections[op['connection']]['dst']
            body=captured[i] if op['verb']=='put' else op['value'].to_bytes(8,'big')
            assert e['time_ns']==rows[i]['applied_ns'] and not e['duplicate']
            assert e['verb']==(1 if op['verb']=='put' else 2)
            assert (e['address'],e['key'],bytes.fromhex(e['body_hex']))==(op['address'],op['key'],body)
            expected[2*op['connection'],rows[i]['sequence']]=(e['verb'],op['address'],op['key'],body)
            if op['verb']=='put':write(owner,op['key'],op['address'],body)
            else:
                old=int.from_bytes(read(owner,op['key'],op['address'],8),'little');old_values[i]=old
                assert e['old_value']==rows[i]['returned_value']==old
                write(owner,op['key'],op['address'],((old+op['value'])%(1<<64)).to_bytes(8,'little'))
                expected[2*op['connection']+1,rows[i]['sequence']]=(3,0,0,old.to_bytes(8,'big'))
    assert not active
    for w in tx['windows']:
        observed=bytearray(w['bytes'])
        for page in w['pages']:
            body=bytes.fromhex(page['hex']);observed[page['offset']:page['offset']+len(body)]=body
        assert observed==memory[w['owner'],w['key']]
    streams = {}
    assert len(net['source_packets'])==len(net['source_payload_packets'])
    for packet,payload in zip(net['source_packets'],net['source_payload_packets']):
        stamp,stream,src,dst,nic,seq,size,uid=packet;body=bytes.fromhex(payload['hex'])
        assert (payload['time_ns'],payload['stream'],payload['sequence'],len(body))==(stamp,stream,seq,size)
        c=connections[stream//2];reply=stream%2
        assert (src,dst,nic)==((c['dst'],c['src'],c['destination_nic']) if reply else (c['src'],c['dst'],c['source_nic']))
        data=streams.setdefault(stream,bytearray());assert seq==len(data) and 0<size<=protocol['payload_bytes'];data.extend(body)
    decoded={}
    for stream,data in streams.items():
        offset=0
        while offset<len(data):
            magic,verb,sequence,address,key,length=struct.unpack('!IIQQII',data[offset:offset+32])
            assert magic==0x4d525431 and 0<length<=32768 and offset+32+length<=len(data)
            assert (stream,sequence) not in decoded
            decoded[stream,sequence]=(verb,address,key,bytes(data[offset+32:offset+32+length]));offset+=32+length
    assert decoded==expected
    capture=result['packet_capture'];wire=capture['wire_headers'];physical=[]
    assert capture['complete'] and not capture['omitted_events']
    for event,index in zip(capture['events'],wire['event_header_indices']):
        row=dict(zip(capture['columns'],event));header=dict(zip(wire['columns'],wire['headers'][index]))
        assert row['packet_uid']==header['packet_uid'];row.update(header);physical.append(row)
    txs=[r for r in physical if r['event']==capture['event_codes']['tx']]
    rxs=[r for r in physical if r['event']==capture['event_codes']['rx']];unmatched=set(range(len(rxs)))
    ports={}
    for link in topology['links']:
        for a,b in (('src','dst'),('dst','src')):
            ports[link[a],link[a+'_port']]=(link[b],link[b+'_port'],link['rate_bps'],link['delay_ns'])
    for row in txs:
        peer,port,rate,delay=ports[row['node'],row['port']]
        # Locked ns-3 DataRate -> Time(int64x64_t) rounds to nearest ns.
        # Keep an integer independent calculation, rather than ceil serialization.
        serial=(2*row['model_bytes']*8000000000+rate)//(2*rate)
        arrival=row['time_ns']+serial+delay
        matches=[i for i in unmatched if (rxs[i]['node'],rxs[i]['port'],rxs[i]['time_ns'])==(peer,port,arrival)
                 and all(rxs[i][key]==row[key] for key in ('packet_uid','sequence','ip_protocol','source_port','destination_port','model_bytes'))]
        assert len(matches)==1;unmatched.remove(matches[0])
    assert not unmatched and len(txs)==len(rxs)
    for stamp,stream,src,dst,nic,seq,size,uid in net['source_packets']:
        matches=[r for r in txs if (r['node'],r['packet_uid'],r['sequence'])==(src,uid,seq) and r['ip_protocol']==17]
        assert len(matches)==1
        assert (matches[0]['time_ns'],matches[0]['port'],matches[0]['data_payload_bytes'])==(stamp,nic,size)
    assert max(r['time_ns'] for r in rxs)==result['finish_ns']
    return dict(passed=True,operations=len(rows),publications=len(publications),
                no_op_rings=sum(p['previous']==p['exclusive'] for p in publications),
                source_packets=len(net['source_packets']),mrt1_bytes=sum(map(len,streams.values())),
                physical_tx=len(txs),finish_ns=result['finish_ns'],atomic_old_values=old_values)


def corruptions(result,config,topology,protocol,transcript):
    cases={
        'descriptor-time':lambda r:r['transactions']['operations'][1].update(descriptor_ready_ns=999999),
        'publication-prefix':lambda r:r['transactions']['submission']['publications'][-1].update(exclusive=1),
        'source-payload':lambda r:r['transactions']['transport']['source_payload_packets'][0].update(hex='00'),
        'target-memory':lambda r:r['transactions']['windows'][0]['pages'][0].update(hex='00'),
        'wire-time':lambda r:r['packet_capture']['events'][0].__setitem__(0,999999),
    }
    for name,change in cases.items():
        mutated=deepcopy(result);change(mutated)
        try:verify(mutated,config,topology,protocol,transcript)
        except (AssertionError,ValueError,KeyError,struct.error):continue
        raise AssertionError('accepted corrupted evidence: '+name)
    return list(cases)
