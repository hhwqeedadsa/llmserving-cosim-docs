"""Read-only DCQCN abstraction and existing endpoint-feedback audit."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[2]
NATIVE=ROOT/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
ALI=ROOT/'data/research/ns-3-alibabacloud-3e0c7c1'
HW='src/point-to-point/model/rdma-hw.cc'
FUNCTIONS=('UpdateAlphaMlx','ScheduleUpdateAlphaMlx','cnp_received_mlx','CheckRateDecreaseMlx',
           'ScheduleDecreaseRateMlx','RateIncEventTimerMlx','RateIncEventMlx','FastRecoveryMlx',
           'ActiveIncreaseMlx','HyperIncreaseMlx')


def read(path):return json.loads(path.read_text())
def digest(data):return hashlib.sha256(data).hexdigest()
def sha(path):return digest(path.read_bytes())
def save(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def function(text,name):
    match=re.search(r'\b(?:void|int) RdmaHw::'+name+r'\([^\n]*\)\s*\{',text)
    assert match,name
    end=text.index('\n}',match.start())+2
    return text[match.start():end],text[:match.start()].count('\n')+1


def tokens(text):
    text=re.sub(r'/\*.*?\*/','',text,flags=re.S)
    text=re.sub(r'//[^\n]*','',text)
    return re.findall(r'\w+|[^\s\w]',text)


def passage(text,needle,after):
    lines=text.splitlines();positions=[i for i,line in enumerate(lines) if needle in line]
    assert len(positions)==1,(needle,positions)
    index=positions[0]
    return dict(start_line=index+1,end_line=index+after,text='\n'.join(lines[index:index+after]))


def source_audit(papers,out,alibaba=ALI):
    files=[];texts={}
    sources=[('native',NATIVE,'3964b1d41cb5f7b51004c316b0eae1a3713c2498',
              [HW,'src/point-to-point/model/rdma-hw.h','src/point-to-point/model/rdma-queue-pair.h',
               'scratch/common.h','scratch/config/config_clos.txt','README.md']),
             ('alibaba',alibaba,'3e0c7c1bfbbe9f77890ddcf5e5b9c79fc6dd7437',['simulation/'+HW]),
             ('platform',ROOT,'68ad3fe',['backend/rdma/fabric.cc','backend/rdma/persistent_streams.cc'])]
    for label,repo,commit,paths in sources:
        full=subprocess.check_output(['git','rev-parse',commit],cwd=repo,text=True).strip()
        if label!='platform':
            assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()==full
        for path in paths:
            blob=subprocess.check_output(['git','show',full+':'+path],cwd=repo)
            assert (repo/path).read_bytes()==blob,'changed source: '+path
            target=out/'sources'/label/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(blob)
            files.append(dict(repository=label,commit=full,path=path,sha256=digest(blob),bytes=len(blob),
                              git_blob_sha1=hashlib.sha1(b'blob '+str(len(blob)).encode()+b'\0'+blob).hexdigest()))
            texts[label,path]=blob.decode()
    native=texts['native',HW];ali=texts['alibaba','simulation/'+HW]
    matched=[]
    for name in FUNCTIONS:
        left,line=function(native,name);right,other=function(ali,name)
        same=tokens(left)==tokens(right)
        matched.append(dict(function=name,native_line=line,alibaba_line=other,tokens_identical=same,
                            native_token_sha256=digest(' '.join(tokens(left)).encode()),
                            alibaba_token_sha256=digest(' '.join(tokens(right)).encode())))
    receiver,_=function(native,'ReceiveUdp');sender,_=function(native,'ReceiveAck')
    explicit,_=function(native,'ReceiveCnp');pkt_sent,_=function(native,'PktSent')
    assert 'if (ecnbits)\n\t\t\tseqh.SetCnp();' in receiver
    assert 'cnp_received_mlx(qp)' in sender and 'cnp_received_mlx' not in explicit
    # A historical PCIe comment mentions GetMicroSeconds; comments are not calls.
    receiver_tokens=tokens(receiver)
    assert 'Schedule' not in receiver_tokens and 'MicroSeconds' not in receiver_tokens
    assert 'RateIncEventMlx' not in pkt_sent
    assert len(re.findall(r'\bRateIncEventMlx\s*\(',native))==2
    ali_receiver,_=function(ali,'ReceiveUdp')
    assert 'if (ecnbits)\n\t\t\tseqh.SetCnp();' in ali_receiver
    default_values={}
    for name,value in [('RPTimer','DoubleValue(1500.0)'),('EwmaGain','DoubleValue(1.0 / 16)'),
                       ('AlphaResumInterval','DoubleValue(55.0)'),('RateDecreaseInterval','DoubleValue(4.0)'),
                       ('RateOnFirstCnp','DoubleValue(1.0)'),('MinRate','DataRateValue(DataRate("100Mb/s"))')]:
        start=re.search(r'\.AddAttribute\s*\(\s*"'+name+r'"',native).end()
        assert value in native[start:native.index('Make',start)]
        default_values[name]=value
    config={line.split()[0]:line.split()[1:] for line in texts['native','scratch/config/config_clos.txt'].splitlines() if line.strip()}
    assert config['CC_MODE']==['12']
    common=texts['native','scratch/common.h']
    assert 'rdmaHw->SetAttribute("RPTimer", DoubleValue(rp_timer));' in common
    assert 'DoubleValue(alpha_resume_interval)' in common
    original=read(papers/'sources.json');paper_text={}
    for record in original['sources']:
        assert record['status']=='read'
        assert sha(papers/(record['id']+'.pdf'))==record['sha256']
        paper_text[record['id']]=(papers/(record['id']+'.raw.txt')).read_text()
    d=paper_text['dcqcn2015'];h=paper_text['roce_hoti2022']
    excerpts={
        'np_generation':passage(d,'CNP is sent immediately. Then, the NIC generates at most',6),
        'rate_increase_timer_and_bytes':passage(d,'Furthermore, RP increases its sending rate using a timer and',9),
        'initial_parameters':passage(d,'We start with recommended parameters from QCN and',4),
        'initial_nonconvergence':passage(d,'Unfortunately we find that with these parameter values,',10),
        'final_parameters_and_scope':passage(d,'In conclusion, to achieve fast convergence to fairness, we',16),
        'hoti_collective_chunking':passage(h,'break each collective into four equally sized chunks and',4),
        'hoti_tuning':passage(h,'parameters in switch to be set. We tuned the parameter in',6),
        'hoti_overhead':passage(h,'introduce considerable overhead at the endpoints, requiring',8),
    }
    for key in ('rate_increase_timer_and_bytes','hoti_overhead'):
        assert len(excerpts[key]['text'])>100
    result=dict(files=files,control_functions=matched,default_values=default_values,
                native_example_config={k:config[k] for k in ('CC_MODE','ALPHA_RESUME_INTERVAL','RP_TIMER','EWMA_GAIN','FAST_RECOVERY_TIMES','RATE_AI','RATE_HAI','MIN_RATE')},
                paper_excerpts=excerpts,papers=original['sources'],
                assertions=dict(per_marked_packet_ACK_CNP=True,NP_50us_holdoff_in_ReceiveUdp=False,
                                standalone_ReceiveCnp_calls_DCQCN_update=False,
                                DCQCN_rate_increase_has_byte_trigger_on_this_path=False,
                                native_example_is_mode1=False,hardware_calibration_established=False))
    save(out/'source-audit.json',result)
    return result


def density(times,window):
    times=sorted(times)
    if not times:return dict(count=0,minimum_gap_ns=None,peak_count=0,peak_window_start_ns=None)
    left=0;peak=0;start=None
    for right,now in enumerate(times):
        while now-times[left]>=window:left+=1
        count=right-left+1
        if count>peak:peak=count;start=times[left]
    return dict(count=len(times),minimum_gap_ns=min((b-a for a,b in zip(times,times[1:])),default=None),
                peak_count=peak,peak_window_start_ns=start,window_ns=window,window='left-closed right-open')


def traffic_audit(task,out):
    rows=[];detailed=[];identities=[]
    for label in ('layer4-depth1','layer4-depth8'):
        work_path=task/'observed'/(label+'.json');result_path=task/'run'/label/'result.json'
        spec=read(work_path);result=read(result_path);protocol_path=task/'observed/protocol.json'
        inp=read(task/'run'/label/'inputs.json')
        for path in (work_path,protocol_path):
            matches=[v for recorded,v in inp['hashes'].items() if Path(recorded).name==path.name]
            assert matches==[sha(path)],'relocated input must match its unique recorded filename/hash'
        assert result['packet_capture']['complete']
        assert not result['fault_drops'] and all(not p['pfc_pause'] for p in result['ports'])
        transport=result['persistent_transport'];streams={s['id']:s for s in spec['streams']}
        pairs={(s['src'],s['dst']):s['id'] for s in spec['streams']};assert len(pairs)==len(streams)
        events=[dict(zip(result['packet_capture']['columns'],p)) for p in result['packet_capture']['events']]
        source={p[7]:dict(time=p[0],stream=p[1],src=p[2],dst=p[3],sequence=p[5],payload=p[6]) for p in transport['source_packets']}
        assert len(source)==len(transport['source_packets'])
        delivered={};by_end={};ack_tx={};ack_rx={};cnp_packets=0
        for e in events:
            if e['ip_protocol']==255:cnp_packets+=1
            if e['ip_protocol']==17 and e['event']==1 and e['packet_uid'] in source:
                p=source[e['packet_uid']]
                if e['node']!=p['dst']:continue
                assert e['sequence']==p['sequence'] and e['data_payload_bytes']==p['payload']
                assert e['packet_uid'] not in delivered
                delivered[e['packet_uid']]=e
                key=p['stream'],p['sequence']+p['payload'];assert key not in by_end;by_end[key]=e
            if e['ip_protocol']==252:
                if e['event']==0:
                    assert e['packet_uid'] not in ack_tx;ack_tx[e['packet_uid']]=e
                elif e['event']==1:
                    assert e['packet_uid'] not in ack_rx;ack_rx[e['packet_uid']]=e
            assert e['ip_protocol']!=253,'NACK excluded from this no-reorder audit'
        per_stream={s:dict(np_marks=[],ack_receives=[],ack_transmits=[]) for s in streams}
        for uid,e in delivered.items():
            if e['ecn']!=0:per_stream[source[uid]['stream']]['np_marks'].append(e['time_ns'])
        matched_acks=0
        for uid,e in ack_rx.items():
            assert uid in ack_tx
            tx=ack_tx[uid];stream=pairs[e['node'],tx['node']]
            assert tx['sequence']==e['sequence']
            data=by_end[stream,e['sequence']]
            assert data['time_ns']<=tx['time_ns']<e['time_ns']
            matched_acks+=1
            if data['ecn']!=0:
                per_stream[stream]['ack_receives'].append(e['time_ns'])
                per_stream[stream]['ack_transmits'].append(tx['time_ns'])
                detailed.append(dict(case=label,stream=stream,data_uid=data['packet_uid'],ack_uid=uid,
                    end_sequence=e['sequence'],np_mark_ns=data['time_ns'],ack_tx_ns=tx['time_ns'],ack_rx_ns=e['time_ns']))
        summaries=[]
        for stream,t in per_stream.items():
            summaries.append(dict(stream=stream,source=streams[stream]['src'],destination=streams[stream]['dst'],
                np_marks_50us=density(t['np_marks'],50000),source_feedback_4us=density(t['ack_receives'],4000),
                source_feedback_50us=density(t['ack_receives'],50000),ack_tx_50us=density(t['ack_transmits'],50000)))
        info=dict(case=label,passed=result['passed'],observation_stop_ns=result['finish_ns'],completed_work=result['completed_work'],
                  offered_work=len(spec['work']),source_data_packets=len(source),delivered_data_packets=len(delivered),
                  unique_np_CE_packets=sum(len(t['np_marks']) for t in per_stream.values()),
                  inferred_CNP_ACKs_received=sum(len(t['ack_receives']) for t in per_stream.values()),
                  ACKs_matched=matched_acks,standalone_CNP_hook_events=cnp_packets,streams=summaries)
        assert info['passed']==(label=='layer4-depth1')
        rows.append(info)
        identities.extend(dict(path=str(p),sha256=sha(p),bytes=p.stat().st_size) for p in (work_path,result_path,protocol_path))
    save(out/'feedback-events.json',detailed)
    result=dict(cases=rows,input_identities=identities,
                CNP_bit_scope='inferred from fixed ReceiveUdp ECN-to-ACK branch and actual matched endpoint packets; ACK flags not in old packet capture',
                density_scope='existing trajectory only; no filtered feedback closed-loop simulation or alternate completion prediction',
                network_reexecuted=False,hardware_calibrated=False)
    save(out/'feedback-audit.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--papers',type=Path,required=True);p.add_argument('--task',type=Path,required=True)
    p.add_argument('--alibaba-source',type=Path,default=ALI,help='Existing read-only clone at fixed3e0c7c1; never downloaded or changed')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output.resolve();assert not out.exists();out.mkdir()
    source=source_audit(a.papers.resolve(),out,a.alibaba_source.resolve());traffic=traffic_audit(a.task.resolve(),out)
    print(json.dumps(dict(source_functions=len(source['control_functions']),
        identical_functions=sum(r['tokens_identical'] for r in source['control_functions']),
        cases=[dict(case=r['case'],CE=r['unique_np_CE_packets'],CNP_ACK=r['inferred_CNP_ACKs_received'],
                    peak_50us=max(s['np_marks_50us']['peak_count'] for s in r['streams'])) for r in traffic['cases']])))


if __name__=='__main__':main()
