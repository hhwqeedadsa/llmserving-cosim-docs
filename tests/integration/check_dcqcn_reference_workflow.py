"""Real native CNPs, independent feedback/state/path accounting, and old-mode checks."""
import argparse
from collections import defaultdict,deque
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]


def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')


def murmur(words,seed):
    mask=0xffffffff
    rot=lambda x,n:((x<<n)|(x>>(32-n)))&mask
    h=seed
    for k in words:
        k=rot(k*0xcc9e2d51&mask,15)*0x1b873593&mask
        h=rot(h^k,13);h=(h*5+0xe6546b64)&mask
    h^=12;h^=h>>16;h=h*0x85ebca6b&mask;h^=h>>13;h=h*0xc2b2ae35&mask;h^=h>>16
    return h


def path_oracle(topology,stream,seed):
    adj=defaultdict(list)
    for e in topology['links']:
        adj[e['src']].append((e['dst'],e['src_port']))
        adj[e['dst']].append((e['src'],e['dst_port']))
    nodes=topology['nodes'];dist={stream['src']:0};q=deque(dist)
    while q:
        at=q.popleft()
        for peer,port in adj[at]:
            if peer in dist:continue
            if nodes[peer]['kind']!='gpu' and nodes[peer]['plane']!=stream['plane']:continue
            dist[peer]=dist[at]+1
            if nodes[peer]['kind']!='gpu':q.append(peer)
    at=stream['dst'];answer=[]
    while at!=stream['src']:
        choices=[(peer,port) for peer,port in adj[at] if dist.get(peer)==dist[at]-1]
        if nodes[at]['kind']=='gpu':
            assert len(choices)==1
            peer,port=choices[0]
        else:
            sip=0x0b000001+stream['dst']*32+stream['plane']
            dip=0x0b000001+stream['src']*32+stream['plane']
            fid=stream['wire_port'];peer,port=choices[murmur((sip,dip,fid|(fid<<16)),seed+at)%len(choices)]
        answer.append((at,port));at=peer
    return answer


def np_oracle(marks,period):
    # Offline recurrence from destination CE times, independent of C++ NP state.
    times=[];last=None;pending=False
    for now in marks:
        if last is not None and pending and now>=last+period:
            last+=period;times.append(last);pending=False
        if last is None or now>=last+period:
            times.append(now);last=now
        else:pending=True
    if pending:times.append(last+period)
    return times


def audit(result,work,topology,protocol):
    assert result['passed'] and result['drained'] and result['native_network_executed']
    t=result['persistent_transport'];reference=t['dcqcn_reference'];c=work['dcqcn_reference']
    assert reference['config']==c and t['closed'] and t['physical_drained']
    events=reference['events'];streams={s['id']:s for s in work['streams']}
    cap=result['packet_capture'];assert cap['complete']
    packet=[dict(zip(cap['columns'],r)) for r in cap['events']]
    rows=t['source_rate_capture'];samples=[dict(zip(rows['columns'],r)) for r in rows['samples']]
    source_samples={r['packet_uid']:r for r in samples if r['event']==0}
    tx=defaultdict(list);rx=defaultdict(list)
    for p in packet:
        if p['event']==0:tx[p['packet_uid']].append(p)
        elif p['event']==1:rx[p['packet_uid']].append(p)
    source_by_uid={p[7]:p for p in t['source_packets']}
    marked=defaultdict(list)
    for uid,p in source_by_uid.items():
        matches=[r for r in rx[uid] if r['node']==p[3]]
        assert len(matches)==1
        if matches[0]['ecn']==3:marked[p[1]].append((matches[0]['time_ns'],uid))
    generated=[r for r in events if r['kind']=='cnp_generated']
    sent={r['packet_uid']:r for r in events if r['kind']=='cnp_tx'}
    arrived={r['packet_uid']:r for r in events if r['kind']=='cnp_received'}
    assert len(generated)==len(sent)==len(arrived)>0
    assert len({p['packet_uid'] for p in packet if p['ip_protocol']==255})==len(generated)
    path_checks=0;waiting=[];switch_waits=[]
    for r in generated:
        uid=r['packet_uid'];s=streams[r['stream']]
        assert all(p['ip_protocol']==255 and p['model_bytes']==c['cnp_model_bytes'] for p in tx[uid]+rx[uid])
        actual=sorted(tx[uid],key=lambda p:p['time_ns'])
        assert [(p['node'],p['port']) for p in actual]==path_oracle(topology,s,protocol['seed'])
        assert actual[0]['time_ns']==sent[uid]['time_ns']>=r['time_ns']
        assert arrived[uid]['time_ns']==next(p['time_ns'] for p in rx[uid] if p['node']==s['src'])
        assert arrived[uid]['time_ns']>sent[uid]['time_ns']
        waiting.append(sent[uid]['time_ns']-r['time_ns']);path_checks+=len(actual)
        for hop in actual[1:]:
            reached=[p for p in rx[uid] if p['node']==hop['node']]
            assert len(reached)==1 and reached[0]['time_ns']<=hop['time_ns']
            switch_waits.append(hop['time_ns']-reached[0]['time_ns'])
    for sid,s in streams.items():
        expected=np_oracle([a for a,u in sorted(marked[sid])],c['np_interval_ns'])
        actual=[r['time_ns'] for r in generated if r['stream']==sid]
        assert expected==actual,(sid,expected,actual)
        assert sorted((r['time_ns'],r['packet_uid']) for r in events if r['stream']==sid and r['kind']=='np_mark')==sorted(marked[sid])
        assert all(b-a>=c['np_interval_ns'] for a,b in zip(actual,actual[1:]))
    # ACK confirms transport; it cannot change the reference rate or alpha.
    before=[r for r in samples if r['event']==1];after=[r for r in samples if r['event']==2]
    assert len(before)==len(after)==len(source_by_uid)
    for a,b in zip(before,after):
        assert (a['time_ns'],a['stream'],a['packet_uid'])==(b['time_ns'],b['stream'],b['packet_uid'])
        assert not a['ack_cnp'] and not b['ack_cnp']
        for key in rows['state_columns']:
            if key!='snd_una':assert a[key]==b[key],key
        assert b['snd_una']==max(a['snd_una'],a['sequence'])
    cb=[r for r in samples if r['event']==3];ca=[r for r in samples if r['event']==4]
    assert len(cb)==len(ca)==len(generated)
    for a,b in zip(cb,ca):
        assert a['snd_una']==b['snd_una'] and a['published_bytes']==b['published_bytes']
    # Independent numerical replay: use actual source frame bytes and actual
    # CNP arrivals, not declared offered totals or a C++ state helper.
    counters=defaultdict(int);state_checks=0
    for sid,s in streams.items():
        maximum=next(e['rate_bps'] for e in topology['links'] if e['src']==s['src'] and e['src_port']==s['source_nic'])
        rate=target=maximum;alpha=1.;active=False;tr=br=0;remaining=c['recovery_bytes'];ad=rd=-1
        def recover(rate,target,tr,br):
            if max(tr,br)>=c['fast_recovery_rounds']:
                step=c['hyper_bps'] if min(tr,br)>c['fast_recovery_rounds'] else c['additive_bps']
                target=min(maximum,target+step)
            return max(c['min_rate_bps'],(rate+target)//2),target
        for e in (e for e in events if e['stream']==sid):
            kind=e['kind'];now=e['time_ns']
            if kind=='cnp_received':
                target=rate;rate=max(c['min_rate_bps'],math.floor(rate*(1-alpha/2)))
                alpha=(1-c['gain'])*alpha+c['gain'];active=True;tr=br=0;remaining=c['recovery_bytes']
                ad=now+c['alpha_interval_ns'];rd=now+c['recovery_interval_ns']
            elif kind=='rp_alpha':
                assert active and now==ad;alpha*=1-c['gain'];ad=now+c['alpha_interval_ns']
            elif kind=='rp_timer':
                assert active and now==rd;rd=now+c['recovery_interval_ns'];tr+=1
                rate,target=recover(rate,target,tr,br)
            elif kind=='source_sent':
                p=source_by_uid[e['packet_uid']]
                assert p[0]==now and p[1]==sid
                sampled=source_samples[e['packet_uid']]
                assert sampled['time_ns']==now and sampled['time_ns']>=sampled['next_avail_ns']
                assert sampled['rate_bps']==rate and sampled['target_rate_bps']==target
                assert math.isclose(sampled['alpha'],alpha,rel_tol=1e-12,abs_tol=1e-12)
                actual=next(x for x in tx[e['packet_uid']] if x['node']==s['src'])
                assert actual['model_bytes']==e['model_bytes']
                if active:
                    length=e['model_bytes']
                    while length>=remaining:
                        length-=remaining;remaining=c['recovery_bytes'];br+=1
                        rate,target=recover(rate,target,tr,br)
                    remaining-=length
            counters[kind]+=1
            assert (rate,target,tr,br,remaining,ad,rd)==tuple(e[k] for k in ('rate_bps','target_bps','timer_rounds','byte_rounds','remaining_bytes','alpha_due_ns','recovery_due_ns'))
            assert math.isclose(alpha,e['alpha'],rel_tol=1e-12,abs_tol=1e-12)
            state_checks+=1
        assert any(e['kind']=='qp_close' for e in events if e['stream']==sid)
    # Application queue slots are still released by actual ACK, including work
    # appended on the same QP; pending feedback cannot replace a completion.
    actual_work={w['id']:w for w in t['work']}
    for s in streams.values():
        ordered=sorted((w for w in actual_work.values() if w['stream']==s['id']),key=lambda w:w['ticket'])
        for i,w in enumerate(ordered):
            if i>=s['queue_depth']:assert w['published_ns']>=ordered[i-s['queue_depth']]['acked_ns']
            assert w['accepted_ns']<=w['acked_ns']<=result['finish_ns']
    assert all(s['qp_creations']==1 for s in t['streams'])
    return dict(source_packets=len(source_by_uid),CE=sum(map(len,marked.values())),CNP=len(generated),
        CNP_path_hops=path_checks,maximum_CNP_source_queue_wait_ns=max(waiting),
        nonzero_CNP_source_queue_waits=sum(x>0 for x in waiting),state_checks=state_checks,event_counts=dict(counters),
        maximum_CNP_switch_wait_ns=max(switch_waits,default=0),nonzero_CNP_switch_waits=sum(x>0 for x in switch_waits),
        final_work_ACK_ns=max(w['acked_ns'] for w in t['work']),physical_finish_ns=result['finish_ns'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',type=Path);p.add_argument('--reference-backend',type=Path)
    p.add_argument('--audit-results',type=Path,help='Recheck existing reference-on/paper-parameters/reverse-background results only; no new process or network')
    p.add_argument('--reverse-busy-from',type=Path,help='One additional functionality case from a completed workflow: keep reverse traffic active through feedback')
    p.add_argument('--reuse-completed',type=Path,action='append',default=[],help='Read hash-verified successful conditions from existing check directories; repeat to supply multiple roots; never retry a failed network')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output.resolve();assert not out.exists();out.mkdir();commands=[];reused=[];start=time.monotonic()
    if a.audit_results:
        source=a.audit_results.resolve();verified={}
        for name in ('reference-on','paper-parameters','reverse-background'):
            result=source/name/'result.json';inputs=read(source/name/'inputs.json')['hashes']
            for path in (source/'topology/topology.json',source/'protocol.json',source/(name+'-work.json')):
                assert inputs[str(path.resolve())]==hashlib.sha256(path.read_bytes()).hexdigest()
            verified[name]=audit(read(result),read(source/(name+'-work.json')),read(source/'topology/topology.json'),read(source/'protocol.json'))
        save(out/'verification.json',dict(passed=True,new_network_runs=0,conditions=verified))
        print(json.dumps(verified));return
    assert a.backend and (a.reference_backend or a.reverse_busy_from),'network workflow requires backend paths'
    env=dict(os.environ,PYTHONPATH=str(ROOT/'python'),PYTHONDONTWRITEBYTECODE='1')
    def cli(name,args,expected=0):
        assert time.monotonic()-start<55
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,args)]
        with (out/(name+'.stdout')).open('w') as stdout,(out/(name+'.stderr')).open('w') as stderr:
            run=subprocess.run(command,cwd=out,env=env,stdout=stdout,stderr=stderr,timeout=30)
        commands.append(dict(command=command,cwd=str(out),expected=expected,exitcode=run.returncode))
        save(out/'commands.json',commands);assert run.returncode==expected,name
    if a.reverse_busy_from:
        source=a.reverse_busy_from.resolve();original=source/'reverse-background'
        assert read(source/'verification.json')['passed']
        identities=read(original/'inputs.json')['hashes']
        for path in (source/'topology/topology.json',source/'protocol.json',source/'reverse-background-work.json'):
            assert identities[str(path.resolve())]==hashlib.sha256(path.read_bytes()).hexdigest()
        (out/'topology').mkdir();shutil.copy2(source/'topology/topology.json',out/'topology/topology.json')
        shutil.copy2(source/'protocol.json',out/'protocol.json')
        w=read(source/'reverse-background-work.json')
        assert w['work'][-1]['stream']==3 and w['work'][-1]['bytes']==65536
        w['work'][-1]['bytes']=1048576;save(out/'reverse-busy-work.json',w)
        cli('reverse-busy',['rdma-stream-probe','--topology','topology','--protocol','protocol.json',
            '--config','reverse-busy-work.json','--backend',a.backend.resolve(),'--output','reverse-busy'])
        verified=audit(read(out/'reverse-busy/result.json'),w,read(out/'topology/topology.json'),read(out/'protocol.json'))
        assert verified['maximum_CNP_source_queue_wait_ns']>4 or verified['maximum_CNP_switch_wait_ns']>0
        save(out/'verification.json',dict(passed=True,condition=verified,new_network_runs=1,hardware_calibrated=False,
            reason='64KiB background drained before CNPs; 1MiB keeps actual reverse traffic active, within original per-work/clock limits'))
        print(json.dumps(verified));return
    save(out/'topology-input.json',read(ROOT/'examples/scaleout_rdma/small-clos-100g.json'))
    cli('topology',['topology','--config','topology-input.json','--output','topology'])
    topology=read(out/'topology/topology.json')
    protocol=read(ROOT/'examples/scaleout_rdma/rdma.json');protocol['queue_metrics']=True
    protocol['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000)
    protocol['ecn_by_rate']['100000000000']=dict(kmin_bytes=1000,kmax_bytes=2000,pmax=1)
    save(out/'protocol.json',protocol)
    work=dict(schema_version='moe-rdma-stream-work/1',streams=[dict(id=i,src=s,dst=6,plane=0,
        wire_port=23000+i,source_nic=1,destination_nic=1,queue_depth=1) for i,s in enumerate((0,2,4))],
        work=[dict(id=i,stream=i,bytes=131072,after=[]) for i in range(3)]+
             [dict(id=i+3,stream=i,bytes=4096,after=[dict(work=i,event='accepted')]) for i in range(3)])
    def run(name,w,binary=None,expected=0):
        save(out/(name+'-work.json'),w)
        previous=next((base.resolve()/name for base in a.reuse_completed if (base/name).is_dir()),None)
        if previous and previous.exists():
            assert not expected and read(previous/'result.json')['passed']
            state=read(previous/'execution/probe.json')
            assert state['status']=='completed' and state['child_exit_code']==0
            inputs=read(previous/'inputs.json')['hashes']
            for path in (previous.parent/'topology/topology.json',previous.parent/'protocol.json',
                         previous.parent/(name+'-work.json'),(binary or a.backend).resolve()):
                assert inputs[str(path.resolve())]==hashlib.sha256(path.read_bytes()).hexdigest()
            assert read(previous.parent/'topology/topology.json')==topology
            assert read(previous.parent/'protocol.json')==protocol
            assert read(previous.parent/(name+'-work.json'))==w
            reused.append(dict(condition=name,source=str(previous),result_sha256=hashlib.sha256((previous/'result.json').read_bytes()).hexdigest()))
            save(out/'reused.json',reused)
            return read(previous/'result.json')
        cli(name,['rdma-stream-probe','--topology','topology','--protocol','protocol.json','--config',name+'-work.json',
                  '--backend',(binary or a.backend).resolve(),'--output',name],expected)
        if not expected:return read(out/name/'result.json')
    old=run('native-old',work,a.reference_backend);new=run('native-new',work)
    # Adding the reference controller introduces a direct libm dependency;
    # dl_iterate_phdr order can change while library identities are the same.
    assert sorted(old['loaded_libraries'])==sorted(new['loaded_libraries'])
    old_network=deepcopy(old);new_network=deepcopy(new)
    old_network.pop('loaded_libraries');new_network.pop('loaded_libraries')
    assert old_network==new_network
    w=deepcopy(work);w['dcqcn_reference']=read(ROOT/'examples/scaleout_rdma_streams/work-reference.json')['dcqcn_reference']
    w['dcqcn_reference'].update(np_interval_ns=5000,alpha_interval_ns=6000,recovery_interval_ns=3000,recovery_bytes=4096,gain=.25)
    off=run('reference-off',w)
    w['source_rate_capture']=dict(start_ns=0,end_ns=5000000,max_events=100000)
    on=run('reference-on',w);stripped=deepcopy(on);del stripped['persistent_transport']['source_rate_capture']
    assert off==stripped
    verified={'fast':audit(on,w,topology,protocol)}
    assert verified['fast']['event_counts'].get('rp_timer',0)>0
    assert verified['fast']['event_counts'].get('rp_bytes',0)>0
    assert verified['fast']['nonzero_CNP_source_queue_waits']>0
    repeat=run('reference-repeat',w);assert on==repeat
    paper=deepcopy(w);paper['dcqcn_reference']=read(ROOT/'examples/scaleout_rdma_streams/work-reference.json')['dcqcn_reference']
    paper_result=run('paper-parameters',paper);verified['paper_parameters']=audit(paper_result,paper,topology,protocol)
    reverse=deepcopy(w);reverse['streams'].append(dict(id=3,src=6,dst=0,plane=0,wire_port=23003,source_nic=1,destination_nic=1,queue_depth=1))
    reverse['work'].append(dict(id=6,stream=3,bytes=65536,after=[]))
    rev=run('reverse-background',reverse);verified['reverse_background']=audit(rev,reverse,topology,protocol)
    overflow=deepcopy(w);overflow['dcqcn_reference']['max_events']=1
    run('event-overflow',overflow,expected=1)
    assert not (out/'event-overflow/result.json').exists()
    assert 'reference feedback event budget exceeded' in (out/'event-overflow/execution/stderr.log').read_text()
    bad=deepcopy(w);bad['dcqcn_reference']['gain']=True
    run('invalid-reference',bad,expected=1);assert not (out/'invalid-reference').exists()
    command=[str(a.backend.resolve()),str(out/'topology/topology.json'),str(out/'protocol.json'),
             str(out/'invalid-reference-work.json'),str(out/'native-rejected.json')]
    rejected=subprocess.run(command,cwd=out,capture_output=True,text=True,timeout=10)
    (out/'native-rejection.stderr').write_text(rejected.stderr)
    assert rejected.returncode==1 and 'invalid reference gain' in rejected.stderr
    assert not (out/'native-rejected.json').exists()
    commands.append(dict(command=command,cwd=str(out),expected=1,exitcode=1));save(out/'commands.json',commands)
    verified.update(passed=True,old_default_identical=True,reference_capture_passive=True,repeat_identical=True,
        public_commands=len(commands)-1,native_rejections=1,hardware_calibrated=False,
        successful_network_conditions=7,new_network_runs=8-len(reused),expected_event_budget_failure=True,
        reused_completed_conditions=reused,
        scope='artificial functionality; not real Prefill rerun or performance/production evidence')
    save(out/'verification.json',verified);print(json.dumps(verified))


if __name__=='__main__':main()
