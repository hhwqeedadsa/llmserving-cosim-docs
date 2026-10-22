"""Independent endpoint join and bisect-window verification; no new network."""
import argparse
from bisect import bisect_left
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[2]


def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False)+'\n')


def oracle(times,span):
    times=sorted(times)
    counts=[bisect_left(times,t+span)-i for i,t in enumerate(times)]
    peak=max(counts,default=0)
    return len(times),min((b-a for a,b in zip(times,times[1:])),default=None),peak,(times[counts.index(peak)] if counts else None)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task',type=Path,required=True);p.add_argument('--audit',type=Path,required=True)
    p.add_argument('--papers',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--prior-archive',type=Path,help='Relocated fixed Prefill checkpoint; hash must match the recorded original')
    a=p.parse_args();out=a.output.resolve();assert not out.exists();out.mkdir()
    old=read(ROOT/'docs/review_artifacts/prefill_pressure_20261022/archive.json')
    old_archive=a.prior_archive.resolve() if a.prior_archive else Path(old['path'])
    assert sha(old_archive)==old['sha256']
    expected=read(a.audit/'feedback-audit.json');detail=read(a.audit/'feedback-events.json')
    source=read(a.audit/'source-audit.json');source_paths={'native':a.audit/'sources/native','alibaba':a.audit/'sources/alibaba','platform':a.audit/'sources/platform'}
    for f in source['files']:assert sha(source_paths[f['repository']]/f['path'])==f['sha256']
    papers=read(a.papers/'sources.json')
    for r in papers['sources']:
        assert sha(a.papers/(r['id']+'.pdf'))==r['sha256']
        assert sha(a.papers/(r['id']+'.txt'))==r['text_sha256']
    for key,v in source['paper_excerpts'].items():
        name='roce_hoti2022' if key.startswith('hoti') else 'dcqcn2015'
        lines=(a.papers/(name+'.raw.txt')).read_text().splitlines()
        assert '\n'.join(lines[v['start_line']-1:v['end_line']])==v['text']
    audited=[];witness=None;all_detail=[];total_packets=0;total_events=0
    with tarfile.open(old_archive,'r:gz') as tar:
        for row in expected['cases']:
            label=row['case'];result_path=a.task/'run'/label/'result.json'
            original=tar.extractfile('raw/run/'+label+'/result.json').read()
            assert original==result_path.read_bytes(),'prior archived trajectory changed'
            result=json.loads(original);t=result['persistent_transport'];events=result['packet_capture']
            assert events['complete'];ci={name:i for i,name in enumerate(events['columns'])}
            streams={s['id']:s for s in t['streams']};pair={(s['src'],s['dst']):s['id'] for s in t['streams']}
            event=lambda r,k:r[ci[k]]
            # Use independently reconstructed endpoint UID pairs, then sequence ranges.
            tx_ack={event(r,'packet_uid'):r for r in events['events'] if event(r,'event')==0 and event(r,'ip_protocol')==252}
            rx_ack={event(r,'packet_uid'):r for r in events['events'] if event(r,'event')==1 and event(r,'ip_protocol')==252}
            rx_data={event(r,'packet_uid'):r for r in events['events'] if event(r,'event')==1 and event(r,'ip_protocol')==17}
            by_sequence={};np_times=defaultdict(list);rp_times=defaultdict(list);ack_tx_times=defaultdict(list)
            for packet in t['source_packets']:
                stamp,s,src,dst,nic,seq,nbytes,uid=packet
                assert (src,dst)==(streams[s]['src'],streams[s]['dst'])
                if uid not in rx_data:continue
                e=rx_data[uid];assert event(e,'node')==dst
                assert event(e,'sequence')==seq and event(e,'data_payload_bytes')==nbytes
                by_sequence[s,seq+nbytes]=e
                if event(e,'ecn')!=0:np_times[s].append(event(e,'time_ns'))
            joined=[]
            for uid,e in rx_ack.items():
                q=tx_ack[uid];s=pair[event(e,'node'),event(q,'node')]
                assert event(q,'sequence')==event(e,'sequence')
                d=by_sequence[s,event(e,'sequence')]
                if event(d,'ecn')==0:continue
                rp_times[s].append(event(e,'time_ns'));ack_tx_times[s].append(event(q,'time_ns'))
                joined.append(dict(case=label,stream=s,data_uid=event(d,'packet_uid'),ack_uid=uid,
                    end_sequence=event(e,'sequence'),np_mark_ns=event(d,'time_ns'),ack_tx_ns=event(q,'time_ns'),ack_rx_ns=event(e,'time_ns')))
            all_detail.extend(joined)
            assert row['unique_np_CE_packets']==sum(map(len,np_times.values()))
            assert row['inferred_CNP_ACKs_received']==len(joined)
            assert row['ACKs_matched']==len(rx_ack)
            assert row['source_data_packets']==len(t['source_packets'])
            for stream in row['streams']:
                s=stream['stream']
                for key,times,span in [('np_marks_50us',np_times[s],50000),('source_feedback_4us',rp_times[s],4000),
                                       ('source_feedback_50us',rp_times[s],50000),('ack_tx_50us',ack_tx_times[s],50000)]:
                    m=stream[key]
                    assert oracle(times,span)==tuple(m.get(k) for k in ('count','minimum_gap_ns','peak_count','peak_window_start_ns'))
            if len(joined):
                stream=max(row['streams'],key=lambda s:s['ack_tx_50us']['peak_count'])
                start=stream['ack_tx_50us']['peak_window_start_ns']
                samples=sorted([j for j in joined if j['stream']==stream['stream'] and start<=j['ack_tx_ns']<start+50000],key=lambda j:j['ack_tx_ns'])
                assert len(samples)==stream['ack_tx_50us']['peak_count']
                witness=dict(case=label,stream=stream['stream'],start_ns=start,end_ns=start+50000,count=len(samples),
                             scope='actual ACK transmissions, CNP bits reconstructed from original native source; not standalone CNP packets',events=samples)
            audited.append(dict(case=label,source_packets=len(t['source_packets']),endpoint_events=len(events['events']),CE=sum(map(len,np_times.values())),matched_ACK=len(rx_ack)))
            total_packets+=len(t['source_packets']);total_events+=len(events['events'])
    key=lambda r:(r['case'],r['stream'],r['ack_uid'])
    assert sorted(all_detail,key=key)==sorted(detail,key=key)
    assert witness and witness['count']>1
    save(out/'witness.json',witness)
    summary=dict(passed=True,prior_archive_sha256=old['sha256'],prior_results_byte_identical=True,
                 paper_passages_exact=True,snapshot_file_hashes_checked=len(source['files']),
                 independent_endpoint_joins=len(all_detail),source_packets=total_packets,endpoint_events=total_events,
                 density_oracle='independent sorted-time bisect for each interval start; audit uses sliding two-pointer',
                 cases=audited,witness=dict(case=witness['case'],stream=witness['stream'],start_ns=witness['start_ns'],end_ns=witness['end_ns'],count=witness['count']),
                 new_network_runs=0,hardware_calibrated=False,scope='prior fixed trajectory and source/paper agreement; no counterfactual latency or universal hardware claim')
    save(out/'verification.json',summary);print(json.dumps(summary))


if __name__=='__main__':main()
