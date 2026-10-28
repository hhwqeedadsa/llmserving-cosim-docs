"""Independent raw-route, packet, endpoint and cumulative-curve experiment audit."""
import argparse
from collections import Counter,defaultdict
import csv
import hashlib
import json
from pathlib import Path

CASES=('clos-normal','clos-loss','hpn-normal','hpn-loss')
def read(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def csvrows(p):
    # Endpoint CSV omits untouched Counter fields; those absent counters are zero.
    with p.open() as f:return [{k:int(v or 0) for k,v in r.items()} for r in csv.DictReader(f)]


def verify(root,output):
    assert not output.exists();config=read(root/'inputs/service.json');expected={};records=config['routes']['requests'];raw={}
    for i,r in enumerate(records):
        p=root/'inputs'/r['file']['path'];assert sha(p)==r['file']['sha256'];raw[i]=read(p)
    source_rows=0
    for gi,group in enumerate(config['service']['parallel']['groups']):
        for step in range(2):
            graph=f'g{gi}.w{step}';matrix=Counter()
            for i,r in enumerate(records):
                if r['rank_id'] not in group:continue
                for layer in (3,4):
                    for expert in raw[i][1+step][str(layer)][0]:
                        dst=group[expert//(256//len(group))]
                        if r['rank_id']!=dst:matrix[layer-3,r['rank_id'],dst]+=14336
                    source_rows+=1
            expected[graph]={(layer,'dispatch',src,dst):n for (layer,src,dst),n in matrix.items()}
            expected[graph].update({(layer,'combine',dst,src):n for (layer,src,dst),n in matrix.items()})
    cases={};checked_packet_events=0;checked_curve_rows=0
    for case in CASES:
        result=read(root/case/'result.json');assert result['passed'] and result['gpu_count']==320
        meta={};logical=defaultdict(Counter)
        for graph,reference in expected.items():
            wave=read(root/case/'graphs'/graph/'wave.json')
            assert {(m['layer'],m['phase'],m['src'],m['dst']):m['bytes'] for m in wave['messages']}==reference
            assert len(wave['messages'])==len(reference)
            for m in wave['messages']:
                assert m['id'] not in meta;meta[m['id']]=dict(m,graph=graph)
                logical[m['src']]['logical_send_bytes']+=m['bytes'];logical[m['dst']]['logical_receive_bytes']+=m['bytes']
        native={m['id']:m for m in result['messages']};assert native.keys()==meta.keys()
        for i,m in meta.items():assert all(native[i][k]==m[k] for k in ('src','dst','bytes','plane'))
        capture=result['packet_capture'];assert capture['complete'] and capture['omitted_events']==0
        endpoints=defaultdict(Counter);seen_rx=set();prefix=defaultdict(int);confirmed=defaultdict(int)
        totals={phase:dict(tx_payload_bytes=0,rx_payload_bytes=0,unique_arrival_bytes=0,accepted_bytes=0,confirmed_bytes=0) for phase in ('dispatch','combine')}
        curves={phase:[dict(time_ns=0,**totals[phase])] for phase in totals};packet_counts=defaultdict(Counter)
        previous_time=0
        for values in capture['events']:
            p=dict(zip(capture['columns'],values));t=p['time_ns'];assert previous_time<=t<=result['finish_ns'];previous_time=t
            node,event,proto=p['node'],p['event'],p['ip_protocol'];i=p['message_id']
            if node<320 and event in (0,1):
                direction='tx' if event==0 else 'rx';kind='data' if proto==17 else 'control'
                endpoints[node][direction+'_'+kind+'_model_bytes']+=p['model_bytes']
                endpoints[node][direction+'_'+kind+'_packets']+=1
            if i not in meta:continue
            m=meta[i];is_slice=m['graph']=='g0.w0' and m['layer']==0;phase=m['phase'];old=dict(totals[phase])
            seq,size=p['sequence'],p['data_payload_bytes']
            if proto==17:
                assert seq%9000==0 and size==min(9000,m['bytes']-seq) and size>0
                if node==m['src'] and event==0:
                    packet_counts[i]['tx_packets']+=1;packet_counts[i]['tx_bytes']+=size
                    if is_slice:totals[phase]['tx_payload_bytes']+=size
                if node==m['dst'] and event==1:
                    packet_counts[i]['rx_packets']+=1;packet_counts[i]['rx_bytes']+=size
                    if is_slice:
                        totals[phase]['rx_payload_bytes']+=size
                        if (i,seq) not in seen_rx:totals[phase]['unique_arrival_bytes']+=size
                    seen_rx.add((i,seq))
                    if seq==prefix[i]:
                        prefix[i]+=size
                        if is_slice:totals[phase]['accepted_bytes']+=size
            elif proto==252 and node==m['src'] and event==1:
                assert 0<=seq<=m['bytes']
                delta=max(confirmed[i],seq)-confirmed[i];confirmed[i]+=delta
                if is_slice:totals[phase]['confirmed_bytes']+=delta
            if is_slice and old!=totals[phase]:curves[phase].append(dict(time_ns=t,**totals[phase]))
        checked_packet_events+=len(capture['events'])
        for i,m in meta.items():
            assert prefix[i]==confirmed[i]==m['bytes']
            assert packet_counts[i]['tx_packets']==native[i]['source_data_packets']
            assert packet_counts[i]['tx_bytes']==native[i]['source_data_payload_bytes']
            logical[m['src']]['source_tx_payload_bytes']+=packet_counts[i]['tx_bytes']
            logical[m['dst']]['destination_rx_payload_bytes']+=packet_counts[i]['rx_bytes']
            logical[m['dst']]['accepted_bytes']+=prefix[i]
        for phase in curves:
            audit=root/(case+'-'+phase+'-audit');curves[phase].append(dict(time_ns=result['finish_ns'],**totals[phase]))
            actual=csvrows(audit/'arrival_curve.csv');assert actual==curves[phase],(case,phase,'curve differs')
            checked_curve_rows+=len(actual)
            endpoints_csv=csvrows(audit/'endpoints.csv');assert len(endpoints_csv)==320
            for row in endpoints_csv:
                rank=row['rank'];expected_fields=endpoints[rank]+logical[rank]
                assert all(row[k]==expected_fields[k] for k in row if k!='rank'),(case,rank)
            # The plotted slice packet ledger must exactly match physical capture rows.
            with (audit/'packet_events.csv').open() as f:
                for row in csv.DictReader(f):
                    original=dict(zip(capture['columns'],capture['events'][int(row['capture_index'])]))
                    assert all(int(row[k])==v for k,v in original.items())
        active=len({rank for m in meta.values() for rank in (m['src'],m['dst'])});assert active==221
        cases[case]=dict(messages=len(meta),logical_bytes=sum(m['bytes'] for m in meta.values()),
                         active_data_ranks=active,packet_events=len(capture['events']),finish_ns=result['finish_ns'])
    for family in ('clos','hpn'):
        for graph in expected:assert sha(root/(family+'-normal')/'graphs'/graph/'wave.json')==sha(root/(family+'-loss')/'graphs'/graph/'wave.json')
    verdict=dict(passed=True,source_requests=len(records),source_token_layer_rows=source_rows,
        physical_gpus=320,cases=cases,independently_checked_packet_events=checked_packet_events,
        independently_checked_curve_rows=checked_curve_rows,endpoint_rows=4*2*320,
        method='independent raw choices to matrices; fixed-packet sequence replay; complete CSV comparison',
        network_reruns=0,hardware_calibrated=False)
    output.write_text(json.dumps(verdict,ensure_ascii=False,indent=2)+'\n');print(json.dumps(verdict,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();verify(a.root.resolve(),a.output.resolve())
