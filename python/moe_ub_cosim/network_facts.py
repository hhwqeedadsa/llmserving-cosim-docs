"""Read bounded passive network facts for preparation and independent evaluation.

This stream contains private packet identities. Diagnostic algorithms consume
an explicitly prepared allowlist, never this raw stream or its source path.
"""
from .plan import integer, require
from .record_storage import iter_result


def policy(value, max_ns):
    require(isinstance(value,dict) and set(value)=={'schema_version','ports','end_ns','max_records'} and
            value['schema_version']=='moe-ub-network-facts/1','Invalid network facts policy')
    ps=value['ports']
    require(isinstance(ps,list) and 1<=len(ps)<=4096,'Network facts require 1..4096 ports')
    keys=set()
    for p in ps:
        require(isinstance(p,dict) and set(p)=={'node_id','port_id'},'Invalid network facts outlet')
        key=tuple(integer(p[k],k,0,2**32-1) for k in ('node_id','port_id'))
        require(key not in keys,'Repeated network facts outlet');keys.add(key)
    integer(value['end_ns'],'network facts end',1,max_ns)
    integer(value['max_records'],'network facts records',2*len(ps),2**63-1)
    return value


def checked_rows(execution, report):
    capture=report['network_facts_capture'];p=capture['policy']
    integer(capture['execution_end_ns'],'execution end',0)
    integer(capture['records'],'capture records',2*len(p['ports']))
    policy(p,max(p['end_ns'],capture['execution_end_ns']))
    require(capture['schema_version']=='moe-ub-network-facts-capture/1' and
        capture['complete'] is True and capture['dropped_records']==0 and capture['start_ns']==0 and
        capture['end_ns']==min(p['end_ns'],capture['execution_end_ns']) and
        capture['access_scope']=='internal_truth_prepare_restricted_inputs_before_diagnosis',
        'Incomplete network facts capture')
    keys={(r['node_id'],r['port_id']) for r in p['ports']};initial=set();final=set();credits={};cells={}
    count=0;previous=0
    for row in iter_result(execution,'network_facts',report):
        require(set(row)=={'sequence','time_ns','node_id','port_id','event','data'},'Unexpected network facts fields')
        count+=1;key=row['node_id'],row['port_id'];now=row['time_ns'];event=row['event'];d=row['data']
        integer(row['sequence'],'sequence',1);integer(now,'event time',0)
        for name in ('node_id','port_id'):integer(row[name],name,0,2**32-1)
        require(isinstance(d,dict),'Invalid network event payload')
        require(row['sequence']==count and key in keys and previous<=now<=capture['end_ns'] and
                key not in final,'Unordered, unknown or closed network facts outlet')
        previous=now
        if event=='initial':
            require(key not in initial and now==0 and set(d)=={'voq_bytes','fifo_bytes','credits','credit_cell_bytes'},'Invalid initial network state')
            initial.add(key);cells[key]=integer(d['credit_cell_bytes'],'cell bytes',1)
            require(isinstance(d['credits'],list) and d['credits'],'Missing initial credits')
            credits[key]=[integer(v,'initial credit',0) for v in d['credits']]
            for f in ('voq_bytes','fifo_bytes'):integer(d[f],f,0)
        else:
            require(key in initial,'Network event precedes initial state')
            if event=='final':
                require(now==capture['end_ns'] and d=={'boundary_state_from_prior_events':True},'Invalid final network boundary')
                final.add(key)
            elif event=='credit':
                require(set(d)=={'vl','available_cells','delta_cells'},'Invalid credit change')
                vl=integer(d['vl'],'VL',0,len(credits[key])-1)
                integer(d['delta_cells'],'credit delta',-(2**31),2**31-1)
                integer(d['available_cells'],'available credit',0,2**31-1)
                require(d['available_cells']==credits[key][vl]+d['delta_cells'] and d['available_cells']>=0,'Credit state lost a mutation')
                credits[key][vl]=d['available_cells']
            elif event=='credit_decision':
                require(set(d)=={'in_port','vl','queue_type','packet_uid','head_bytes','required_cells','available_cells','blocked'},'Invalid credit decision')
                vl=integer(d['vl'],'VL',0,len(credits[key])-1);size=integer(d['head_bytes'],'head bytes',0)
                integer(d['in_port'],'input port',0,2**32-1)
                integer(d['required_cells'],'required cells',0,2**32-1)
                integer(d['available_cells'],'available credit',0,2**31-1)
                require(d['required_cells']==(size+cells[key]-1)//cells[key] and
                    d['available_cells']==credits[key][vl] and type(d['blocked']) is bool and
                    d['blocked']==(d['available_cells']<d['required_cells']),'Credit decision differs from actual state/geometry')
                require(d['queue_type'] in ('VOQ','generated') and
                    (type(d['packet_uid']) is int if d['queue_type']=='VOQ' else d['packet_uid'] is None),'Invalid head identity scope')
                if d['packet_uid'] is not None:integer(d['packet_uid'],'head UID',0,2**64-1)
            elif event in ('voq','fifo'):
                require(set(d)=={'bytes'},'Invalid queue state');integer(d['bytes'],'queue bytes',0)
            elif event in ('fifo_enqueue','fifo_dequeue'):
                require(set(d)=={'packet_uid','packet_bytes','fifo_bytes'},'Invalid FIFO event')
                for f in d:integer(d[f],f,0)
            elif event=='tx':
                require(set(d)=={'packet_uid','packet_bytes','completion_delay_ns','rate_bps'},'Invalid physical service event')
                # ns-3 uses the configured integer time resolution; a short
                # control frame can legitimately serialize in zero ticks.
                for f in d:integer(d[f],f,0 if f in ('packet_uid','completion_delay_ns') else 1)
            else:raise ValueError('Unknown network facts event')
        yield row
    require(initial==final==keys and count==capture['records'] and count<=p['max_records'],'Network facts coverage/count mismatch')
