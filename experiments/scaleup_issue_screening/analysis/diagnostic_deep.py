"""Private packet, stage-work and complete-gate checks of the six fixed cases."""
import argparse
from collections import Counter,defaultdict
import gzip
import json
from pathlib import Path
import sys
from moe_ub_cosim.record_storage import iter_port_records,iter_result
from .diagnostic_judge import rows
from .network_packets import overlap
from .workflow import CONFIG,digest,host,read,save


def distribution(values):
    values=sorted(values)
    return dict(n=len(values),sum=sum(values),min=min(values),median=values[len(values)//2],
        p95=values[min(len(values)-1,int(.95*len(values)))],max=max(values)) if values else dict(n=0)


def progress(packets,time_field,weight='business_bytes'):
    selected=sorted((p[time_field],p[weight]) for p in packets if time_field in p and p.get(weight,0)>0)
    total=sum(n for _,n in selected);result={'bytes':total}
    for q in (.5,.9,.99):
        count=0;value=None
        for t,n in selected:
            count+=n
            if count>=total*q:value=t;break
        result['t'+str(int(q*100))+'_ns']=value
    return result


def review(case,prepared,business,output):
    case,prepared,business,output=map(Path,(case,prepared,business,output))
    if output.exists():raise ValueError('Deep review output must be new')
    run=case/'run';ex=run/'raw/execution';seal=read(prepared/'summary.json');gates=read(business/'summary.json')
    if seal['source_manifest_sha256']!=digest(run/'manifest.json') or gates['manifest_sha256']!=seal['source_manifest_sha256']:
        raise ValueError('Deep review source mismatch')
    if digest(prepared/'private/packets.jsonl.gz')!=seal['private_sha256']['packets.jsonl.gz']:raise ValueError('Packet evidence changed')
    spec=read(CONFIG/'scenarios/mechanism-cases.json');selection=read(case/'selection.json');keys=set(map(tuple,selection['detail_ports']))
    net=read(run/'inputs/network/backend.json');links={}
    for l in net['topology']['links']:
        for a,b in (('src','dst'),('dst','src')):links[l[a],l[a+'_port']]=dict(peer=l[b],delay=l['delay_ns'])
    identities={};busy={};native_counts={};all_starts={}
    for key in keys:
        folder=ex/'ports'/f'{key[0]}-{key[1]}';identity=defaultdict(list);intervals=[];count=0;starts=[]
        for r in iter_port_records(ex,folder,'truth/classified_packet_ranges.jsonl'):identity[r['packet_uid']].append(r)
        for r in iter_port_records(ex,folder,'egress-queue-audit.jsonl'):
            if r['event']=='tx_start':
                intervals.append((r['time_ns'],r['busy_until_ns']));count+=1
                starts.append(dict(tx_start_ns=r['time_ns'],packet_bytes=r['packet_bytes']))
        identities[key]=identity;busy[key]=intervals;native_counts[key]=count;all_starts[key]=starts
    critical=defaultdict(list)
    for gate in gates['reviewed_gates']:
        for node in gate['completion']['latest_completion_dag']:
            if node.get('transfer_id') is not None:
                critical[node['transfer_id']].append(dict(operation_id=gate['operation_id'],rank_id=gate['rank_id'],
                    is_operation_latest_consumer=gate['is_latest'],
                    node={k:node[k] for k in ('node_id','name','kind','start_ns','end_ns','delivery_condition') if k in node}))
    actual={t['transfer_id']:t for t in iter_result(ex,'transfers')}
    packets=defaultdict(list)
    for p in rows(prepared/'private/packets.jsonl.gz'):
        key=p['node_id'],p['port_id'];found=identities[key].get(p['packet_uid'],[])
        tids={r['transfer_id'] for r in found}
        if len(tids)>1:raise ValueError('Ambiguous native packet business identity')
        p['categories']=dict(Counter({category:sum(r['packet_byte_range'][1]-r['packet_byte_range'][0] for r in found if r['message_category']==category)
            for category in {r['message_category'] for r in found}}))
        p['business_bytes']=p['categories'].get('business_data',0)
        p['fifo_wait_ns']=p['fifo_dequeue_ns']-p['fifo_enqueue_ns']
        if 'network_arrival_ns' in p:
            p['arrival_to_fifo_ns']=p['fifo_enqueue_ns']-p['network_arrival_ns']
            p['arrival_to_tx_ns']=p['tx_start_ns']-p['network_arrival_ns']
            p['busy_during_wait_ns']=overlap(busy[key],p['network_arrival_ns'],p['tx_start_ns'])
        if tids:
            tid=next(iter(tids));p['transfer_id']=tid;p['operation_id']=found[0]['operation_id']
            p['necessary_gate_references']=critical.get(tid,[])
            p['downstream_wire_arrival_ns']=p['tx_start_ns']+p['completion_delay_ns']+links[key]['delay']
            p['actual_transfer_data_ready_ns']=actual[tid]['data_ready_ns']
            p['is_final_wire_arrival_for_transfer']=links[key]['peer']==actual[tid]['dst_ub_node'] and p['downstream_wire_arrival_ns']==actual[tid]['data_ready_ns']
        packets[key].append(p)
    outcomes=[]
    for c in spec['cases']:
        s=c['selection'];result=dict(case_id=c['case_id'],problem=c['problem'],fixed_selection=s)
        if 'outlets' not in s:
            key=s['switch_id'],s['port_id'];all_packets=packets[key];a,b=s['start_ns'],s['end_ns']
            selected=[p for p in all_packets if a<=p['tx_start_ns']<b or
                p.get('network_arrival_ns',p['fifo_enqueue_ns'])<b and p['tx_start_ns']>a]
            result.update(selected_packets=len(selected),whole_outlet_packets=len(all_packets),actual_all_tx_packets=native_counts[key],
                selected_business_packets=sum(p['business_bytes']>0 for p in selected),
                interval_wait_metrics={field:distribution([p[field] for p in selected if field in p]) for field in
                    ('fifo_wait_ns','arrival_to_fifo_ns','arrival_to_tx_ns','busy_during_wait_ns')},
                whole_wait_metrics={field:distribution([p[field] for p in all_packets if field in p]) for field in ('fifo_wait_ns','arrival_to_fifo_ns','arrival_to_tx_ns')},
                selected_missing_wire_arrival=sum('network_arrival_ns' not in p for p in selected),
                selected_business_missing_wire_arrival=sum(p['business_bytes']>0 and 'network_arrival_ns' not in p for p in selected),
                selected_terminal_gate_packets=[{k:p[k] for k in ('packet_uid','transfer_id','operation_id','tx_start_ns','downstream_wire_arrival_ns','actual_transfer_data_ready_ns','necessary_gate_references')}
                    for p in selected if p.get('is_final_wire_arrival_for_transfer') and p['necessary_gate_references']])
        else:
            sides=[]
            for key in map(tuple,s['outlets']):
                ps=packets[key];byop=defaultdict(list)
                for p in ps:
                    if p.get('operation_id'):byop[p['operation_id']].append(p)
                sides.append(dict(outlet=key,all_packet_transmit=progress(all_starts[key],'tx_start_ns','packet_bytes'),
                    whole_business_arrival=progress(ps,'network_arrival_ns'),whole_business_transmit=progress(ps,'tx_start_ns'),
                    per_operation={op:dict(arrival=progress(v,'network_arrival_ns'),transmit=progress(v,'tx_start_ns'),
                        wire_work_bytes=sum(p['packet_bytes'] for p in v),categories=dict(sum((Counter(p['categories']) for p in v),Counter()))) for op,v in sorted(byop.items())},
                    business_wait_ns=distribution([p['arrival_to_tx_ns'] for p in ps if p['business_bytes'] and 'arrival_to_tx_ns' in p]),
                    terminal_necessary_transfers=sorted({p['transfer_id'] for p in ps if p.get('is_final_wire_arrival_for_transfer') and p['necessary_gate_references']})))
            result['sides']=sides
        outcomes.append(result)
    controls=[]
    for control in selection['case']['service']:
        key=control['node_id'],control['port_id'];a,b=control['start_ns'],control['end_ns']
        ps=packets[key];starts=[p for p in ps if a<=p['tx_start_ns']<b]
        waiting=[p for p in ps if p.get('network_arrival_ns',p['fifo_enqueue_ns'])<b and p['tx_start_ns']>a]
        destinations=Counter(actual[p['transfer_id']]['dst_ub_node'] for p in starts if 'transfer_id' in p)
        controls.append(dict(requested=control,actual_started_packets=len(starts),
            actual_rates_bps=dict(Counter(p['rate_bps'] for p in starts)),
            actual_started_bytes=sum(p['packet_bytes'] for p in starts),
            busy_union_within_interval_ns=overlap(busy[key],a,b),
            actual_destination_packets=dict(destinations),
            wait_metrics={field:distribution([p[field] for p in waiting if field in p]) for field in
                ('fifo_wait_ns','arrival_to_fifo_ns','arrival_to_tx_ns','busy_during_wait_ns')},
            terminal_gate_packets=[{k:p[k] for k in ('packet_uid','transfer_id','operation_id','tx_start_ns',
                'downstream_wire_arrival_ns','actual_transfer_data_ready_ns','necessary_gate_references')}
                for p in ps if p.get('is_final_wire_arrival_for_transfer') and p['necessary_gate_references']],
            shared_hop_necessary_transfer_count=len({p['transfer_id'] for p in ps if p.get('necessary_gate_references')})))
    output.mkdir(parents=True)
    with gzip.open(output/'packet-evidence.jsonl.gz','wt') as stream:
        for key in sorted(packets):
            for p in packets[key]:stream.write(json.dumps(p,separators=(',',':'))+'\n')
    save(output/'summary.json',dict(schema_version='issue-diagnostic-deep-review/1',source_manifest_sha256=seal['source_manifest_sha256'],
        prepared_sha256=digest(prepared/'summary.json'),business_sha256=digest(business/'summary.json'),
        fixed_cases_sha256=digest(CONFIG/'scenarios/mechanism-cases.json'),cases=outcomes,controls=controls,
        packet_evidence_sha256=digest(output/'packet-evidence.jsonl.gz'),program_sha256=digest(__file__),
        limits='Packet waits are not additive business loss. UID matching is within one run only. Shared-hop packet membership in a necessary transfer does not prove that hop sets completion; protocol-only packets may lack application attribution.'))
    print('Reviewed',len(outcomes),'fixed cases with actual waits, stage work and complete gates')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('case','prepared','business','output'):p.add_argument('--'+n,required=True,type=Path)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:review(a.case,a.prepared,a.business,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],str(a.output)+'-resources',**host())
        if r['status']!='completed':raise SystemExit(r['status'])

if __name__=='__main__':main()
