"""Queue-state resampling and checks, using only the declared waiting objects."""
import math

from .observation import local_time,PORT_FIELDS
from .plan import integer,require

OBJECTS=('voq_waiting','fifo_waiting')
QUEUE_FIELDS=PORT_FIELDS[:5]+tuple(kind+suffix for kind in OBJECTS for suffix in ('_byte_ns','_mean_bytes','_peak_bytes'))


def check_values(row,duration):
    for kind in OBJECTS:
        area=integer(row[kind+'_byte_ns'],'waiting-byte area',0,2**128-1)
        peak=integer(row[kind+'_peak_bytes'],'waiting-byte peak',0,2**64-1)
        mean=row[kind+'_mean_bytes']
        require(type(mean) in (int,float) and math.isfinite(mean) and mean>=0 and area<=peak*duration and
                math.isclose(mean,area/duration,rel_tol=1e-12,abs_tol=1e-12),'Queue integral/mean/peak mismatch')


def resample(events,port,clock,max_ns):
    """Bounded stream: intersect constant-state intervals with local windows."""
    start,end,period=(port[k] for k in ('start_local_ns','end_local_ns','period_ns'))
    current=None;area=[0,0];peak=[0,0];previous=0;state=None;last=local_time(0,clock)
    def output(index):
        duration=min(period,end-(start+index*period))
        return dict(index=index,**{k+suffix:values[j] for j,k in enumerate(OBJECTS)
            for suffix,values in (('_byte_ns',area),('_mean_bytes',[a/duration for a in area]),('_peak_bytes',peak))})
    for event in events:
        require(set(event)=={'time_ns','voq_waiting_bytes','fifo_waiting_bytes'},'Invalid queue state fields')
        time=integer(event['time_ns'],'queue state time',previous,max_ns);previous=time
        now=local_time(time,clock)
        after=[integer(event[k+'_bytes'],k,0,2**64-1) for k in OBJECTS]
        if state is None:
            require(time==0,'Queue state capture lacks initial time-zero state');state=after
        cursor=max(last,start);stop=min(now,end)
        if any(state):
            while cursor<stop:
                index=(cursor-start)//period;right=min(stop,start+(index+1)*period)
                if current is not None and current!=index:
                    if any(peak):yield output(current)
                    area=[0,0];peak=[0,0]
                current=index
                area=[area[j]+state[j]*(right-cursor) for j in range(2)]
                peak=[max(peak[j],state[j]) for j in range(2)];cursor=right
        state=after;last=now
        if start<=now<end:
            index=(now-start)//period
            if current is not None and current!=index:
                if any(peak):yield output(current)
                area=[0,0];peak=[0,0]
            current=index;peak=[max(peak[j],state[j]) for j in range(2)]
    require(state is not None and state==[0,0],'Queue state capture is absent or ends with unresolved waiting bytes')
    if current is not None and any(peak):yield output(current)


def export_queue(meta,port,manifest,records,writer,max_ns):
    require(manifest['schema_version']=='moe-ub-port-records/2' and
            {'queue-state-events.jsonl','queue-windows.jsonl'}<=set(manifest['fields']),
            'Queue statistics require captured state history and initial state; rerun simulation')
    clock=meta['clocks']['switch',port['switch_id']]
    selected=dict(schema_version='moe-ub-queue-sampling/1',
        **{k:port[k] for k in ('start_local_ns','end_local_ns','period_ns')},
        **{k:clock[k] for k in ('offset_ns','drift_ppb')})
    same=selected==manifest['queue_sampling'];base=iter(records('queue-windows.jsonl')) if same else None
    count=0
    for value in resample(records('queue-state-events.jsonl'),port,clock,max_ns):
        index=value.pop('index');left=port['start_local_ns']+index*port['period_ns'];right=min(left+port['period_ns'],port['end_local_ns'])
        check_values(value,right-left)
        if same:
            actual=next(base,None);require(actual is not None and actual['index']==index,'Missing or different live queue window')
            require(set(actual)=={'index'}|{k+suffix for k in OBJECTS for suffix in ('_byte_ns','_mean_bytes','_peak_bytes')},
                    'Unknown live queue window fields')
            for kind in OBJECTS:
                text=actual[kind+'_byte_ns'];require(type(text) is str and str(int(text))==text,'Invalid exact area text')
                actual[kind+'_byte_ns']=int(text)
                require(actual[kind+'_byte_ns']==value[kind+'_byte_ns'] and
                        actual[kind+'_peak_bytes']==value[kind+'_peak_bytes'],'Live queue statistics differ from state history')
            check_values(actual,right-left)
        writer.add('observed','queue_windows.csv',dict(switch_id=port['switch_id'],port_id=port['port_id'],direction='egress',
            local_window_start_ns=left,local_window_end_ns=right,**value));count+=1
    if same:require(next(base,None) is None,'Extra live queue window')
    return count
