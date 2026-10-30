"""Freeze prior results and audit source-observed recovery batches without simulation."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import gc
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
CASES=('normal','local-w20','prefix-w20','prefix-w60')


def load(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
    with Path(p).open('x') as f:json.dump(v,f,indent=2,ensure_ascii=False);f.write('\n')


def prepare(a):
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    prior=a.previous.resolve();analysis=load(prior/'analysis.json');receipt=load(prior/'receipt.json')
    assert receipt['passed'] and sha(prior/'analysis.json')==receipt['delivery_sha256']['analysis.json']
    for path,h in receipt['compiled_sources_sha256'].items():assert sha(ROOT/path)==h
    command=[sys.executable,'-B',str(Path(__file__).with_name('sources.py')),'import',
      '--acquired',str(a.acquired.resolve()),'--output',str(out/'sources')]
    with (out/'sources.stdout.log').open('x') as stdout,(out/'sources.stderr.log').open('x') as stderr:
        result=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr)
    save(out/'sources-command.json',dict(command=command,cwd=str(ROOT),exit_code=result.returncode))
    assert result.returncode==0
    sources=load(out/'sources/sources.json');assert sources['passed']
    devices=[]
    for d in sorted(Path('/sys/class/infiniband').glob('mlx5_*')):
        values={}
        for name in ('hca_type','fw_ver','ports/1/state','ports/1/link_layer','ports/1/rate'):
            p=d/name
            values[name]=p.read_text().strip() if p.is_file() else None
        devices.append(dict(device=d.name,attributes=values))
    save(out/'hardware.json',dict(sysfs_devices=devices,
      preflight_verbs_query=dict(command=['ibv_devinfo','-d','mlx5_0','-i','1'],exit_code=255,
        diagnostic="IB device 'mlx5_0' wasn't found",provenance='read-only preflight query before input freeze'),
      calibrated=False,scope='sysfs visibility does not prove userspace access or an active two-host RoCE link'))
    cases={name:dict(directory=analysis['cases'][name]['directory'],result_sha256=analysis['cases'][name]['result_sha256']) for name in CASES}
    for row in cases.values():assert sha(Path(row['directory'])/'result.json')==row['result_sha256']
    code=[*sorted(Path(__file__).parent.glob('*.py')),Path(__file__).with_name('README.md')]
    save(out/'frozen.json',dict(passed=True,prior_directory=str(prior),prior_analysis_sha256=sha(prior/'analysis.json'),
      prior_receipt_sha256=sha(prior/'receipt.json'),cases=cases,
      source_sha256={str(p.relative_to(ROOT)):sha(p) for p in code},
      public_source_sha256={row['path']:row['sha256'] for row in sources['sources']},
      hardware_sha256=sha(out/'hardware.json'),
      platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
      budget=dict(monitored_seconds=300,artifact_bytes=64*2**20,min_free_bytes=8*2**30),new_networks=0))
    print(json.dumps(dict(prepared=True,new_networks=0,cases=len(cases))))


def checked(directory):
    f=load(directory/'frozen.json')
    for path,h in f['source_sha256'].items():assert sha(ROOT/path)==h
    for path,h in f['public_source_sha256'].items():assert sha(directory/'sources'/path)==h
    assert sha(directory/'hardware.json')==f['hardware_sha256']
    for c in f['cases'].values():assert sha(Path(c['directory'])/'result.json')==c['result_sha256']
    return f


def union(intervals):
    result=[]
    for left,right in sorted(intervals):
        if left>=right:continue
        if result and left<=result[-1][1]:result[-1]=(result[-1][0],max(right,result[-1][1]))
        else:result.append((left,right))
    return result


def subtract(intervals,removed):
    result=[]
    for left,right in intervals:
        cursor=left
        for a,b in union(removed):
            if b<=cursor or a>=right:continue
            if a>cursor:result.append((cursor,min(a,right)))
            cursor=max(cursor,min(b,right))
        if cursor<right:result.append((cursor,right))
    return result


def length(intervals):return sum(b-a for a,b in intervals)


def examine(d):
    r=load(d/'result.json');assert r['passed'] and r['drained'] and r['gpu_count']==128
    n=r['service_transport']['native'];sr=n['selective_repeat']['events']
    recovery=[dict(zip(n['recovery']['columns'],row)) for row in n['recovery']['events']]
    source=[dict(zip(n['source_packet_columns'],row)) for row in n['source_packets']]
    high=defaultdict(int);by_stream=defaultdict(list)
    for p in source:
        end=p['sequence']+p['payload_bytes'];assert p['sequence']<=high[p['stream']]
        p['repeated_bytes']=max(0,min(end,high[p['stream']])-p['sequence'])
        high[p['stream']]=max(end,high[p['stream']]);by_stream[p['stream']].append(p)
    triggers=[]
    for x in recovery:
        if x['event']==n['recovery']['event_codes']['timeout_retransmit']:
            triggers.append(dict(kind='timeout',time_ns=x['time_ns'],stream=x['message_id'],
                source=x['source'],ack=x['acknowledged_bytes'],high=x['highest_sent_bytes'],source_event=x))
    for i,x in enumerate(sr):
        if x['kind']=='fast_retransmit':
            prior=[p for p in by_stream[x['stream']] if p['time_ns']<x['time_ns']]
            assert prior
            triggers.append(dict(kind='fast_retransmit',time_ns=x['time_ns'],stream=x['stream'],
                source=prior[0]['src'],ack=x['cumulative_ack'],high=max(p['sequence']+p['payload_bytes'] for p in prior),
                selective_index=i,source_event=x))
    streams={s['id']:s for s in n['streams']};port_rates={(p['node'],p['port']):p['rate_bps'] for p in r['ports']}
    batches=[]
    for t in sorted(triggers,key=lambda x:(x['time_ns'],x['stream'],x['kind'])):
        now,sid=t['time_ns'],t['stream'];nic=streams[sid]['source_nic']
        prior_feedback=[];ties=[]
        for i,x in enumerate(sr):
            if x['kind']!='feedback_received' or x['stream']!=sid:continue
            if x['time_ns']<now or (x['time_ns']==now and t['kind']=='fast_retransmit' and i<t['selective_index']):
                prior_feedback.append(x)
            elif x['time_ns']==now:ties.append(x)
        reported=[(x['sack_begin'],x['sack_end']) for x in prior_feedback if x['sack_valid']]
        missing=subtract([(t['ack'],t['high'])],reported);remaining=deepcopy(missing)
        future=[x['time_ns'] for x in sr if x['kind']=='feedback_received' and x['stream']==sid and x['time_ns']>now]
        future.extend(x['time_ns'] for x in triggers if x['stream']==sid and x['time_ns']>now)
        cutoff=min(future,default=r['finish_ns']+1)
        sent=[]
        for p in by_stream[sid]:
            if p['time_ns']<now or p['time_ns']>=cutoff or not p['repeated_bytes']:continue
            updated=subtract(remaining,[(p['sequence'],p['sequence']+p['payload_bytes'])])
            if length(updated)<length(remaining):sent.append(p);remaining=updated
            if not remaining:break
        complete=bool(missing) and not remaining and not ties
        first=sent[0]['time_ns'] if sent else None
        last=sent[-1]['time_ns'] if sent else None
        end=max((p['time_ns']+round((p['payload_bytes']+36)*8*10**9/port_rates[p['src'],p['nic']]) for p in sent),default=None)
        other=[]
        if end is not None:
            for p in source:
                if (p['src'],p['nic'])!=(t['source'],nic) or p['stream']==sid:continue
                duration=round((p['payload_bytes']+36)*8*10**9/port_rates[p['src'],p['nic']])
                overlap=max(0,min(end,p['time_ns']+duration)-max(now,p['time_ns']))
                if overlap:other.append(dict(source_packet=p,overlap_ns=overlap))
        own=sum(round((p['payload_bytes']+36)*8*10**9/port_rates[p['src'],p['nic']]) for p in sent)
        batches.append(dict(trigger=t,nic=nic,source_feedback=prior_feedback,same_ns_unordered_feedback=ties,
            missing_ranges=missing,missing_bytes=length(missing),cutoff_ns=cutoff,
            actual_retransmission_packets=sent,uncovered_ranges=remaining,fully_covered_before_next_feedback=complete,
            first_retransmission_ns=first,first_retransmission_delay_ns=None if first is None else first-now,
            last_retransmission_start_ns=last,batch_serialization_end_ns=end,
            batch_span_ns=None if end is None else end-now,own_serialization_ns=own,
            other_QP_serialization=other,other_QP_occupied_ns=sum(x['overlap_ns'] for x in other),
            unexplained_or_interpacket_ns=None if end is None else end-now-own-sum(x['overlap_ns'] for x in other)))
    source_wait=[]
    for x in batches:
        if x['first_retransmission_ns'] is not None and x['first_retransmission_delay_ns']>0:
            source_wait.append((x['trigger']['source'],x['nic'],x['trigger']['time_ns'],x['first_retransmission_ns'],x['trigger']['stream']))
    overlaps=[]
    for i,a in enumerate(source_wait):
        for b in source_wait[i+1:]:
            amount=max(0,min(a[3],b[3])-max(a[2],b[2]))
            if a[:2]==b[:2] and a[4]!=b[4] and amount:overlaps.append(dict(first=a,second=b,overlap_ns=amount))
    logical=sum(m['bytes'] for m in r['service_transport']['messages'])
    assert sum(high.values())==logical
    return dict(result_sha256=sha(d/'result.json'),logical_payload_bytes=logical,
        repeated_payload_bytes=sum(p['repeated_bytes'] for p in source),
        trigger_counts=dict(Counter(t['kind'] for t in triggers)),batches=batches,
        positive_first_send_wait_count=len(source_wait),simultaneous_waiting_recoveries=overlaps,
        complete_batches=sum(x['fully_covered_before_next_feedback'] for x in batches),
        same_ns_feedback_ambiguous_batches=sum(bool(x['same_ns_unordered_feedback']) for x in batches),
        max_first_send_wait_ns=max((x['first_retransmission_delay_ns'] or 0 for x in batches),default=0),
        other_QP_occupied_ns=sum(x['other_QP_occupied_ns'] for x in batches),
        max_batch_span_ns=max((x['batch_span_ns'] or 0 for x in batches),default=0))


def excerpts(directory):
    specs={'rfc6298.txt':['(5.3)','segment is never retransmitted too early'],
      'rfc8985.txt':['RACK-TLP uses per-segment transmit timestamps','Tail Loss Probe (TLP)'],
      'rxe_comp.c':['static void reset_retry_timer','reset_retry_timer(qp);'],
      'rxe_qp.c':['timeout = 4.096','qp->qp_timeout_jiffies = j ? j : 1'],
      'rxe_req.c':['ack_req = ((pkt->mask','!timer_pending(&qp->retrans_timer)']}
    result={}
    for file,needles in specs.items():
        lines=(directory/file).read_text().splitlines();rows=[]
        for needle in needles:
            hits=[i for i,line in enumerate(lines) if needle in line];assert hits,(file,needle)
            for i in hits:
                start=max(0,i-1);end=min(len(lines),i+11)
                rows.append(dict(anchor=needle,line_start=start+1,line_end=end,text='\n'.join(lines[start:end])))
        result[file]=rows
    return result


def analyze(a):
    f=checked(a.input);cases={}
    for name,row in f['cases'].items():
        cases[name]=examine(Path(row['directory']));gc.collect()
    result=dict(passed=True,frozen_sha256=sha(a.input/'frozen.json'),cases=cases,
      public_source_excerpts=excerpts(a.input/'sources'),hardware=load(a.input/'hardware.json'),
      scope='observed source-local batch opportunities; no new network or counterfactual completion estimate')
    save(a.output,result)
    print(json.dumps({n:{k:v for k,v in row.items() if k not in ('batches','result_sha256')} for n,row in cases.items()}))


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('prepare')
    for k in ('previous','acquired','output'):q.add_argument('--'+k,type=Path,required=True)
    q=sub.add_parser('analyze')
    for k in ('input','output'):q.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();globals()[a.command](a)


if __name__=='__main__':main()
