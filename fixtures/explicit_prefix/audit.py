"""Independently replay physical bytes/queues/ACKs and explicit-prefix steps."""
import argparse
from collections import Counter,defaultdict,deque
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from fixtures.selective_repeat.audit import audit as audit_transport
from fixtures.live_ecmp.audit import murmur

def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def audit(topology,protocol,work,result):
    transport=audit_transport(result,work,protocol)
    assert result['gpu_count']==128 and result['switch_count']==20
    for s in result['persistent_transport']['streams']:
        assert s['qp_creations']==1 and s['snd_una']==s['snd_nxt']==s['submitted_bytes']==s['published_bytes']==80000
    caps=result['packet_capture'];packets=[dict(zip(caps['columns'],x)) for x in caps['events']]
    wh=caps['wire_headers'];headers=[dict(zip(wh['columns'],x)) for x in wh['headers']]
    assert len(wh['event_header_indices'])==len(packets)
    ports={(p['node'],p['port']):p for p in result['ports']}
    adjacency=defaultdict(list)
    for edge in topology['links']:
        adjacency[edge['src']].append((edge['dst'],edge['src_port']))
        adjacency[edge['dst']].append((edge['src'],edge['dst_port']))
    fault=protocol.get('link_failures',{}).get('events',[None])[0]
    explicit=protocol.get('local_failover',{}).get('mode')=='explicit_prefix/1'
    repair=result.get('explicit_repair')
    routes=[];by_action={};by_uid=defaultdict(list);counts=Counter();states=[]
    if fault:
        events=result['link_failures']['events'];assert result['link_failures']['pending_events']==0
        dead=False
        for e in events:
            if e['kind']=='routes_updated':
                if 'notification_revision' in e:dead=e['notification_revision']==1
                else:dead=True
                states.append((e['time_ns'],dead))
        assert result['finish_ns']>=max(e['time_ns'] for e in events)
    if explicit:
        assert repair['complete'] and repair['observations']==len(repair['events'])
        assert len(repair['packet_metadata'])==len(packets)
        routes=[dict(zip(repair['columns'],x)) for x in repair['events']]
        for e in routes:
            assert e['action'] in (1,2,3) and e['encapsulation_bytes']==38 and e['merge']==133
            assert e['node']=={1:132,2:129,3:133}[e['action']]
            assert e['output_port']=={1:2,2:18,3:0}[e['action']]
            assert e['next_step']==(1 if e['action']==1 else 2)
            key=e['time_ns'],e['node'],e['packet_uid'],e['action'];assert key not in by_action
            by_action[key]=e;by_uid[e['packet_uid']].append(e)
            counts['repair_'+str(e['action'])]+=1
        for uid,rows in by_uid.items():
            assert [e['action'] for e in rows]==[1,2,3]
            assert rows[0]['model_bytes']==rows[1]['model_bytes']==rows[2]['model_bytes']+38
            assert rows[0]['time_ns']>=fault['time_ns']
            assert rows[0]['time_ns']<fault.get('restore_time_ns',10**12)
    else:assert repair is None
    distances={}
    def candidates(node,dst,dead,physical):
        key=dst,dead
        if key not in distances:
            d={dst:0};q=deque([dst])
            while q:
                at=q.popleft()
                for peer,_ in adjacency[at]:
                    if dead and {at,peer}=={128,132}:continue
                    if peer not in d:
                        d[peer]=d[at]+1
                        if peer>=128:q.append(peer)
            distances[key]=d
        d=distances[key]
        return [p for peer,p in adjacency[node] if not(dead and {node,peer}=={128,132}) and
            d.get(peer,-10)==d.get(node,-20)-1 and not(physical and {node,peer}=={128,132})]
    busy=Counter();wrapped_queue_bytes=0
    for i,(p,hi) in enumerate(zip(packets,wh['event_header_indices'])):
        t,n,o,kind,uid=p['time_ns'],p['node'],p['port'],p['event'],p['packet_uid']
        overhead,wire=repair['packet_metadata'][i] if explicit else (0,p['ip_protocol'])
        assert overhead in (0,38) and wire==(250 if overhead else p['ip_protocol'])
        if n<128:assert overhead==0,'encapsulation leaked to endpoint'
        if overhead:
            assert n in (129,132,133) and p['model_bytes']>=38
            assert p['data_payload_bytes']==(1000 if p['ip_protocol']==17 else 0)
            counts['wrapped_packet_events']+=1
        if kind==0:
            assert t>=busy[n,o],'overlapping physical TX'
            busy[n,o]=t+round(p['model_bytes']*8*10**9/ports[n,o]['rate_bps'])
            counts['serialized_tx']+=1;counts['encapsulation_wire_bytes']+=overhead
        if kind!=3 or n<128 or p['ip_protocol'] not in (17,252,253,255):continue
        h=headers[hi];dst=(h['ip_dst']-0x0b000001)//32
        if overhead:
            action=1 if n==132 else 2
            e=by_action[t,n,uid,action]
            assert e['output_port']==o and e['model_bytes']==p['model_bytes'] and h['ip_dst']==e['original_destination_ip']
            wrapped_queue_bytes+=p['model_bytes'];counts['explicit_enqueues']+=1
        else:
            # Exact zero-delay ordering is not shared across trace arrays.
            boundaries={e['time_ns'] for e in result.get('link_failures',{}).get('events',[])}
            if t in boundaries:counts['boundary_enqueues']+=1;continue
            dead=next((value for when,value in reversed(states) if when<t),False)
            physical=bool(fault and fault['time_ns']<=t<fault.get('restore_time_ns',10**12))
            choices=candidates(n,dst,dead,physical)
            assert choices,(p,h,dead,physical)
            if p['ip_protocol']==255:
                # The legacy header observer omits CNP fid. Here every source/
                # destination pair has one declared stream, so derive its fid
                # from the input, not from a selected route or future packet.
                streams=[s for s in work['streams'] if 0x0b000001+s['src']*32==h['ip_dst'] and
                         0x0b000001+s['dst']*32==h['ip_src']]
                assert len(streams)==1
                fid=streams[0]['wire_port'];word=fid|(fid<<16)
                counts['ordinary_CNP_enqueues']+=1
            else:word=h['source_port']|(h['destination_port']<<16)
            words=[h['ip_src'],h['ip_dst'],word]
            assert o==choices[murmur(words,protocol['seed']+n)%len(choices)]
            counts['ordinary_enqueues']+=1
            if explicit and n==133 and (t,n,uid,3) in by_action:
                assert by_action[t,n,uid,3]['model_bytes']==p['model_bytes']
                counts['merge_enqueues']+=1
    if explicit:
        assert counts['merge_enqueues']==counts['repair_3']
        assert counts['explicit_enqueues']==counts['repair_1']+counts['repair_2']
        # Normal repair prefix consists of exactly two modeled wire hops.
        assert counts['encapsulation_wire_bytes']==counts['repair_1']*2*38
    return dict(transport=transport,counts=dict(counts),wrapped_data_and_control=sorted({e['inner_ip_protocol'] for e in routes}),
      first_round_ACK_ns=max(x['acked_ns'] for x in result['persistent_transport']['work'] if x['id'] in (1,2,3,4)),
      last_ACK_ns=max(x['acked_ns'] for x in result['persistent_transport']['work']),finish_ns=result['finish_ns'],
      wrapped_queue_enqueued_model_bytes=wrapped_queue_bytes)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();inp=a.stage/'input';topology=load(inp/'topology/topology.json');work=load(inp/'work.json')
    result={};raw={};protocols={}
    for name in ('normal-old','normal-new','live','prefix','restore','instant'):
        raw[name]=load(a.stage/'network'/name/'result.json')
        protocols[name]=load(inp/('normal.json' if name.startswith('normal') else name+'.json'))
        result[name]=audit(topology,protocols[name],work,raw[name])
    normal=[deepcopy(raw[n]) for n in ('normal-old','normal-new')]
    for r in normal:del r['loaded_libraries']
    assert normal[0]==normal[1]
    assert {17,252}.issubset(result['prefix']['wrapped_data_and_control'])
    # Keep an independently detected in-flight prefix crossing physical restore.
    routes=[dict(zip(raw['restore']['explicit_repair']['columns'],x)) for x in raw['restore']['explicit_repair']['events']]
    crossing=[x for x in routes if x['action']==3 and x['time_ns']>=12000]
    # A later merge is useful coverage, but a fixed case is never retuned to get it.
    mutations=[]
    for label in ('missing wire overhead','wrong physical repair port','missing merge','fabricated work ACK'):
        r=deepcopy(raw['prefix']);cap=r['explicit_repair']
        if label=='missing wire overhead':
            i=next(i for i,x in enumerate(cap['packet_metadata']) if x[0]);cap['packet_metadata'][i][0]=0
        elif label=='wrong physical repair port':cap['events'][0][3]+=1
        elif label=='missing merge':
            i=next(i for i,x in enumerate(cap['events']) if x[2]==3);del cap['events'][i];cap['observations']-=1
        else:r['persistent_transport']['work'][0]['acked_ns']-=1
        try:audit(topology,protocols['prefix'],work,r)
        except (AssertionError,KeyError):mutations.append(dict(mutation=label,rejected=True))
        else:raise AssertionError('corrupt execution accepted: '+label)
    value=dict(passed=True,actual_Clos128_networks=6,default_result_identical_except_loaded_libraries=True,
      cases=result,tamper_checks=mutations,restore_late_merges=len(crossing),
      inputs_sha256={str(f.relative_to(inp)):sha(f) for f in inp.rglob('*') if f.is_file()},
      results_sha256={n:sha(a.stage/'network'/n/'result.json') for n in raw},verifier_sha256=sha(Path(__file__)))
    with a.output.open('x') as s:json.dump(value,s,indent=2);s.write('\n')
    print(json.dumps(dict(passed=True,networks=6,physical_tx=sum(v['counts']['serialized_tx'] for v in result.values()),
      encapsulations=result['prefix']['counts'].get('repair_1',0),rejected_mutations=len(mutations))))


if __name__=='__main__':main()
