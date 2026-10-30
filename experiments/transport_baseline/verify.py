"""Independently verify exported completion, packet counts and causal gates."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path


def load(p):
    return json.loads(p.read_bytes())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__();self.paths=[]
    def handle_starttag(self, tag, attrs):
        self.paths.extend(v for k,v in attrs if (tag=='a' and k=='href') or (tag=='img' and k=='src'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('study','delivery','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();e=load(a.delivery/'analysis.json');f=load(a.study/'frozen.json')
    assert e['frozen_sha256']==sha(a.study/'frozen.json')
    assert len(e['cases'])==4 and e['new_networks']==4 and e['builds']==0
    for key,h in e['source_sha256'].items():
        assert sha(Path(__file__).resolve().parents[2]/key)==h
    normal=load(a.study/'base-normal/result.json')
    longest=max(m['ack_ns']-m['ready_ns'] for m in normal['service_transport']['messages'])
    assert (longest//1000+1)*1000==e['calibration']['initial_rto_ns']
    assert longest==e['calibration']['normal_max_ready_ack_ns']
    total_calls=0
    for name,row in e['cases'].items():
        path=a.study/name;r=load(path/'result.json')
        assert sha(path/'result.json')==row['result_sha256']
        assert load(path/'verification.json')['passed']
        front=load(path/'frontend.json')
        for i in range(4):
            assert len([h for h in front['history'] if h['group_index']==i])==2
            assert max(h['finished_ns'] for h in front['history'] if h['group_index']==i)==row['group_complete_ns'][str(i)]
        assert r['finish_ns']==row['drain_ns'] and r['drained']
        native=r['service_transport']['native'];cols=native['source_packet_columns']
        tx=sum(x[cols.index('payload_bytes')] for x in native['source_packets'])
        planned=sum(m['bytes'] for m in r['service_transport']['messages'])
        assert (tx,planned,tx-planned)==(row['source_tx_payload_bytes'],row['logical_bytes'],row['retransmitted_payload_bytes'])
        assert not r['service']['inflight_helpers']['attempts']
        assert native['physical_drained'] and native['inflight_packets']==0
        assert r['service_transport']['message_buffers']['used_bytes']==native['message_payload']['retained_bytes']==0
        cap=r['packet_capture'];col=cap['columns']
        dropped=[dict(zip(col,x)) for x in cap['events'] if x[col.index('event')]==cap['event_codes']['drop']]
        assert dropped==row['packet_drops'] and len(dropped)==r['fault_drops']==row['fault_drops']
        assert all(d['node']==32 and d['ip_protocol']==17 and 0<=d['time_ns']<20000 for d in dropped)
        if name.endswith('normal'):
            assert not dropped
        calls=load(a.study/('diagnosis-'+name)/'calls.json');total_calls+=len(calls)
        assert len(calls)==16
        # A slower dispatch must be the actual last predecessor, not assumed GPU backlog.
        for call in calls:
            assert call['recorded_frontier_advance_ns']==0
            for b in call['bundles']:
                assert b['expert_start_ns']>=b['dispatch_gate_ns']
        assert row['gates_ns']=={g['graph_id']+'.'+g['id']:g['released_ns'] for g in r['service']['phase_completion']['gates']}
    effect=e['effects']
    for condition in ('normal','local'):
        b=e['cases']['base-'+condition];s=e['cases']['short-'+condition]
        assert effect[condition]['target_saved_ns']==b['group_complete_ns']['0']-s['group_complete_ns']['0']
        assert effect[condition]['first_dispatch_saved_ns']==b['gates_ns']['g0.w0.L0.dispatch']-s['gates_ns']['g0.w0.L0.dispatch']
        assert effect[condition]['other_group_delay_ns']=={str(i):s['group_complete_ns'][str(i)]-b['group_complete_ns'][str(i)] for i in (1,2,3)}
    parser=Links();parser.feed((a.delivery/'report.html').read_text())
    assert parser.paths and all((a.delivery/x).is_file() for x in parser.paths)
    probes=[load(p) for p in (a.study.parent/'monitors').rglob('probe.json')]
    assert probes and all(p['status']=='completed' and p['child_exit_code']==0 for p in probes)
    proof=dict(passed=True,cases=4,calls=total_calls,graph_equality_checked=e['same_original_graphs'],
        source_hashes_verified=True,normal_calibration_independently_checked=True,
        all_buffers_drained=True,html_resources=len(parser.paths),browser_rendering_verified=False,
        monitored_seconds=sum(p['wall_seconds'] for p in probes),
        files_sha256={p.name:sha(p) for p in a.delivery.iterdir() if p.is_file()})
    with a.output.open('x') as s:json.dump(proof,s,indent=2);s.write('\n')
    print(json.dumps(dict(passed=True,cases=4,calls=total_calls)))


if __name__=='__main__':
    main()
