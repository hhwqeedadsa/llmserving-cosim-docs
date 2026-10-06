"""Stream exact aggregate coarsening or explicitly retained detailed intervals."""
from collections import Counter

from .observation import local_time
from .observation_recording import CATEGORIES,aligned,detail_range,first_global
from .plan import integer,require
from .queue_statistics import OBJECTS,check_values,resample

CLASSES=('business_payload_bytes','application_metadata_bytes','padding_bytes',
         'data_protocol_overhead_bytes','control_packet_bytes','unattributed_bytes')
COUNTS=('counted_packets','counted_packet_bytes')+CLASSES


def bounds(port,index):
    left=port['start_local_ns']+index*port['period_ns']
    return dict(switch_id=port['switch_id'],port_id=port['port_id'],direction='egress',
        local_window_start_ns=left,local_window_end_ns=min(left+port['period_ns'],port['end_local_ns']))


def check_counts(row):
    for k in COUNTS:integer(row[k],k,0,2**64-1)
    require(row['counted_packets']>0 and row['counted_packets']<=row['counted_packet_bytes'] and
            sum(row[k] for k in CLASSES)==row['counted_packet_bytes'] and row['unattributed_bytes']==0,
            'Aggregate packet/classification conservation failed')


def check_attribution(row,attribution,categories):
    totals=Counter()
    for (_,kind),value in attribution.items():totals[kind]+=value
    if 'business_data' in categories:require(totals['business_data']==row['business_payload_bytes'],'Missing classified business provenance')
    metadata=sum(totals[k] for k in CATEGORIES[1:])
    require(metadata<=row['application_metadata_bytes'],'Classified metadata exceeds actual metadata')
    if set(CATEGORIES[1:])<=set(categories):require(metadata==row['application_metadata_bytes'],'Missing classified metadata provenance')


class PacketOutput:
    def __init__(self,port,writer,padding,categories,max_operations):
        self.port=port;self.writer=writer;self.padding=padding;self.categories=categories;self.limit=max_operations*len(CATEGORIES)
        self.current=None;self.counts=Counter();self.attribution=Counter()
        self.windows=self.packets=self.bytes=self.peak_attribution=0

    def add(self,index,counts,attribution):
        if self.current is not None and index!=self.current:self.flush()
        self.current=index;self.counts.update({k:counts[k] for k in COUNTS});self.attribution.update(attribution)
        require(len(self.attribution)<=self.limit,'Coarsened operation attribution budget exceeded')
        self.peak_attribution=max(self.peak_attribution,len(self.attribution))

    def flush(self):
        if self.current is None:return
        b=bounds(self.port,self.current);check_counts(self.counts);check_attribution(self.counts,self.attribution,self.categories)
        self.writer.add('observed','port_windows.csv',dict(**b,**{k:self.counts[k] for k in COUNTS[:2]}))
        self.writer.add('metrics','byte-classification.jsonl',dict(**b,**{k:self.counts[k] for k in CLASSES if self.padding or k!='padding_bytes'}))
        for (operation,kind),value in sorted(self.attribution.items()):
            self.writer.add('truth','message_attribution_windows.csv',dict(**b,operation_id=operation,message_category=kind,application_bytes=value))
            if kind=='business_data':self.writer.add('truth','attribution_windows.csv',dict(**b,operation_id=operation,business_payload_bytes=value))
        self.windows+=1;self.packets+=self.counts['counted_packets'];self.bytes+=self.counts['counted_packet_bytes']
        self.current=None;self.counts.clear();self.attribution.clear()


def source_index(row,source,previous):
    count=(source['end_local_ns']-source['start_local_ns']-1)//source['period_ns']+1
    return integer(row['index'],'aggregate window index',previous+1,count-1)


def aggregate_packets(records,source,target,meta,index,output):
    categories=output.categories;known=set(index.operations)
    provenance=iter(records('truth/message-windows.jsonl')) if meta['provenance'] else iter(())
    pending=next(provenance,None);previous=-1;packets=whole=0
    for row in records('aggregate-windows.jsonl'):
        require(set(row)==set(COUNTS)|{'index'},'Invalid aggregate packet fields')
        at=source_index(row,source,previous);previous=at;check_counts(row);attribution={}
        while pending is not None and pending['index']==at:
            require(set(pending)=={'index','operation_id','message_category','application_bytes'} and
                    pending['operation_id'] in known and pending['message_category'] in categories,'Invalid aggregate application identity/category')
            key=pending['operation_id'],pending['message_category'];require(key not in attribution,'Repeated application attribution')
            attribution[key]=integer(pending['application_bytes'],'application bytes',1,row['counted_packet_bytes']);pending=next(provenance,None)
        require(pending is None or type(pending['index']) is int and pending['index']>at,'Unordered application aggregate')
        check_attribution(row,attribution,categories);packets+=row['counted_packets'];whole+=row['counted_packet_bytes']
        left=source['start_local_ns']+at*source['period_ns']
        if target['start_local_ns']<=left<target['end_local_ns']:
            output.add((left-target['start_local_ns'])//target['period_ns'],row,attribution)
    require(pending is None,'Extra aggregate application attribution');output.flush()
    return packets,whole


def detailed_packets(records,capture,target,clock,meta,index,output):
    provenance=iter(records('truth/classified_packet_ranges.jsonl')) if meta['provenance'] else iter(())
    pending=next(provenance,None);previous=capture['start_ns'];packets=whole=0
    for event in records('packet-events.jsonl'):
        require(set(event) in ({'sim_time_ns','packet_uid','counted_packet_bytes','business_payload_bytes'},
            {'sim_time_ns','packet_uid','counted_packet_bytes','business_payload_bytes','application_metadata_bytes','padding_bytes'}),'Invalid detailed packet fields')
        now=integer(event['sim_time_ns'],'detailed packet time',previous,capture['end_ns']-1);previous=now
        integer(event['packet_uid'],'packet uid',0,2**64-1);size=integer(event['counted_packet_bytes'],'whole bytes',1,2**32-1)
        business=integer(event['business_payload_bytes'],'business bytes',0,size)
        metadata=integer(event.get('application_metadata_bytes',0),'metadata bytes',0,size-business)
        padding=integer(event.get('padding_bytes',0),'padding bytes',0,size-business-metadata)
        payload=business+metadata+padding
        row=dict(counted_packets=1,counted_packet_bytes=size,business_payload_bytes=business,application_metadata_bytes=metadata,
                 padding_bytes=padding,data_protocol_overhead_bytes=size-payload if payload else 0,
                 control_packet_bytes=0 if payload else size,unattributed_bytes=0)
        attribution=Counter();covered=[];key=now,event['packet_uid'],size
        while pending is not None and (pending['sim_time_ns'],pending['packet_uid'],pending['counted_packet_bytes'])==key:
            require(set(pending)=={'sim_time_ns','packet_uid','counted_packet_bytes','ub_task_id','transfer_id','operation_id',
                    'task_byte_range','packet_byte_range','message_category'},'Invalid classified packet source fields')
            transfer=index.get(pending['transfer_id']);category=pending['message_category']
            require(transfer is not None and category in output.categories and transfer['operation_id']==pending['operation_id'] and
                    transfer['ub_task_id']==pending['ub_task_id'],'Unknown classified packet source')
            a,b=pending['task_byte_range'];c,d=pending['packet_byte_range']
            for v in (a,b,c,d):integer(v,'classified byte boundary',0,2**32-1)
            require(0<=a<b<=transfer['payload_bytes'] and 0<c<d<=size and b-a==d-c and
                    not any(c<y and x<d for x,y in covered),'Invalid/overlapping classified byte range')
            region_kind='business' if category=='business_data' else 'metadata'
            require(any(r['kind']==region_kind and r['begin']<=a<b<=r['end'] for r in transfer['payload_regions']) and
                    (category=='business_data' or category==index.category(pending['transfer_id'])),
                    'Packet application category differs from actual planned message')
            covered.append((c,d));attribution[pending['operation_id'],category]+=b-a;pending=next(provenance,None)
        check_attribution(row,attribution,output.categories);local=local_time(now,clock)
        if target['start_local_ns']<=local<target['end_local_ns']:
            output.add((local-target['start_local_ns'])//target['period_ns'],row,attribution)
        packets+=1;whole+=size
    require(pending is None,'Extra classified packet sources');output.flush();return packets,whole


def export_queues(records,source,target,clock,method,capture,writer,max_ns):
    def emit(index,value):
        b=bounds(target,index);check_values(value,b['local_window_end_ns']-b['local_window_start_ns'])
        writer.add('observed','queue_windows.csv',dict(**b,**value))
    if method=='detailed':
        for row in resample(records('queue-state-events.jsonl'),target,clock,max_ns,capture=capture):
            at=row.pop('index');emit(at,row)
        return
    current=None;values={k+suffix:0 for k in OBJECTS for suffix in ('_byte_ns','_peak_bytes')};previous=-1
    def flush():
        if current is None:return
        b=bounds(target,current);length=b['local_window_end_ns']-b['local_window_start_ns']
        emit(current,dict(values,**{k+'_mean_bytes':values[k+'_byte_ns']/length for k in OBJECTS}))
    for row in records('queue-windows.jsonl'):
        require(set(row)=={'index'}|{k+s for k in OBJECTS for s in ('_byte_ns','_mean_bytes','_peak_bytes')},'Invalid queue aggregate fields')
        at=source_index(row,source,previous);previous=at
        left=source['start_local_ns']+at*source['period_ns'];right=min(left+source['period_ns'],source['end_local_ns'])
        for k in OBJECTS:
            text=row[k+'_byte_ns'];require(type(text) is str and str(int(text))==text,'Invalid queue area text');row[k+'_byte_ns']=int(text)
        check_values(row,right-left);require(any(row[k+'_peak_bytes'] for k in OBJECTS),'Empty stored queue aggregate')
        if not target['start_local_ns']<=left<target['end_local_ns']:continue
        selected=(left-target['start_local_ns'])//target['period_ns']
        if current is not None and current!=selected:
            flush();values={k+suffix:0 for k in OBJECTS for suffix in ('_byte_ns','_peak_bytes')}
        current=selected
        for k in OBJECTS:
            values[k+'_byte_ns']+=row[k+'_byte_ns'];values[k+'_peak_bytes']=max(values[k+'_peak_bytes'],row[k+'_peak_bytes'])
    flush()


def export_port(meta,port,manifest,records,writer,index,max_ns,observation,audit,native,limits):
    source=manifest['queue_sampling'];policy=manifest['recording'];requested=observation['recording']
    expected=dict(schema_version='moe-ub-port-recording/1',detail_range=detail_range(requested,port,max_ns),
        **{k:requested[k] for k in ('truth_categories','max_detail_events_per_port','max_window_attribution_entries')})
    require(policy==expected and audit['recording']==policy,'Export differs from captured recording policy')
    require(manifest['logical_windows']==(source['end_local_ns']-source['start_local_ns']-1)//source['period_ns']+1,
            'Aggregate window declaration mismatch')
    clock=meta['clocks']['switch',port['switch_id']];old_clock={k:source[k] for k in ('offset_ns','drift_ppb')}
    current_clock={k:clock[k] for k in ('offset_ns','drift_ppb')}
    method='aggregate' if aligned(source,port,old_clock,current_clock) else 'detailed';capture=policy['detail_range']
    if method=='detailed':require(capture is not None and first_global(port['start_local_ns'],clock)>=capture['start_ns'] and
        first_global(port['end_local_ns'],clock)<=capture['end_ns'],'Requested sampling requires uncaptured detailed queue and packet history')
    details={'packet-events.jsonl','queue-state-events.jsonl','egress-queue-audit.jsonl'} if capture else set()
    if capture and meta['provenance']:details.add('truth/classified_packet_ranges.jsonl')
    names=details|{'aggregate-windows.jsonl','queue-windows.jsonl'}|({'truth/message-windows.jsonl'} if meta['provenance'] else set())
    require(set(manifest['fields'])==names,'Unexpected or missing capture records')
    raw_bytes=sum(manifest['fields'][n]['raw_bytes'] for n in details);detail_events=sum(manifest['fields'][n]['records'] for n in details)
    require(raw_bytes==audit['event_bytes']<=observation['resource_budget']['max_event_bytes_per_port'] and
            detail_events==audit['detail_events']<=policy['max_detail_events_per_port'],'Detailed capture byte/event budget mismatch')
    output=PacketOutput(port,writer,meta['padding_present'],policy['truth_categories'],limits['max_operations'])
    if method=='aggregate':
        packets,whole=aggregate_packets(records,source,port,meta,index,output)
        require(packets==audit['aggregate_packets'] and whole==audit['aggregate_bytes'],'Live packet aggregate totals differ from audit')
    else:detailed_packets(records,capture,port,clock,meta,index,output)
    export_queues(records,source,port,clock,method,capture,writer,max_ns)
    require(native['all_successful_start_bytes']==native['native_tx_bytes_delta'] and output.packets<=native['all_successful_start_packets'] and
            output.bytes<=native['all_successful_start_bytes'],'Retained observations exceed native counters')
    return dict(audit=dict(switch_id=port['switch_id'],port_id=port['port_id'],all_counted_packets=native['all_successful_start_packets'],
        all_counted_packet_bytes=native['all_successful_start_bytes'],included_packets=output.packets,included_packet_bytes=output.bytes,
        wire_and_time_fnv1a64=native['wire_and_time_fnv1a64'],sampling_source=method),
        nonzero_windows=output.windows,max_attribution=output.peak_attribution)
