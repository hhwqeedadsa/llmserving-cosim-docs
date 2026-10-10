"""Researcher verification of selected STORE packet paths and queue durations."""
import argparse
from collections import defaultdict
import json
import hashlib
from pathlib import Path
import sys

from moe_ub_cosim.record_storage import iter_port_records
from .analyze import distribution
from .workflow import digest, host, read, save


def require(ok, message):
    if not ok:raise ValueError(message)


def queue_duration(rows, start, end):
    rows=iter(rows);previous=next(rows,None)
    require(previous is not None and previous['event_kind']=='initial' and previous['time_ns']==start,'Missing initial queue state')
    result={q:dict(nonempty_ns=0,max_contiguous_ns=0,byte_ns=0,intervals=0) for q in ('voq','fifo')}
    active={};last_end={};final=False
    for row in rows:
        a,b=previous['time_ns'],row['time_ns']
        require(not final and a<=b<=end,'Unordered queue states or data after final')
        for q in result:
            value=previous[q+'_waiting_bytes'];require(value>=0,'Negative queue state')
            if value and b>a:
                if last_end.get(q)!=a:active[q]=a;result[q]['intervals']+=1
                last_end[q]=b
                result[q]['nonempty_ns']+=b-a;result[q]['byte_ns']+=value*(b-a)
                result[q]['max_contiguous_ns']=max(result[q]['max_contiguous_ns'],b-active[q])
        final=row['event_kind']=='final';previous=row
    require(final and previous['time_ns']==end,'Missing final queue state')
    return result


def merged_ranges(regions):
    merged=[]
    for a,b in sorted(regions):
        require(a<b,'Invalid application range')
        if merged and a<=merged[-1][1]:merged[-1][1]=max(b,merged[-1][1])
        else:merged.append([a,b])
    return merged


def reconstruct(packets, transfer, links, kinds):
    plan,actual=transfer['plan'],transfer['actual'];covered=[];details=[]
    for uid,points in sorted(packets.items()):
        points=sorted(points,key=lambda p:p['start_ns'])
        require(points and links.get((actual['src_ub_node'],points[0]['node_id'])) is not None,'First hop is not source access switch')
        regions=points[0]['ranges']
        require(all(p['ranges']==regions for p in points),'Application identity changed between hops')
        require(len({p['node_id'] for p in points})==len(points),'Packet revisits a switch')
        residences=[]
        for a,b in zip(points,points[1:]):
            link=links[a['node_id'],a['port_id'],'egress']
            require(link['target']==b['node_id'] and b['start_ns']>=a['busy_until_ns']+link['delay_ns'],
                    'Disconnected path or propagation precedes serialization')
            residences.append(b['start_ns']-a['busy_until_ns']-link['delay_ns'])
        last=points[-1];link=links[last['node_id'],last['port_id'],'egress']
        require(link['target']==actual['dst_ub_node'],'Final hop does not reach consumer')
        require(points[0]['start_ns']>=actual['send_start_ns'],'Packet predates task submission')
        covered.extend(regions)
        details.append(dict(packet_uid=uid,departures=points,arrival_ns=last['busy_until_ns']+link['delay_ns'],
            intermediate_arrival_to_departure_ns=residences))
    require(details,'Selected transfer has no captured packets')
    expected=merged_ranges((r['begin'],r['end']) for r in plan['payload_regions'] if r['kind']!='padding')
    require(merged_ranges(covered)==expected,'Captured application ranges do not cover the selected task')
    require(sum(b-a for a,b in covered)==sum(b-a for a,b in expected),'Duplicated packet application ranges')
    require(len(details)==actual['committed_segments'],'Not every committed data packet was captured')
    require(max(p['arrival_ns'] for p in details)==actual['data_ready_ns'],'Final physical delivery does not match data-ready callback')
    return details


def analyze(detail, output):
    detail,output=Path(detail).resolve(),Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    selection=read(detail/'selection.json');equivalence=read(detail/'equivalence.json')
    require(equivalence['passed'] and equivalence['selection_sha256']==digest(detail/'selection.json'),'Unverified or changed selection')
    require(equivalence['replay_manifest_sha256']==digest(detail/'run/manifest.json'),'Replay binding changed')
    selected={int(k):v for k,v in selection['transfers'].items()}
    ex=detail/'run/raw/execution';net=read(detail/'run/inputs/network/backend.json')
    require(not net.get('port_service',{'intervals':[]})['intervals'] and net['attributes']['ns3::UbPort::UbInterframeGap']=='+0ns',
            'Arrival reconstruction requires normal service and zero interframe gap')
    kinds={n['node_id']:n['kind'] for n in net['topology']['nodes']};links={}
    for link in net['topology']['links']:
        for a,b in (('src','dst'),('dst','src')):
            links[link[a],link[a+'_port'],'egress']=dict(target=link[b],delay_ns=link['delay_ns'])
            links[link[a],link[b]]=link
    paths=defaultdict(lambda:defaultdict(list));queues=[]
    for node,port in selection['ports']:
        folder=ex/'ports'/f'{node}-{port}';attributions=defaultdict(list)
        for row in iter_port_records(ex,folder,'truth/classified_packet_ranges.jsonl'):
            if row['transfer_id'] in selected:attributions[row['packet_uid'],row['sim_time_ns']].append(row)
        wanted={key[0] for key in attributions};enqueue={};starts={}
        for row in iter_port_records(ex,folder,'egress-queue-audit.jsonl'):
            uid=row['packet_uid']
            if uid not in wanted:continue
            if row['event']=='enqueue':
                require(uid not in enqueue,'Repeated FIFO enqueue');enqueue[uid]=row['time_ns']
            elif row['event']=='tx_start':
                key=uid,row['time_ns'];require(key not in starts,'Repeated physical departure');starts[key]=row
        for key,regions in attributions.items():
            require(key in starts and key[0] in enqueue,'Missing selected packet FIFO/physical start')
            physical=starts[key];tids={r['transfer_id'] for r in regions};require(len(tids)==1,'Packet belongs to multiple transfers')
            require(all(r['counted_packet_bytes']==physical['packet_bytes'] for r in regions),'Packet byte totals mismatch')
            point=dict(node_id=node,port_id=port,start_ns=key[1],busy_until_ns=physical['busy_until_ns'],
                packet_bytes=physical['packet_bytes'],fifo_enqueue_ns=enqueue[key[0]],
                fifo_enqueue_to_start_ns=key[1]-enqueue[key[0]],ranges=merged_ranges(r['task_byte_range'] for r in regions))
            require(point['fifo_enqueue_to_start_ns']>=0,'FIFO residence is negative')
            paths[next(iter(tids))][key[0]].append(point)
        durations=queue_duration(iter_port_records(ex,folder,'queue-state-events.jsonl'),
            selection['detail_start_ns'],selection['detail_end_ns'])
        areas={q:0 for q in ('voq','fifo')}
        for r in iter_port_records(ex,folder,'queue-windows.jsonl'):
            for q in areas:areas[q]+=int(r[q+'_waiting_byte_ns'])
        require(all(areas[q]==durations[q]['byte_ns'] for q in areas),'Detailed queue integral differs from complete aggregate')
        queues.append(dict(switch_id=node,port_id=port,**durations))
    require(set(paths)==set(selected),'Some selected transfers were not captured')
    summaries=[]
    with (output/'packets.jsonl').open('w') as stream:
        for tid,t in sorted(selected.items()):
            packets=reconstruct(paths[tid],t,links,kinds)
            for packet in packets:stream.write(json.dumps(dict(transfer_id=tid,**packet))+'\n')
            plan,actual=t['plan'],t['actual'];signatures=[]
            for packet in packets:
                signatures.append(dict(ranges=packet['departures'][0]['ranges'],
                    ports=[[p['node_id'],p['port_id']] for p in packet['departures']]))
            summaries.append(dict(transfer_id=tid,operation_id=plan['operation_id'],src_rank=plan['src_rank'],
                dst_rank=plan['dst_rank'],message_role=plan['message_role'],pair_byte_range=plan['pair_byte_range'],
                message_id=plan['message_id'],chunk_index=plan['chunk_index'],
                sources_sha256=hashlib.sha256(json.dumps(plan['sources'],sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                task_byte_range=plan['task_byte_range'],payload_bytes=plan['payload_bytes'],packets=len(packets),
                send_start_ns=actual['send_start_ns'],data_ready_ns=actual['data_ready_ns'],
                latency_ns=actual['data_ready_ns']-actual['send_start_ns'],
                before_first_switch_start_ns=min(p['departures'][0]['start_ns'] for p in packets)-actual['send_start_ns'],
                captured_completion_span_ns=actual['data_ready_ns']-min(p['departures'][0]['start_ns'] for p in packets),
                intermediate_arrival_to_departure_ns=distribution([d for p in packets for d in p['intermediate_arrival_to_departure_ns']]),
                fifo_residence_ns=distribution([p['fifo_enqueue_to_start_ns'] for packet in packets for p in packet['departures']]),
                path_signatures=sorted(signatures,key=lambda x:x['ranges'])))
    save(output/'transfers.json',summaries);save(output/'queues.json',queues)
    result=dict(schema_version='issue-screening-selected-paths/1',passed=True,scope='researcher_internal_not_E3_input',
        selection_sha256=digest(detail/'selection.json'),equivalence_sha256=digest(detail/'equivalence.json'),
        pairs=selection['pairs'],critical_data=selection['critical_data'],transfers=len(selected),
        packets=sum(r['packets'] for r in summaries),ports=len(queues),
        complete_selected_application_bytes=True,connected_physical_paths=True,actual_data_ready_reconciled=True,
        exact_queue_duration_ns=True,queue_integrals_match_aggregates=True,
        queue_distributions={q:{f:distribution([r[q][f] for r in queues]) for f in ('nonempty_ns','max_contiguous_ns')} for q in ('voq','fifo')},
        fifo_residence_ns=distribution([r['fifo_residence_ns']['maximum'] for r in summaries]),
        limitation='No source-device departure or per-packet VOQ admission time; port occupancy is not selected-packet waiting or avoidable business loss.',
        files={p.name:digest(p) for p in output.iterdir() if p.is_file()},program_sha256=digest(__file__))
    save(output/'summary.json',result);print(json.dumps(dict(passed=True,transfers=len(selected),ports=len(queues))))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--detail',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:analyze(a.detail,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],str(a.output.resolve())+'-resources',**host(extra_roots=[a.detail,a.output]))
        if r['status']!='completed':raise SystemExit('Path analysis failed: '+r['status'])


if __name__=='__main__':main()
