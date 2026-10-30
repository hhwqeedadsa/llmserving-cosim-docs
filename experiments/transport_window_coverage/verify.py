"""Independent raw-record validation of the fixed recovery-window study."""
from collections import defaultdict
import gc
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import argparse


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


class Links(HTMLParser):
    def __init__(self):super().__init__();self.paths=[]
    def handle_starttag(self,tag,attrs):
        self.paths.extend(v for k,v in attrs if (tag=='a' and k=='href') or (tag=='img' and k=='src'))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();study=a.study
    f=load(study/'frozen.json');e=load(study/'delivery/analysis.json');cal=load(study/'calibration.json')
    assert e['frozen_sha256']==sha(study/'frozen.json') and e['requests']==15
    for path,h in f['source_sha256'].items():assert sha(Path(__file__).resolve().parents[2]/path)==h
    normal=load(study/'base-normal/result.json')
    spans={str(m['id']):dict(ready_ns=m['ready_ns'],ack_ns=m['ack_ns']) for m in normal['service_transport']['messages']}
    assert spans==cal['message_intervals']
    maximum=max(m['ack_ns']-m['ready_ns'] for m in spans.values())
    assert (maximum//1000+1)*1000==cal['initial_rto_ns']['calibrated']
    del normal;gc.collect()
    unique=list(dict.fromkeys(cal['case_map'].values()))
    assert len(unique)==e['new_networks']==cal['unique_networks']<=12
    baseline_graphs=baseline_config=None;calls_count=0
    for name in unique:
        directory=study/name;r=load(directory/'result.json');row=e['cases'][name]
        assert sha(directory/'result.json')==row['result_sha256'] and load(directory/'verification.json')['passed']
        assert load(study/(name+'-command.json'))['exit_code']==0
        assert load(study/(name+'-diagnosis.json'))['exit_code']==0
        assert r['passed'] and r['drained'] and r['gpu_count']==128 and r['switch_count']==20
        c=load(directory/'input.json');assert c['inflight_helpers']['policy']=='observe_only/1'
        assert c['transport']['transport_recovery']['initial_rto_ns']==row['initial_rto_ns']
        assert len(c['route_replay']['requests'])==15 and list(map(len,c['parallel']['groups']))==[32]*4
        c['transport']['transport_recovery']['initial_rto_ns']=80000
        if baseline_config is None:baseline_config=c
        assert c==baseline_config
        graphs={str(p.relative_to(directory/'graphs')):sha(p) for p in (directory/'graphs').rglob('*') if p.is_file()}
        if baseline_graphs is None:baseline_graphs=graphs
        assert graphs==baseline_graphs
        history=load(directory/'frontend.json')['history'];assert len(history)==8
        for i in range(4):assert max(h['finished_ns'] for h in history if h['group_index']==i)==row['group_complete_ns'][str(i)]
        assert r['finish_ns']==row['drain_ns']
        n=r['service_transport']['native'];assert n['physical_drained'] and n['inflight_packets']==0 and n['closed']
        assert r['service_transport']['message_buffers']['used_bytes']==n['message_payload']['retained_bytes']==0
        assert not r['service']['inflight_helpers']['attempts']
        cols=n['source_packet_columns'];high=defaultdict(int);overlap={};total=duplicate=0
        for source in n['source_packets']:
            x=dict(zip(cols,source));start=x['sequence'];end=start+x['payload_bytes'];sid=x['stream']
            assert start<=high[sid]
            repeated=min(end,high[sid])-start
            overlap[x['packet_uid']]=repeated;duplicate+=repeated;total+=end-start;high[sid]=max(high[sid],end)
        logical=sum(m['bytes'] for m in r['service_transport']['messages'])
        assert logical==sum(high.values())==row['logical_bytes']
        assert total==row['tx_payload_bytes'] and duplicate==total-logical==row['retransmitted_payload_bytes']
        cap=r['packet_capture'];assert cap['complete'] and cap['omitted_events']==0;cols=cap['columns']
        drops=[dict(zip(cols,x)) for x in cap['events'] if x[cols.index('event')]==cap['event_codes']['drop']]
        assert drops==row['drops'] and len(drops)==r['fault_drops']==row['fault_drops']
        window=name.split('-')[1];end=f['windows_ns'][window]
        assert all(d['node']==32 and d['ip_protocol']==17 and 0<=d['time_ns']<end for d in drops)
        assert sum(overlap[d['packet_uid']] for d in drops)==row['wasted_retry_payload_bytes']
        rc=n['recovery'];cols=rc['columns']
        timeouts=[dict(zip(cols,x)) for x in rc['events'] if x[cols.index('event')]==rc['event_codes']['timeout_retransmit']]
        assert timeouts==row['timeouts']
        calls=load(study/('diagnosis-'+name)/'calls.json');assert len(calls)==60;calls_count+=len(calls)
        assert all(c['recorded_frontier_advance_ns']==0 for c in calls)
        assert row['first_dispatch_ns']==next(g['released_ns'] for g in r['service']['phase_completion']['gates'] if g['graph_id']=='g0.w0' and g['id']=='L0.dispatch')
        gc.collect()
    for name,canonical in cal['case_map'].items():
        if name!=canonical:
            assert e['cases'][name]['result_sha256']==e['cases'][canonical]['result_sha256']
            assert load(study/(name+'-alias.json'))['reused_from']==canonical
    for name,effect in e['effects'].items():
        policy,window=name.split('-');base=e['cases']['base-'+window];row=e['cases'][name]
        assert effect['target_saved_ns']==base['group_complete_ns']['0']-row['group_complete_ns']['0']
        assert effect['extra_payload_bytes']==row['tx_payload_bytes']-base['tx_payload_bytes']
        assert effect['other_group_delay_ns']=={str(i):row['group_complete_ns'][str(i)]-base['group_complete_ns'][str(i)] for i in (1,2,3)}
    parser=Links();parser.feed((study/'delivery/report.html').read_text())
    assert all((study/'delivery'/path).is_file() for path in parser.paths)
    proof=dict(passed=True,unique_networks=len(unique),calls_checked=calls_count,graph_files_per_case=len(baseline_graphs),
        all_original_graphs_equal=True,configuration_only_RTO_changes=True,packet_bytes_and_failed_retries_checked=True,
        all_final_buffers_zero=True,normal_calibration_independently_checked=True,html_resources=len(parser.paths),
        browser_rendering_verified=False,files_sha256={p.name:sha(p) for p in (study/'delivery').iterdir() if p.is_file()})
    with a.output.open('x') as s:json.dump(proof,s,indent=2);s.write('\n')
    print(json.dumps(dict(passed=True,networks=len(unique),calls=calls_count)))


if __name__=='__main__':main()
