"""Independent BFS/native-hash oracle for this declared single-plane study."""
from collections import Counter, defaultdict, deque


def hash_words(words,seed):
    h=seed;mask=2**32-1
    def rot(x,n):return ((x<<n)|(x>>(32-n)))&mask
    for word in words:
        k=rot((word*0xcc9e2d51)&mask,15)*0x1b873593&mask
        h=(rot(h^k,13)*5+0xe6546b64)&mask
    h^=12;h^=h>>16;h=h*0x85ebca6b&mask;h^=h>>13;h=h*0xc2b2ae35&mask;return h^(h>>16)


def check_routes(topology,protocol,work,result):
    g=result['gpu_count'];assert {n['plane'] for n in topology['nodes'] if n['id']>=g}=={0}
    adjacency=defaultdict(list);ports={}
    for edge in topology['links']:
        a,b=edge['src'],edge['dst'];ap,bp=edge['src_port'],edge['dst_port']
        adjacency[a].append((b,ap));adjacency[b].append((a,bp));ports[a,b]=ap;ports[b,a]=bp
    live='local_failover' in protocol;declared=protocol.get('link_failures',{}).get('events',[])
    assert len(declared)<=1 and ('link_failures' in result)==bool(declared)
    events=result.get('link_failures',{}).get('events',[])
    if declared:
        lf=result['link_failures'];assert lf['configuration']==protocol['link_failures'] and lf['pending_events']==0 and lf['route_epoch']==1
        d,u=events;v=declared[0]
        assert d['kind']=='link_down' and u['kind']=='routes_updated' and d['event_index']==u['event_index']==0
        assert (d['src'],d['dst'],d['time_ns'])==(v['src'],v['dst'],v['time_ns'])
        assert (d['src_port'],d['dst_port'])==(ports[d['src'],d['dst']],ports[d['dst'],d['src']])
        assert u['time_ns']==v['time_ns']+v['route_update_delay_ns'] and u['excluded_directed_ports']==2 and u['route_epoch']==1
        assert ('local_failover' in lf)==live
    downs=[e for e in events if e['kind']=='link_down'];updates=[e for e in events if e['kind']=='routes_updated'];cache={}
    def pairs(rows):
        return {(a,b) for row in rows for a,b in ((row['src'],row['dst']),(row['dst'],row['src']))}
    def options(node,dst,globally_down,physically_down):
        key=(dst,tuple(sorted(globally_down)))
        if key not in cache:
            dist={dst:0};q=deque([dst])
            while q:
                here=q.popleft()
                for peer,_ in adjacency[here]:
                    if (here,peer) in globally_down or peer in dist:continue
                    dist[peer]=dist[here]+1
                    if peer>=g:q.append(peer)
            cache[key]=dist
        dist=cache[key]
        return [port for peer,port in adjacency[node] if (node,peer) not in globally_down and
            dist.get(peer,-100)==dist.get(node,-200)-1 and (not live or (node,peer) not in physically_down)]
    if live:
        policy=result['link_failures']['local_failover'];assert policy['configuration']==protocol['local_failover']
        assert len(policy['events'])==2
        for row in policy['events']:
            d=downs[0];assert row['node'] in (d['src'],d['dst']) and row['time_ns']==d['time_ns'] and row['event_index']==0
            removed=Counter();before_count=after_count=empty=destinations=0
            # This local adjustment precedes the scheduled global update even
            # when that update has the same numerical timestamp.
            for dst in range(g):
                before=options(row['node'],dst,set(),set());after=options(row['node'],dst,set(),pairs(downs))
                destinations+=bool(before);empty+=bool(before) and not after
                before_count+=len(before);after_count+=len(after);removed.update(set(before)-set(after))
            assert (row['candidate_destinations'],row['candidate_next_hops'],row['installed_next_hops'],row['empty_destinations'])==(destinations,before_count,after_count,empty)
            assert row['removed_by_port']==[dict(port=p,destination_entries=n) for p,n in sorted(removed.items())]
    c=result['packet_capture'];h=c['wire_headers'];headers=[dict(zip(h['columns'],x)) for x in h['headers']]
    contexts={v['packet_uid']:v['stream'] for v in result['persistent_transport']['dcqcn_reference']['events'] if v['kind']=='cnp_generated'}
    streams={v['id']:v for v in work['streams']};arrivals={};drops=Counter();counts=Counter();boundaries={v['time_ns'] for v in events}
    for raw,hi in zip(c['events'],h['event_header_indices']):
        e=dict(zip(c['columns'],raw));header=headers[hi];node=e['node'];now=e['time_ns'];proto=e['ip_protocol']
        if e['event']==0:
            assert not any(now>=d['time_ns'] and (node,e['port']) in ((d['src'],d['src_port']),(d['dst'],d['dst_port'])) for d in downs)
        if node<g or proto not in (17,252,253,255):continue
        assert e['packet_uid']==header['packet_uid'] and proto==header['ip_protocol']
        physical=pairs([d for d in downs if d['time_ns']<=now]);global_dead=pairs(downs) if updates and updates[0]['time_ns']<=now else set()
        dst=(header['ip_dst']-0x0b000001)//32
        allowed=options(node,dst,global_dead,physical)
        if proto==255:
            # Capture exposes actual IPs but not CNP fid. Match the generated
            # CNP's declared stream and the fixed CnpSwitch fid projection.
            port=streams[contexts[e['packet_uid']]]['wire_port'];word=port|(port<<16)
        else:word=header['source_port']|(header['destination_port']<<16)
        selection=allowed[hash_words([header['ip_src'],header['ip_dst'],word],protocol['seed']+node)%len(allowed)] if allowed else None
        key=(node,e['packet_uid'],proto,e['sequence'])
        if e['event']==1:arrivals[key]=e
        if e['event']==3:
            # The captured runs have no enqueue exactly on a route-change
            # timestamp; separate trace arrays cannot order such a tie.
            assert now not in boundaries,'ambiguous trace timestamp ordering'
            assert e['port']==selection,'actual output differs from independently computed candidate/hash'
            counts[str(proto)]+=1
        if e['event']==5:
            close=any(now==d['time_ns'] and (node,e['port']) in ((d['src'],d['src_port']),(d['dst'],d['dst_port'])) for d in downs)
            if close:drops['queue_close']+=1
            else:
                rx=arrivals[key];assert rx['port']==e['port'] and rx['time_ns']==now
                if live:assert selection is None
                else:assert selection is None or next((node,p) in physical for p,port in adjacency[node] if port==selection)
                drops['no_live_old_candidate' if live else 'stale_forwarding']+=1
    return dict(passed=True,enqueues_verified_by_protocol=dict(counts),local_drop_causes=dict(drops),
        no_postfailure_tx_on_failed_ports=True,no_enqueue_timestamp_tie=True,
        cnp_identity_scope='actual IP/UID plus generated stream/fid projection; packet capture does not serialize fid')
