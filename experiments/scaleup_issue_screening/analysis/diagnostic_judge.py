"""Diagnose an explicit local-counter allowlist; no truth or run readers.

Field ablation uses complete aligned aggregates. Sampling ablation keeps the
same full local fields and a common per-port record cap, with causal decisions.
"""
import argparse
from collections import Counter,defaultdict,deque
import gzip
import json
import math
from pathlib import Path
import random
import sys
from .diagnostic_schema import FIELDS,IX,BITS
from .workflow import digest,host,read,save,CONFIG

INPUTS={'schema.json','windows.jsonl.gz','endpoint_ops.jsonl.gz','endpoint_stages.jsonl.gz','workload.json','queue_histograms.json'}
FIELDSETS={
 'counters':('tx_bytes','tx_packets'),
 'queues':('tx_bytes','tx_packets','voq_byte_ns','fifo_byte_ns','waiting_ns','queue_peak_bytes','fifo_arrival_bytes'),
 'service':tuple(f for f in FIELDS if f not in ('probes','blocked_probes')),
 'credit':FIELDS}


def rows(path):
    with gzip.open(path,'rt') as stream:
        for line in stream:yield json.loads(line)


def classify(values,width,nominal,level,rule):
    x={f:values[IX[f]] for f in FIELDSETS[level]};labels=[]
    if level=='counters':
        if x['tx_bytes']*8*1e9>=rule['busy_fraction']*nominal*width:labels=['high_load_queue_unknown']
    else:
        waiting=x['queue_peak_bytes']>=rule['queue_peak_bytes'] and x['waiting_ns']>=rule['minimum_window_wait_ns']
        if waiting:
            if level=='queues':labels=['queue_cause_unknown']
            else:
                measured=x['completed_packet_bytes']>=rule['minimum_service_bytes'] and x['completed_serial_ns']>0
                slow=measured and \
                    x['completed_packet_bytes']*8*1e9<rule['service_rate_fraction']*nominal*x['completed_serial_ns']
                if slow:labels.append('local_service_deficit')
                if level=='credit' and x['blocked_probes']:labels.append('credit_gate_witness_root_unknown')
                if not labels:
                    labels.append(('busy_competition' if measured else 'busy_service_rate_unknown') if
                        x['queued_busy_ns']>=rule['busy_fraction']*x['waiting_ns'] else 'arrival_or_arbitration_or_credit_unknown')
        elif level=='credit' and x['blocked_probes']:labels=['credit_gate_witness_root_unknown']
    return labels


def merge(group):
    value=[0]*len(FIELDS)
    for row in group:
        for i,x in enumerate(row):value[i]=max(value[i],x) if i==IX['queue_peak_bytes'] else value[i]+x
    return value


def sample(values,period,kind,seed,nominal,rule):
    """Return causal report (source window, delivery window) and overflow count."""
    n=len(values);horizon=rule['report_budget_horizon_ns']
    if horizon%1000 or not n*1000<=horizon<=1000000:raise ValueError('Capture exceeds predeclared collection budget horizon')
    cap=math.ceil(horizon/1000/period);reported=[];overflow=0
    rng=random.Random(seed);last=deque(maxlen=rule['event_pre_windows']);post=0;next_trigger=0;seen=set()
    for i,row in enumerate(values):
        requested=[]
        if kind=='periodic':
            if i%period==0:requested=[i]
        elif kind=='random':
            if rng.random()<1/period:requested=[i]
        elif kind=='event':
            hit=bool(classify(row,1000,nominal,'credit',rule))
            if hit and i>=next_trigger:
                requested=[*last,i];post=rule['event_post_windows'];next_trigger=i+rule['event_cooldown_periods']*period
            elif post:
                requested=[i];post-=1
            last.append(i)
        else:raise ValueError('Unknown strategy')
        for j in requested:
            if j in seen:continue
            seen.add(j)
            if len(reported)<cap:reported.append((j,i))
            else:overflow+=1
    return reported,overflow


def judge(directory,rule_path,output):
    directory,output=Path(directory),Path(output)
    if output.exists():raise ValueError('Diagnosis output must be new')
    if {p.name for p in directory.iterdir()}!=INPUTS:raise ValueError('Diagnostic input allowlist mismatch')
    meta=read(directory/'schema.json');rule=read(rule_path);hist=read(directory/'queue_histograms.json')
    if set(meta)!={'schema_version','start_ns','end_ns','window_ns','fields','ports','encoding','semantics','hardware_scope','rank_nodes'}:
        raise ValueError('Diagnostic metadata allowlist mismatch')
    if meta['schema_version']!='issue-local-measurements/3' or meta['fields']!=list(FIELDS) or meta['start_ns']!=0 or meta['window_ns']!=1000:
        raise ValueError('Unsupported local measurement contract')
    end=meta['end_ns'];n=math.ceil(end/1000);points={(p['switch_id'],p['port_id']):p for p in meta['ports']}
    if not 0<end<=1000000 or not 0<len(points)<=4096:raise ValueError('Invalid declared study coverage')
    if any(any(type(v)is not int or not 0<=v<65536 for v in k) for k in points):raise ValueError('Resource ID exceeds accounted record width')
    data={k:{} for k in points};previous=None
    for row in rows(directory/'windows.jsonl.gz'):
        if not isinstance(row,list) or len(row)!=3+len(FIELDS) or any(type(x)is not int or x<0 for x in row):raise ValueError('Invalid counter row')
        if any(value>=2**BITS[field] for field,value in zip(FIELDS,row[3:])):raise ValueError('Counter exceeds accounted field width')
        key=tuple(row[:2]);index=row[2];order=(*key,index)
        if key not in points or index>=n or (previous is not None and order<=previous):raise ValueError('Unordered/unknown local window')
        previous=order;data[key][index]=row[3:]
    endpoints=list(rows(directory/'endpoint_ops.jsonl.gz'))
    for r in endpoints:
        if set(r)!={'rank_id','local_record_id','kind','start_local_ns','end_local_ns'}:raise ValueError('Private endpoint fields are forbidden')
    stages=list(rows(directory/'endpoint_stages.jsonl.gz'));stage_by=defaultdict(dict)
    for r in stages:
        if set(r)!={'rank_id','local_record_id','stage','time_local_ns','event_count'}:raise ValueError('Private stage fields are forbidden')
        stage_by[r['rank_id'],r['local_record_id']][r['stage']]=dict(time_ns=r['time_local_ns'],count=r['event_count'])
    work=read(directory/'workload.json')
    if set(work)!={'schema_version','meaning','operations'} or work['schema_version']!='issue-software-local-work/1':raise ValueError('Invalid software workload')
    work_by={}
    for w in work['operations']:
        if not set(w)<={'rank_id','local_record_id','kind','send_payload_bytes','receive_payload_bytes','send_transfers','receive_transfers'}:raise ValueError('Private workload identity')
        work_by[w['rank_id'],w['local_record_id']]=w
    node_ranks=defaultdict(list)
    for r in meta['rank_nodes']:node_ranks[r['node_id']].append(r['rank_id'])
    all_ranks=sorted({r['rank_id'] for r in endpoints})
    modes={f'{level}-{period}us':dict(fieldset=level,kind='aggregate',period=period) for level in FIELDSETS for period in rule['periods_us']}
    modes.update({f'{kind}-{period}us':dict(fieldset='credit',kind=kind,period=period) for kind in ('periodic','random','event') for period in rule['strategy_periods_us']})
    modes['counter_histogram']=dict(fieldset='counters',kind='histogram',period=None)
    predictions={m:[] for m in modes};costs={m:Counter() for m in modes};peaks={m:Counter() for m in modes}
    progress={};histograms={(p['node_id'],p['port_id']):p['counts'] for p in hist['ports']}
    if set(histograms)!=set(points) or hist['period_ns']!=1024 or hist['phase_ns']!=0:raise ValueError('Histogram coverage mismatch')
    for key,p in sorted(points.items()):
        values=[data[key].get(i,[0]*len(FIELDS)) for i in range(n)]
        total=sum(v[IX['tx_bytes']] for v in values);cumulative=0;t90=None
        for i,v in enumerate(values):
            cumulative+=v[IX['tx_bytes']]
            if total and t90 is None and cumulative>=.9*total:t90=min(end,(i+1)*1000)
        progress[key]=(total,t90)
        possible=node_ranks.get(p['peer_node'],[]) if p['role']=='device_bound' else all_ranks
        for mode,spec in modes.items():
            period=spec['period'];level=spec['fieldset'];kind=spec['kind'];cost=costs[mode]
            width_bytes=(96+sum(BITS[f] for f in FIELDSETS[level]))//8
            reports=[]
            if kind=='aggregate':
                reports=[(i,min(n,i+period),min(n,i+period)-1,merge(values[i:i+period])) for i in range(0,n,period)]
            elif kind=='histogram':
                bins=histograms[key];cost['histogram_reads']+=sum(bins.values());cost['histogram_bytes']+=33*4+4
                if any(int(b)>=14 and count for b,count in bins.items()):
                    predictions[mode].append(dict(node_id=key[0],port_id=key[1],start_ns=0,end_ns=end,
                        labels=['queue_time_and_cause_unknown'],possible_ranks=possible,endpoint_records='all_intersecting_operations_not_exact_affected_set'))
                # Complete end-of-capture bytes and common endpoint records are
                # retained even when the histogram does not nominate this port.
                cost['records']+=1;cost['report_bytes']+=width_bytes;peaks[mode][n-1]+=width_bytes
                continue
            else:
                seed=rule['random_seed']+(key[0]<<32)+key[1]
                chosen,overflow=sample(values,period,kind,seed,p['rate_bps'],rule)
                cost['overflow_records']+=overflow
                reports=[(i,i+1,at,values[i]) for i,at in chosen]
                cost['base_windows_examined']+=n
                cost['record_cap']+=math.ceil(rule['report_budget_horizon_ns']/1000/period)
                if kind=='event':cost['ring_bytes']+=rule['event_pre_windows']*width_bytes
            for a,b,at,value in reports:
                begin=a*1000;finish=min(end,b*1000)
                cost['records']+=1;cost['report_bytes']+=width_bytes;peaks[mode][at]+=width_bytes
                labels=classify(value,finish-begin,p['rate_bps'],level,rule)
                if labels:
                    predictions[mode].append(dict(node_id=key[0],port_id=key[1],start_ns=begin,end_ns=finish,
                        reported_at_ns=min(end,(at+1)*1000),labels=labels,possible_ranks=possible,
                        evidence={f:value[IX[f]] for f in FIELDSETS[level]}))
    # Strong counter-only alternative: compare complete progress with actual
    # carried bytes for the same peer, never assume equal spraying.
    groups=defaultdict(list);pairs=[]
    for key,p in points.items():groups[tuple(p['peer_group'])].append(key)
    for group,keys in sorted(groups.items()):
        for i,a in enumerate(sorted(keys)):
            for b in sorted(keys)[i+1:]:
                wa,ta=progress[a];wb,tb=progress[b]
                if min(wa,wb)>0 and abs(wa-wb)/max(wa,wb)<=rule['peer_work_relative_difference'] and abs(ta-tb)>=rule['peer_progress_gap_ns']:
                    pairs.append(dict(outlets=[a,b],packet_bytes=[wa,wb],t90_ns=[ta,tb],
                        diagnosis='arrival_distribution_or_service_unknown',candidate_resources=2))
    output.mkdir(parents=True)
    for mode,preds in predictions.items():
        for row in preds:
            possible=set(row['possible_ranks'])
            active=[r for r in endpoints if r['rank_id'] in possible and r['start_local_ns']<row['end_ns'] and r['end_local_ns']>row['start_ns']]
            row['possible_endpoint_records']=[[r['rank_id'],r['local_record_id']] for r in active]
            row['business_scope']='active_logical_operations_not_proven_affected' if active else 'protocol_or_no_visible_active_operation'
        with gzip.open(output/(mode+'.jsonl.gz'),'wt') as stream:
            for row in preds:stream.write(json.dumps(row,separators=(',',':'))+'\n')
        cost=costs[mode];spec=modes[mode]
        for field in ('records','report_bytes','histogram_bytes','histogram_reads','base_windows_examined',
                      'overflow_records','record_cap','ring_bytes'):cost.setdefault(field,0)
        cost['peak_report_bytes_per_1us']=max(peaks[mode].values(),default=0)
        cost['mean_report_bytes_per_second']=cost['report_bytes']*1e9/end
        cost['counter_bank_bytes']=len(points)*sum(BITS[f] for f in FIELDSETS[spec['fieldset']])//8
        kind=spec['kind']
        cost['counter_snapshot_reads']=(cost['records']+len(points) if kind=='aggregate' else
            cost['base_windows_examined'] if kind=='event' else 2*cost['records'])
        cost['local_clock_or_rng_steps']=cost['base_windows_examined'] if kind in ('event','random') else cost['records']
        cost['histogram_state_bytes']=len(points)*33*4 if kind=='histogram' else 0
        # Independent per-port streams use the actual CPython MT state, not an
        # unimplemented small hardware RNG. Event deduplication is represented
        # as a bounded bitmap of the predeclared base windows.
        cost['rng_state_bytes']=len(points)*len(random.Random(0).getstate()[1])*4 if kind=='random' else 0
        cost['event_deduplication_state_bytes']=len(points)*math.ceil(rule['report_budget_horizon_ns']/1000/8) if kind=='event' else 0
        cost['sampling_control_state_bytes']=len(points)*(20 if kind=='event' else 8 if kind in ('periodic','random') else 0)
        cost['common_endpoint_records']=len(endpoints);cost['common_endpoint_payload_bytes']=len(endpoints)*32
        cost['common_stage_records']=len(stages);cost['common_stage_payload_bytes']=len(stages)*24
        cost['common_workload_payload_bytes']=len(work_by)*40
        cost['histogram_bytes']+=0
    save(output/'progress-pairs.json',pairs)
    save(output/'application-baseline.json',[dict(**r,stages=stage_by[r['rank_id'],r['local_record_id']],
        workload=work_by.get((r['rank_id'],r['local_record_id']),{})) for r in endpoints])
    save(output/'summary.json',dict(schema_version='issue-diagnostic-predictions/1',coverage_port_ns=len(points)*end,
        ports=len(points),end_ns=end,input_sha256={n:digest(directory/n) for n in sorted(INPUTS)},
        rule_sha256=digest(rule_path),program_sha256=digest(__file__),modes=modes,
        predictions={m:len(v) for m,v in predictions.items()},costs={m:dict(v) for m,v in costs.items()},
        counter_progress_ambiguous_pairs=len(pairs),endpoint_scope='static possible ranks; no private packet/operation mapping',
        collection_budget_horizon_ns=rule['report_budget_horizon_ns'],
        random_state_semantics='Independent CPython MT19937 streams: 625 uint32 slots including index; no Gaussian cache used',
        cost_boundaries='Counter widths and local replay costs, not JSON sizes or hardware implementation. Event counters still update continuously; source update counts are measured separately by evaluator.'))
    print('Diagnosed',len(points),'ports in',len(modes),'fixed conditions')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',required=True,type=Path)
    p.add_argument('--rule',type=Path,default=CONFIG/'observation/diagnostic-rule.json');p.add_argument('--output',required=True,type=Path)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:judge(a.input,a.rule,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],str(a.output)+'-resources',**host())
        if r['status']!='completed':raise SystemExit(r['status'])

if __name__=='__main__':main()
