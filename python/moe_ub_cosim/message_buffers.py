"""Optional functional message bytes; host arenas, not GPU memory or activations."""
from collections import Counter, defaultdict, deque

from .scaleout_topology import fields, integer, require


def configuration(transport):
    spec=transport.get('message_buffers')
    if 'message_buffers' not in transport:return None
    fields(spec,'schema_version content capacity_bytes_per_gpu max_retained_bytes max_messages max_events '
           'max_payload_capture_bytes','service message buffers')
    require(spec['schema_version']=='moe-service-message-buffers/1' and
            spec['content']=='deterministic_message_marker/1','unsupported service message buffer model')
    for key,maximum in [('capacity_bytes_per_gpu',64*2**20),('max_retained_bytes',128*2**20),
                        ('max_messages',131072),('max_events',1000000),('max_payload_capture_bytes',64*2**20)]:
        integer(spec[key],'message buffers '+key,1,maximum)
    require(spec['max_messages']>=transport['limits']['max_work'] and
            spec['max_events']>=7*spec['max_messages'],'message buffer history/event budget too small')
    return spec


def marker(identity,size,offset=0):
    return bytes(((identity>>(8*(i%8)))^(i//8))&255 for i in range(offset,offset+size))


def digest(data):
    value=14695981039346656037
    for byte in data:value=((value^byte)*1099511628211)&((1<<64)-1)
    return f'{value:016x}'


def copy_peak_bounds(messages):
    """Bounds from actual work post/ACK ns; cross-callback tie order is unknown.

    Native ACK frees retransmission copies before scheduling the service ACK
    callback. A same-ns new post can therefore observe different copy and arena
    occupancy. Never claim the two measured peaks must be identical.
    """
    changes=defaultdict(lambda:defaultdict(lambda:[0,0,0]))
    for m in messages.values():
        changes[m['src']][m['ready_ns']][0]+=m['bytes']
        changes[m['src']][m['ready_ns']][2]=max(changes[m['src']][m['ready_ns']][2],m['bytes'])
        changes[m['src']][m['ack_ns']][1]+=m['bytes']
    bounds={}
    for owner,times in changes.items():
        retained=lower=upper=0
        for _,(acquired,released,single) in sorted(times.items()):
            after=retained+acquired-released
            require(after>=0,'copy confirmation exceeds posted bytes')
            lower=max(lower,retained,after,single);upper=max(upper,retained+acquired)
            retained=after
        require(retained==0,'message copies lack complete ACKs')
        bounds[owner]=(lower,upper)
    return bounds


def packet_writes(result,messages):
    """Independently accept actual destination packets, then split into messages.

    Source packet hex is joined to full packet capture by time/stream/sequence;
    receiver bytes are traced back to the actual packet UID, including SR holds.
    No receiver contents are filled from the logical marker definition.
    """
    transport=result['service_transport'];native=transport['native']
    streams={s['id']:s for s in native['streams']}
    recovery=native['recovery']['config'];selective=recovery['mode']=='selective_repeat/1'
    sources=defaultdict(deque)
    for row in native.get('source_payload_packets',[]):
        try:data=bytes.fromhex(row['hex'])
        except (TypeError,ValueError) as error:raise ValueError('invalid packet payload hex') from error
        require(data and row['hex']==data.hex(),'noncanonical or empty packet payload')
        sources[row['time_ns'],row['stream'],row['sequence'],len(data)].append(data)
    intervals=defaultdict(list)
    for row in messages.values():intervals[row['stream']].append(row)
    for rows in intervals.values():rows.sort(key=lambda m:m['begin'])
    transmitted={};prefix=Counter();held=defaultdict(dict);used=Counter()
    writes=defaultdict(list);delivered=[];tx_bytes=0
    def deliver(sid,seq,data,now):
        delivered.append((sid,seq,now,data.hex()))
        end=seq+len(data);covered=0
        for row in intervals[sid]:
            left=max(seq,row['begin']);right=min(end,row['end'])
            if left<right:
                part=data[left-seq:right-seq];covered+=len(part)
                writes[row['id']].append((now,left-row['begin'],part))
        require(covered==len(data),'packet bytes outside posted message ranges')
    capture=result['packet_capture']
    for raw in capture['events']:
        p=dict(zip(capture['columns'],raw));sid=p['message_id']
        if sid not in streams or p['ip_protocol']!=17:continue
        s=streams[sid];seq=p['sequence'];size=p['data_payload_bytes'];now=p['time_ns'];uid=p['packet_uid']
        if p['event']==0 and p['node']==s['src']:
            key=now,sid,seq,size
            require(sources[key],'source DATA lacks actual captured payload')
            data=sources[key].popleft();tx_bytes+=size;cursor=0
            for row in intervals[sid]:
                left=max(seq,row['begin']);right=min(seq+size,row['end'])
                if left<right:
                    actual=data[left-seq:right-seq];cursor+=len(actual)
                    require(actual==marker(row['id'],len(actual),left-row['begin']),
                            'source DATA differs from declared message marker')
            require(cursor==size,'source DATA outside posted message ranges')
            require(uid not in transmitted,'source packet UID reused')
            transmitted[uid]=(sid,seq,data)
        if p['event']!=1 or p['node']!=s['dst']:continue
        require(uid in transmitted,'destination payload lacks actual source packet')
        sent_sid,sent_seq,data=transmitted[uid]
        require((sent_sid,sent_seq,len(data))==(sid,seq,size),'packet identity/length changed in network')
        before=prefix[sid];end=seq+size
        if end<=before:continue
        require(seq>=before,'partial duplicate payload overlap')
        if seq==before:
            deliver(sid,seq,data,now);prefix[sid]=end
            while prefix[sid] in held[sid]:
                start=prefix[sid];part=held[sid].pop(start);used[sid]-=len(part)
                deliver(sid,start,part,now);prefix[sid]+=len(part)
        elif selective:
            if seq in held[sid]:require(held[sid][seq]==data,'changed reordered packet bytes')
            else:
                require(all(end<=start or seq>=start+len(part) for start,part in held[sid].items()),
                        'overlapping reordered payload')
                if used[sid]+size<=recovery['receive_buffer_bytes'] and len(held[sid])<4096:
                    held[sid][seq]=data;used[sid]+=size
    require(all(not q for q in sources.values()),'payload capture contains extra source packets')
    require(all(not entries for entries in held.values()),'receiver retains undelivered payload')
    if selective:
        actual=Counter((e['stream'],e['sequence'],e['time_ns'],e['hex'])
                       for e in native['selective_repeat']['events'] if e['kind']=='payload_delivered')
        require(actual==Counter(delivered),'actual SR byte delivery differs from destination packet replay')
    return writes,tx_bytes


def audit(result,transport,scopes):
    """Check byte evidence, actual lifetime events and concurrent address ranges."""
    spec=configuration(transport);a=result['service_transport'];native=a['native']
    require(('message_buffers' in a)==(spec is not None) and
            ('message_payload' in native)==(spec is not None),'declared/reported message buffers differ')
    if spec is None:return None
    report=a['message_buffers']
    require(report['schema_version']=='moe-service-message-buffer-report/1' and report['configuration']==spec,
            'effective message buffer configuration differs')
    messages={m['id']:m for m in a['messages']};rows={m['id']:m for m in report['messages']}
    require(len(rows)==len(report['messages'])==len(messages) and set(rows)==set(messages)==set(scopes) and
            len(rows)<=spec['max_messages'],'missing/extra buffered message or execution scope')
    writes,tx_bytes=packet_writes(result,messages)
    expected=defaultdict(deque)
    for identity,row in rows.items():
        m=messages[identity];parts=writes[identity];cursor=0;body=bytearray()
        require(all(row[k]==m[k] for k in ('src','dst','bytes')) and
                row['post_ns']==m['ready_ns'] and row['receive_ready_ns']==m['receive_ready_ns'] and
                row['ack_ns']==m['ack_ns'] and row['scope_finished_ns']==scopes[identity],
                'buffer identity/lifecycle differs from real transport/scope')
        require(parts and parts[-1][0]==row['receive_ready_ns'],'target completion lacks actual packet bytes')
        for now,offset,data in parts:
            require(offset==cursor,'duplicate/missing target bytes');cursor+=len(data);body.extend(data)
        checksum=digest(body)
        require(cursor==row['bytes']==row['received_bytes'] and checksum==row['source_digest_fnv64']==
                row['target_digest_fnv64'] and row['source_live'] is False and row['target_live'] is False,
                'buffer contents differ or remain live')
        require(row['target_released_ns']==max(row['receive_ready_ns'],row['ack_ns'],scopes[identity]) and
                row['post_ns']<=row['receive_ready_ns']<=scopes[identity] and
                row['receive_ready_ns']<=row['ack_ns'],'unsafe buffer release time')
        def event(kind,owner,address,size,offset,now,checksum='0000000000000000'):
            expected[identity].append(dict(kind=kind,message=identity,owner=owner,address=address,
                bytes=size,offset=offset,time_ns=now,digest_fnv64=checksum))
        event('source_acquired',row['src'],row['source_address'],row['bytes'],0,row['post_ns'])
        event('target_acquired',row['dst'],row['target_address'],row['bytes'],0,row['post_ns'])
        for now,offset,data in parts:event('target_write',row['dst'],row['target_address'],len(data),offset,now,digest(data))
        event('target_ready',row['dst'],row['target_address'],row['bytes'],0,row['receive_ready_ns'],checksum)
        # For same-ns ACK/scope, preserve actual order after independently checking both times.
        tail=[]
        tail.append(dict(kind='source_released',message=identity,owner=row['src'],address=row['source_address'],
                         bytes=row['bytes'],offset=0,time_ns=row['ack_ns'],digest_fnv64=checksum))
        tail.append(dict(kind='scope_finished',message=identity,owner=row['dst'],address=row['target_address'],
                         bytes=row['bytes'],offset=0,time_ns=scopes[identity],digest_fnv64='0000000000000000'))
        tail.sort(key=lambda e:e['time_ns'])
        # Scope completion can precede ACK; keep a partial order for the tail.
        for e in tail:expected[identity].append(e)
        event('target_released',row['dst'],row['target_address'],row['bytes'],0,row['target_released_ns'],checksum)
    arena=defaultdict(dict);used=Counter();peaks=Counter();global_used=peak=0;previous=-1
    events=report['events'];require(len(events)<=spec['max_events'],'buffer event budget exceeded')
    for index,e in enumerate(events):
        identity=e['message'];require(identity in rows and expected[identity],'unknown/extra buffer event')
        queue=expected[identity]
        if (len(queue)>=2 and queue[0]['kind'] in ('source_released','scope_finished') and
                queue[1]['kind'] in ('source_released','scope_finished') and
                queue[0]['time_ns']==queue[1]['time_ns'] and e['kind']==queue[1]['kind']):
            queue[0],queue[1]=queue[1],queue[0]
        want=queue.popleft();require(all(e[k]==v for k,v in want.items()),'buffer event differs from independent history')
        require(previous<=e['time_ns']<=result['finish_ns'],'buffer event time order violated');previous=e['time_ns']
        row=rows[identity]
        if e['kind']=='source_acquired':
            require(index+1<len(events) and events[index+1]['kind']=='target_acquired' and
                    events[index+1]['message']==identity,'buffer post is not atomic paired admission')
            for side,owner,address in [('source',row['src'],row['source_address']),('target',row['dst'],row['target_address'])]:
                integer(address,'buffer address',0,spec['capacity_bytes_per_gpu'])
                require(address+row['bytes']<=spec['capacity_bytes_per_gpu'] and
                        all(address+row['bytes']<=a or address>=a+n for a,n in arena[owner].values()),
                        'live source/target address ranges alias or exceed arena')
                arena[owner][identity,side]=(address,row['bytes']);used[owner]+=row['bytes'];global_used+=row['bytes']
                peaks[owner]=max(peaks[owner],used[owner])
            peak=max(peak,global_used)
        elif e['kind'] in ('source_released','target_released'):
            side='source' if e['kind']=='source_released' else 'target';owner=e['owner']
            require(arena[owner].pop((identity,side),None)==(e['address'],e['bytes']),'release lacks live range')
            used[owner]-=e['bytes'];global_used-=e['bytes']
        require(e['used_bytes']==used[e['owner']] and e['global_used_bytes']==global_used and
                global_used<=spec['max_retained_bytes'],'buffer occupancy/budget differs')
    require(all(not queue for queue in expected.values()) and not global_used and all(not a for a in arena.values()),
            'missing buffer lifecycle events or live ranges at drain')
    owners={e['owner']:e for e in report['owners']}
    require(len(owners)==len(report['owners']) and set(owners)==set(used) and all(
        owners[o]['used_bytes']==used[o]==0 and owners[o]['peak_bytes']==peaks[o] for o in owners) and
        report['used_bytes']==0 and report['peak_bytes']==peak,'final/peak buffer occupancy differs')
    payload=native['message_payload'];copied={e['owner']:e for e in payload['owners']}
    bounds=copy_peak_bounds(messages)
    require(payload['release']=='full_cumulative_ack/1' and payload['capture_limit_bytes']==spec['max_payload_capture_bytes'] and
            payload['captured_source_bytes']==tx_bytes<=spec['max_payload_capture_bytes'] and payload['retained_bytes']==0 and
            len(copied)==len(payload['owners']) and set(copied)==set(bounds) and all(
                e['retained_bytes']==0 and type(e['peak_bytes']) is int and
                bounds[o][0]<=e['peak_bytes']<=bounds[o][1] for o,e in copied.items()),
            'transport byte copy accounting differs')
    return dict(passed=True,messages=len(rows),target_written_bytes=sum(m['bytes'] for m in messages.values()),
                source_packet_payload_bytes=tx_bytes,peak_arena_bytes=peak,arena_bytes_at_drain=0,
                scope='functional marker bytes, packet acceptance, disjoint arenas and actual scope/ACK release; no numeric/GPU proof')
