"""Verify selected physical packet waiting and complete business gates.

Researcher-only input: raw packet identities, actual paths and sealed graph
facts. No output here is presented as an observed-only diagnosis.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import json
from pathlib import Path
import sys

from moe_ub_cosim.record_storage import iter_port_records
from .analyze import distribution
from .path_analysis import queue_duration
from .workflow import digest, host, read, save


def overlap(intervals, start, end):
    """Union of busy time, clipped to waiting; never sum overlapping service."""
    if end<start:raise ValueError('Invalid wait interval')
    until=start;total=0
    for a,b in sorted(intervals):
        a=max(a,start,until);b=min(b,end)
        if a<b:total+=b-a;until=b
    return total


def review(case, facts, output):
    case,facts,output=Path(case),Path(facts),Path(output)
    if output.exists():raise ValueError('Packet review output must be new')
    selection=read(case/'selection.json');ex=case/'run/raw/execution';original=read(facts/'summary.json')
    if selection['intervention'] is None:
        eq=read(case/'equivalence.json')
        if not eq['passed'] or eq['replay_manifest_sha256']!=digest(case/'run/manifest.json'):
            raise ValueError('Capture equivalence is not bound to this run')
    if digest(facts/'selected-facts.json.gz')!=original['facts_sha256']:
        raise ValueError('Business facts differ from their seal')
    if original.get('manifest_sha256')!=digest(case/'run/manifest.json'):
        raise ValueError('Business facts are not from this actual execution')
    with gzip.open(facts/'selected-facts.json.gz','rt') as stream:detail=json.load(stream)
    planned={int(k):v for k,v in detail['planned_transfers'].items()}
    actual={int(k):v for k,v in detail['actual_transfers'].items()}
    network=read(case/'run/inputs/network/backend.json');links={}
    for l in network['topology']['links']:
        for s,t in (('src','dst'),('dst','src')):links[l[s],l[s+'_port']]=dict(target=l[t],delay=l['delay_ns'],rate=l['rate_bps'])
    starts={};events={};classified={};states={}
    selected={(c['switch_id'],c['port_id']) for c in selection['discovery']['candidates']}
    for n,p in selection['ports']:
        folder=ex/'ports'/f'{n}-{p}';tx={};audit=defaultdict(dict);identity=defaultdict(list)
        for r in iter_port_records(ex,folder,'egress-queue-audit.jsonl'):
            uid=r['packet_uid'];event=r['event']
            if event in audit[uid]:raise ValueError('Repeated packet event on one directed port')
            audit[uid][event]=r
            if event=='tx_start':tx[uid]=r
        starts[n,p]=tx
        if (n,p) in selected:
            events[n,p]=audit
            for r in iter_port_records(ex,folder,'truth/classified_packet_ranges.jsonl'):identity[r['packet_uid']].append(r)
            classified[n,p]=identity
            states[n,p]=queue_duration(iter_port_records(ex,folder,'queue-state-events.jsonl'),
                selection['detail_start_ns'],selection['detail_end_ns'])
    output.mkdir(parents=True);summaries=[]
    with gzip.open(output/'packets.jsonl.gz','wt') as stream:
        for c in selection['discovery']['candidates']:
            key=c['switch_id'],c['port_id'];tx=starts[key];audit=events[key]
            incoming={}
            for upstream,packets in starts.items():
                if links[upstream]['target']!=key[0]:continue
                for uid,r in packets.items():
                    if uid in incoming:raise ValueError('Ambiguous incoming actual path')
                    incoming[uid]=dict(node_id=upstream[0],port_id=upstream[1],
                        start_ns=r['time_ns'],busy_until_ns=r['busy_until_ns'],
                        arrival_ns=r['busy_until_ns']+links[upstream]['delay'])
            busy=[(r['time_ns'],r['busy_until_ns']) for r in tx.values()]
            rows=[];counts=Counter();by_operation=defaultdict(list);gate_transfers=set()
            receiver=next(t['receiver_rank'] for t in original['targets']
                          if t['candidate']['candidate_id']==c['candidate_id'])
            for op in original['operations']:
                if op['rank_id']!=receiver or op['kind']!='dispatch' or op['layer_id']!=7:continue
                for node in op['completion']['latest_completion_dag']:
                    tid=node.get('transfer_id')
                    if tid is not None:gate_transfers.add(tid)
            for uid,r in sorted(tx.items(),key=lambda x:(x[1]['time_ns'],x[0])):
                row=dict(candidate_id=c['candidate_id'],switch_id=key[0],port_id=key[1],packet_uid=uid,
                    tx_start_ns=r['time_ns'],busy_until_ns=r['busy_until_ns'],packet_bytes=r['packet_bytes'],
                    destination_arrival_ns=r['busy_until_ns']+links[key]['delay'])
                e=audit[uid];identities=classified[key].get(uid,[])
                tids={i['transfer_id'] for i in identities}
                if len(tids)>1:raise ValueError('One packet has several native task identities')
                row['classified_ranges']=identities
                if 'enqueue' in e and 'dequeue' in e:
                    a,b=e['enqueue']['time_ns'],e['dequeue']['time_ns']
                    if not a<=b<=r['time_ns']:raise ValueError('Invalid FIFO ordering')
                    row.update(fifo_enqueue_ns=a,fifo_dequeue_ns=b,fifo_wait_ns=b-a,
                        fifo_busy_overlap_ns=overlap(busy,a,b))
                    row['fifo_wait_without_busy_ns']=b-a-row['fifo_busy_overlap_ns']
                    counts['fifo_byte_ns_exited']+=(b-a)*e['enqueue']['packet_bytes']
                    if uid in incoming:
                        arrival=incoming[uid]['arrival_ns']
                        if arrival>a:raise ValueError('Downstream FIFO predates actual link arrival')
                        row.update(upstream=incoming[uid],arrival_to_fifo_ns=a-arrival,
                            arrival_to_fifo_busy_ns=overlap(busy,arrival,a))
                        row['arrival_to_fifo_without_busy_ns']=a-arrival-row['arrival_to_fifo_busy_ns']
                        counts['arrival_to_fifo_byte_ns_exited']+=(a-arrival)*e['enqueue']['packet_bytes']
                    else:counts['fifo_packets_without_upstream_capture']+=1
                else:counts['packets_without_fifo_events']+=1
                if tids:
                    tid=next(iter(tids));t=planned.get(tid)
                    if t is None:raise ValueError('Captured target task absent from explicit business facts')
                    row.update(transfer_id=tid,operation_id=t['operation_id'],src_rank=t['src_rank'],dst_rank=t['dst_rank'],
                        message_role=t['message_role'],on_original_latest_gate=tid in gate_transfers)
                    a=actual[tid]
                    row.update(transfer_data_ready_ns=a['data_ready_ns'],transfer_send_start_ns=a['send_start_ns'],
                        is_packet_at_complete_transfer_delivery=row['destination_arrival_ns']==a['data_ready_ns'])
                    if row['destination_arrival_ns']>a['data_ready_ns'] and selection['intervention'] is None:
                        raise ValueError('Captured destination arrival exceeds original callback')
                    by_operation[t['operation_id']].append(row)
                counts['packets']+=1;rows.append(row)
                stream.write(json.dumps(row,separators=(',',':'))+'\n')
            fields=('fifo_wait_ns','fifo_wait_without_busy_ns','arrival_to_fifo_ns','arrival_to_fifo_without_busy_ns')
            counts['fifo_byte_ns_not_in_completed_packet_rows']=states[key]['fifo']['byte_ns']-counts['fifo_byte_ns_exited']
            if counts['fifo_byte_ns_not_in_completed_packet_rows']<0:
                raise ValueError('Packet FIFO waits exceed independently integrated occupancy')
            summaries.append(dict(candidate=c,queue_state=states[key],counts=dict(counts),
                operations={op:dict(packets=len(rs),sources=sorted({r['src_rank'] for r in rs}),
                    distributions={f:distribution([r[f] for r in rs if f in r]) for f in fields},
                    complete_transfer_delivery_packets=sum(r['is_packet_at_complete_transfer_delivery'] for r in rs),
                    original_latest_gate_packets=[r for r in rs if r['on_original_latest_gate']],
                    longest_wait_packets=sorted(rs,key=lambda r:r.get('arrival_to_fifo_ns',0)+r.get('fifo_wait_ns',0),reverse=True)[:5])
                    for op,rs in sorted(by_operation.items())}))
    save(output/'summary.json',dict(schema_version='issue-network-packet-review/1',candidates=summaries,
        selection_sha256=digest(case/'selection.json'),manifest_sha256=digest(case/'run/manifest.json'),
        facts_sha256=digest(facts/'summary.json'),program_sha256=digest(__file__),
        packets_sha256=digest(output/'packets.jsonl.gz'),
        interpretation=['Arrival-to-FIFO includes actual VOQ residence and fixed allocation; subtraction is not a measurement of pure arbitration or credit wait.',
            'Busy overlap proves occupied local service during the interval, not the absence of simultaneous credit gating.',
            'Only observed actual upstream hops are used; direct device ingress and protocol packets without application ranges remain explicit.',
            'A packet reaching complete transfer delivery is not necessarily the last necessary operation condition.']))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('case','facts','output'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:review(a.case,a.facts,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],
            str(a.output.resolve())+'-resources',**host(extra_roots=[a.output]))
        if r['status']!='completed':raise SystemExit('Network packet review failed: '+r['status'])


if __name__=='__main__':main()
