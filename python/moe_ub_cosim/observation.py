"""Integer local clocks and the closed, observed-only research input contract."""
import csv
import json
from itertools import zip_longest
from pathlib import Path

from .plan import integer, require, MAX_RANK_ID

I64_MIN, I64_MAX = -(2**63), 2**63-1
PPB = 10**9
ENDPOINT_FIELDS = ('rank_id','local_record_id','kind','start_local_ns','end_local_ns')
PORT_FIELDS = ('switch_id','port_id','direction','local_window_start_ns','local_window_end_ns',
               'counted_packets','counted_packet_bytes')
ATTRIBUTION_FIELDS = ('operation_id','switch_id','port_id','direction','local_window_start_ns',
                      'local_window_end_ns','business_payload_bytes')
WINDOW_CONFIG_FIELDS = {'switch_id','port_id','direction','start_local_ns','end_local_ns','period_ns'}
PUBLIC_FIELDS = {'schema_version','endpoint_schema','port_schema','rank_ids','ports','time_unit',
                 'byte_semantics','counter_location','clock_resolution_ns','endpoint_fields'}


def local_time(global_ns, clock):
    integer(global_ns,'global time',0,I64_MAX)
    value = ((PPB + clock['drift_ppb'])*global_ns)//PPB + clock['offset_ns']
    return integer(value,'local clock result',I64_MIN,I64_MAX)


def windows(port):
    start,end,period=port['start_local_ns'],port['end_local_ns'],port['period_ns']
    for left in range(start,end,period):
        yield left,min(left+period,end)


OBSERVATION_BUDGET_FIELDS = ('max_ports','max_windows_per_port','max_total_windows','max_event_bytes_per_port',
                             'max_ledger_packets','max_registered_transfers','max_export_bytes')


def validate_observation_budget(budget):
    require(isinstance(budget,dict) and set(budget)==set(OBSERVATION_BUDGET_FIELDS)|{'schema_version'} and
            budget['schema_version']=='moe-ub-observation-resources/1','Invalid observation resource budget')
    for key in OBSERVATION_BUDGET_FIELDS:integer(budget[key],key,1)
    require(budget['max_windows_per_port']<=budget['max_total_windows'],'Observation window budget mismatch')
    return budget


def validate_observation(config, ranks, max_ns):
    budget=config.get('resource_budget')
    if budget is not None:validate_observation_budget(budget)
    limits=budget or dict(max_ports=16,max_windows_per_port=4096,max_total_windows=16*4096)
    require(set(config)=={'clocks','switches','record_provenance'}|({'resource_budget'} if budget is not None else set()) and
            type(config['record_provenance']) is bool,'Invalid observation fields/provenance flag')
    require(isinstance(config['clocks'],list) and isinstance(config['switches'],list) and
            1<=len(config['switches'])<=limits['max_ports'],'Require observed switches and device clocks')
    clocks={}
    for clock in config['clocks']:
        require(set(clock)=={'device_type','device_id','offset_ns','drift_ppb'} and
                clock['device_type'] in ('rank','switch'),'Invalid clock fields/device type')
        integer(clock['device_id'],'clock device_id',0,2**32-1)
        integer(clock['offset_ns'],'clock offset_ns',I64_MIN,I64_MAX)
        integer(clock['drift_ppb'],'clock drift_ppb',-PPB+1,PPB)
        key=clock['device_type'],clock['device_id']
        require(key not in clocks,'Duplicate device clock')
        local_time(0,clock);local_time(max_ns,clock)
        clocks[key]=clock
    ports=[];switches=set()
    for group in config['switches']:
        require(set(group)=={'switch_id','port_ids','start_local_ns','end_local_ns','period_ns'},'Invalid switch sampling fields')
        switch=integer(group['switch_id'],'switch_id',0,2**32-1)
        require(switch not in switches and ('switch',switch) in clocks,'Duplicate switch or missing switch clock')
        switches.add(switch)
        require(isinstance(group['port_ids'],list) and 1<=len(group['port_ids'])<=limits['max_ports'],'Invalid observed ports')
        for port in group['port_ids']:integer(port,'port_id',0,2**32-1)
        require(len(set(group['port_ids']))==len(group['port_ids']),'Duplicate observation port')
        integer(group['start_local_ns'],'window start',I64_MIN,I64_MAX)
        integer(group['end_local_ns'],'window end',I64_MIN,I64_MAX)
        integer(group['period_ns'],'window period',1,I64_MAX)
        clock=clocks['switch',switch]
        require(local_time(0,clock)<=group['start_local_ns']<group['end_local_ns']<=local_time(max_ns,clock),
                'Local observation interval outside simulated time bounds')
        require((group['end_local_ns']-group['start_local_ns']-1)//group['period_ns']<limits['max_windows_per_port'],'Too many observation windows')
        for port in group['port_ids']:
            ports.append(dict(switch_id=switch,port_id=port,direction='egress',
                **{k:group[k] for k in ('start_local_ns','end_local_ns','period_ns')}))
    require(1<=len(ports)<=limits['max_ports'],'Observation port budget exceeded (legacy: 16)')
    require(sum((p['end_local_ns']-p['start_local_ns']-1)//p['period_ns']+1 for p in ports)<=limits['max_total_windows'],
            'Observation total window budget exceeded')
    require(set(clocks)=={('rank',r) for r in ranks}|{('switch',s) for s in switches},
            'Missing or unexpected rank/switch clocks')
    return clocks,sorted(ports,key=lambda p:(p['switch_id'],p['port_id']))


def backend_observation(config, ranks, max_ns):
    _,ports=validate_observation(config,ranks,max_ns)
    # Capture successful starts throughout the simulation. Local clocks are
    # deliberately absent from the backend input and its event scheduler.
    result=dict(schema_version='moe-ub-port-observation-set/1',ports=[dict(
        schema_version='moe-ub-port-observation/1',switch_id=p['switch_id'],port_id=p['port_id'],
        window_start_ns=0,window_end_ns=max_ns,window_period_ns=max_ns,
        record_provenance=config['record_provenance']) for p in ports])
    if 'resource_budget' in config:
        result.update(schema_version='moe-ub-port-observation-set/2',resource_budget=config['resource_budget'])
        for port in result['ports']:port.update(schema_version='moe-ub-port-observation/2',resource_budget=config['resource_budget'])
    return result


def execution_coverage(config,ranks,max_ns,finished_ns):
    """Private evidence: completeness in time for the explicitly selected ports."""
    clocks,ports=validate_observation(config,ranks,max_ns)
    require(0<=finished_ns<=max_ns,'Finished time outside execution budget')
    partial=[]
    for p in ports:
        clock=clocks['switch',p['switch_id']]
        # Windows are half-open: an event at finished_ns must be included too.
        if p['start_local_ns']>local_time(0,clock) or p['end_local_ns']<=local_time(finished_ns,clock):
            partial.append(dict(switch_id=p['switch_id'],port_id=p['port_id']))
    return dict(schema_version='moe-ub-execution-coverage/1',execution_complete=True,
                execution_budget_ns=max_ns,simulation_end_ns=finished_ns,
                observation_complete=not partial,incomplete_ports=partial,
                scope='time_coverage_of_declared_ports_not_all_network_ports')


def public_config(ranks, ports, resource_budget=None):
    result=dict(schema_version='moe-ub-observed/1',endpoint_schema='moe-ub-endpoint-ops/1',
        port_schema='moe-ub-port-windows/1',rank_ids=ranks,ports=ports,time_unit='ns',
        byte_semantics='ns3_packet_get_size',counter_location='egress_packet_start',clock_resolution_ns=1,
        endpoint_fields=list(ENDPOINT_FIELDS))
    if resource_budget is not None:
        result.update(schema_version='moe-ub-observed/2',limits={k:resource_budget[k] for k in ('max_ports','max_windows_per_port','max_total_windows')})
    return result


def iter_json_lines(path, max_line_bytes=64*1024*1024):
    with path.open('rb') as stream:
        while True:
            line=stream.readline(max_line_bytes+1)
            if not line:break
            require(len(line)<=max_line_bytes,'JSONL record exceeds line budget')
            require(line.strip(),'Blank JSONL record')
            yield json.loads(line)


def json_lines(path):
    return list(iter_json_lines(path))


def write_lines(path, rows):
    with path.open('w') as stream:
        for row in rows:stream.write(json.dumps(row,sort_keys=True)+'\n')


def write_csv(path, fields, rows):
    with path.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)


def iter_csv(path, fields, text_fields=('direction',)):
    with path.open(newline='') as stream:
        reader=csv.DictReader(stream)
        require(reader.fieldnames==list(fields),'Unexpected CSV fields')
        for row in reader:
            require(set(row)==set(fields) and all(v is not None for v in row.values()),'Malformed CSV row')
            for key,value in row.items():
                if key not in text_fields:
                    number=int(value);require(str(number)==value,'Noncanonical CSV integer')
                    row[key]=number
            yield row


def read_csv(path, fields, text_fields=('direction',)):
    return list(iter_csv(path,fields,text_fields))


def validate_observed(directory):
    """Read only these three files. No graph, manifest, clock model, or truth access."""
    directory=Path(directory)
    require(directory.is_dir() and not directory.is_symlink(),'Observed directory must be a real directory')
    expected={'public_config.json','endpoint_ops.jsonl','port_windows.csv'}
    require({p.name for p in directory.iterdir()}==expected,'Missing or unexpected observed file')
    require(all((directory/name).is_file() and not (directory/name).is_symlink() for name in expected),
            'Observed symlinks/non-files are forbidden')
    public=json.loads((directory/'public_config.json').read_text())
    ranks,port_configs,total_windows=validate_public_config(public)
    expected_windows=((p['switch_id'],p['port_id'],'egress',a,b) for p in port_configs for a,b in windows(p))
    port_count=zero_count=0
    for row,expected_row in zip_longest(iter_csv(directory/'port_windows.csv',PORT_FIELDS),expected_windows):
        require(row is not None and expected_row is not None and tuple(row[k] for k in PORT_FIELDS[:5])==expected_row,
                'Missing, duplicated or reordered port windows (missing is not zero)')
        port_count+=1;zero_count+=row['counted_packets']==0
        integer(row['counted_packets'],'counted_packets',0,2**64-1)
        integer(row['counted_packet_bytes'],'counted_packet_bytes',0,2**64-1)
        require((row['counted_packets']==0)==(row['counted_packet_bytes']==0) and
                row['counted_packets']<=row['counted_packet_bytes'],'Invalid packet/byte counts')
    endpoint_count=validate_endpoint_rows(iter_json_lines(directory/'endpoint_ops.jsonl'),ranks)
    return dict(schema_version='moe-ub-observed-validation/1',passed=True,endpoint_records=endpoint_count,
                port_windows=port_count,zero_windows=zero_count,
                access_scope='observed_only')


def validate_public_config(public):
    extended=public.get('schema_version')=='moe-ub-observed/2'
    limits=public.get('limits',dict(max_ports=16,max_windows_per_port=4096,max_total_windows=16*4096))
    require(set(limits)=={'max_ports','max_windows_per_port','max_total_windows'},'Invalid public resource limits')
    for k,v in limits.items():integer(v,k,1)
    require(set(public)==PUBLIC_FIELDS|({'limits'} if extended else set()) and (extended or public['schema_version']=='moe-ub-observed/1') and
        public['endpoint_schema']=='moe-ub-endpoint-ops/1' and public['port_schema']=='moe-ub-port-windows/1' and
        public['endpoint_fields']==list(ENDPOINT_FIELDS) and public['time_unit']=='ns' and
        public['byte_semantics']=='ns3_packet_get_size' and public['counter_location']=='egress_packet_start' and
        type(public['clock_resolution_ns']) is int and public['clock_resolution_ns']==1,'Invalid public field whitelist')
    ranks=public['rank_ids']
    require(isinstance(ranks,list) and bool(ranks) and
            all(type(r) is int and 0<=r<=MAX_RANK_ID for r in ranks) and
            ranks==sorted(set(ranks)),'Invalid public ranks')
    port_configs=public['ports'];points=set();total_windows=0
    require(isinstance(port_configs,list) and 1<=len(port_configs)<=limits['max_ports'],'Invalid public port list')
    for p in port_configs:
        require(set(p)==WINDOW_CONFIG_FIELDS and p['direction']=='egress','Invalid public port fields')
        integer(p['switch_id'],'switch_id',0,2**32-1);integer(p['port_id'],'port_id',0,2**32-1)
        integer(p['start_local_ns'],'start_local_ns',I64_MIN,I64_MAX)
        integer(p['end_local_ns'],'end_local_ns',I64_MIN,I64_MAX)
        integer(p['period_ns'],'period_ns',1,I64_MAX)
        point=p['switch_id'],p['port_id']
        require(point not in points and p['start_local_ns']<p['end_local_ns'] and
            (p['end_local_ns']-p['start_local_ns']-1)//p['period_ns']<limits['max_windows_per_port'],'Invalid or duplicate public sampling interval')
        points.add(point)
        total_windows+=(p['end_local_ns']-p['start_local_ns']-1)//p['period_ns']+1
    require(total_windows<=limits['max_total_windows'],'Public total window budget exceeded')
    return ranks,port_configs,total_windows


def validate_endpoint_rows(rows,ranks):
    endpoint_count=0;last={r:(0,I64_MIN) for r in ranks}
    for row in rows:
        endpoint_count+=1
        require(set(row)==set(ENDPOINT_FIELDS) and type(row['rank_id']) is int and row['rank_id'] in last and
                row['kind'] in ('dispatch','combine','KV_TRANSFER'),'Invalid endpoint field whitelist')
        integer(row['local_record_id'],'local_record_id',1)
        integer(row['start_local_ns'],'start_local_ns',I64_MIN,I64_MAX)
        integer(row['end_local_ns'],'end_local_ns',row['start_local_ns'],I64_MAX)
        number,time=last[row['rank_id']]
        require(row['local_record_id']==number+1 and row['start_local_ns']>=time,'Invalid local endpoint sequence')
        last[row['rank_id']]=row['local_record_id'],row['start_local_ns']
    return endpoint_count
