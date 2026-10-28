"""Independently check source choices, endpoint counters and exported curve sums."""
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import argparse


def read(p):return json.loads(p.read_bytes())
def table(p):return list(csv.DictReader(p.open()))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def verify(stage):
    output={};digests={}
    for case in ('normal','data-and-ack-loss'):
        root=stage/case;r=read(root/'result.json');assert r['passed'] and r['packet_capture']['complete']
        source=read(root/'routes/manifest.json')
        originals={f['file_id']:read(root/f['file']['path']) for f in source['files']}
        for f in source['files']:assert sha(root/f['file']['path'])==f['file']['sha256']
        graphs=list((root/'graphs').glob('*/wave.json'));message_map={};token_rows=0
        for path in graphs:
            wave=read(path);assert wave['route_source']=='published_model_trace'
            for layer in wave['routes']:
                for token in layer['tokens']:
                    provenance=token['source'];raw=originals[provenance['file_id']]
                    assert token['experts']==raw[provenance['source_entry']][str(provenance['source_layer_id'])][0]
                    token_rows+=1
            message_map.update({m['id']:m for m in wave['messages']})
            if case!='normal':assert path.read_bytes()==(stage/'normal/graphs'/path.parent.name/'wave.json').read_bytes()
        tx,rx,packets=Counter(),Counter(),Counter()
        idx={k:i for i,k in enumerate(r['packet_capture']['columns'])}
        for e in r['packet_capture']['events']:
            mid=e[idx['message_id']]
            if mid not in message_map or e[idx['ip_protocol']]!=17:continue
            m=message_map[mid];node=e[idx['node']];event=e[idx['event']];size=e[idx['data_payload_bytes']]
            if node==m['src'] and event==0:tx[mid]+=size;packets[mid]+=1
            if node==m['dst'] and event==1:rx[mid]+=size
        for phase in ('dispatch','combine'):
            audit=stage/(case+'-'+phase+'-audit');summary=read(audit/'summary.json')
            rows=table(audit/'messages.csv');ledger=table(audit/'packet_events.csv');curve=table(audit/'arrival_curve.csv')
            assert summary['accepted_bytes']==sum(m['receiver_contiguous_bytes'] for m in r['messages'])
            assert summary['source_tx_payload_bytes']==sum(tx.values())
            assert summary['destination_rx_payload_bytes']==sum(rx.values())
            for row in rows:
                mid=int(row['message_id']);assert (int(row['tx_payload_bytes']),int(row['rx_payload_bytes']),int(row['tx_packets']))==(tx[mid],rx[mid],packets[mid])
            increments=sum(int(p['accepted_delta_bytes']) for p in ledger)
            assert increments==int(curve[-1]['accepted_bytes'])==summary['slice']['logical_bytes']
            assert all(int(a['accepted_bytes'])<=int(b['accepted_bytes']) for a,b in zip(curve,curve[1:]))
            assert all(int(c['accepted_bytes'])<=int(c['unique_arrival_bytes'])<=summary['slice']['logical_bytes'] for c in curve)
        if case!='normal':assert r['fault_injection']['drop_counts']==[2,1]
        output[case]=dict(passed=True,source_token_layer_rows=token_rows,messages=len(r['messages']),
            capture_events=r['packet_capture']['observations'],finish_ns=r['finish_ns'],
            source_payload_bytes=sum(tx.values()),destination_payload_bytes=sum(rx.values()))
        for p in (root/'result.json',root/'frontend.json',root/'rdma.json',root/'input.json'):
            digests[str(p.relative_to(stage))]=sha(p)
    return dict(passed=True,cases=output,raw_sha256=digests,network_replays=0,
        note='native final states, raw TX/RX, original choices and curve ledger sums checked independently; no GPU calibration')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();assert not a.output.exists()
    a.output.write_text(json.dumps(verify(a.stage.resolve()),ensure_ascii=False,indent=2)+'\n')
