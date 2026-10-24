"""Independent NP membership, raw physical/queue, work and rational RP oracle."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
from fractions import Fraction
import math
from pathlib import Path
from experiments.feedback_ancestry.stage import load,save,sha
from experiments.local_fault_baselines.routing import check_routes


def rp_oracle(r,reported):
    t=r['persistent_transport'];c=t['dcqcn_reference']['config'];gain=Fraction(str(c['gain']));states={};count=0
    ports={(p['node'],p['port']):p for p in r['ports']};streams={s['id']:s for s in t['streams']}
    samples=t['source_rate_capture'];before={x['packet_uid']:x for x in (dict(zip(samples['columns'],row)) for row in samples['samples']) if x['event']==3}
    notices={n['packet_uid']:n for n in reported['notices']}
    source_samples={x['packet_uid']:x for x in (dict(zip(samples['columns'],row)) for row in samples['samples']) if x['event']==0}
    def recovery(s):
        if max(s['tr'],s['br'])>=c['fast_recovery_rounds']:
            inc=c['hyper_bps'] if min(s['tr'],s['br'])>c['fast_recovery_rounds'] else c['additive_bps']
            s['target']=min(s['maximum'],s['target']+inc)
        s['rate']=max(c['min_rate_bps'],(s['target']+s['rate'])//2)
    for e in t['dcqcn_reference']['events']:
        sid=e['stream'];now=e['time_ns'];kind=e['kind']
        if sid not in states:
            m=ports[streams[sid]['src'],streams[sid]['source_nic']]['rate_bps']
            states[sid]=dict(maximum=m,rate=m,target=m,alpha=Fraction(1),tr=0,br=0,remaining=c['recovery_bytes'],ad=-1,rd=-1,active=False)
        s=states[sid]
        if kind=='cnp_received':
            n=notices[e['packet_uid']];b=before[e['packet_uid']]
            assert n['rate_before_bps']==b['rate_bps']==s['rate'] and math.isclose(n['alpha_before'],float(s['alpha']),abs_tol=1e-12)
            s['target']=s['rate'];ratio=s['rate']*(1-s['alpha']/2)
            s['rate']=max(c['min_rate_bps'],ratio.numerator//ratio.denominator)
            s['alpha']=(1-gain)*s['alpha']+gain;s.update(tr=0,br=0,remaining=c['recovery_bytes'],ad=now+c['alpha_interval_ns'],rd=now+c['recovery_interval_ns'],active=True)
            assert n['rate_after_bps']==s['rate'] and math.isclose(n['alpha_after'],float(s['alpha']),abs_tol=1e-12)
        elif kind=='rp_alpha':
            assert s['active'] and now==s['ad'];s['alpha']*=1-gain;s['ad']+=c['alpha_interval_ns']
        elif kind=='rp_timer':
            assert s['active'] and now==s['rd'];s['rd']+=c['recovery_interval_ns'];s['tr']+=1;recovery(s)
        elif kind=='source_sent':
            sample=source_samples[e['packet_uid']]
            assert sample['time_ns']==now and sample['rate_bps']==s['rate'] and math.isclose(sample['alpha'],float(s['alpha']),abs_tol=1e-12)
            todo=e['model_bytes'] if s['active'] else 0
            while todo>=s['remaining']:
                todo-=s['remaining'];s['remaining']=c['recovery_bytes'];s['br']+=1;recovery(s)
            s['remaining']-=todo
        assert (s['rate'],s['target'],s['tr'],s['br'],s['remaining'],s['ad'],s['rd'])==tuple(e[k] for k in ('rate_bps','target_bps','timer_rounds','byte_rounds','remaining_bytes','alpha_due_ns','recovery_due_ns'))
        assert math.isclose(float(s['alpha']),e['alpha'],abs_tol=1e-12);count+=1
    return count


def check(inputs,case,report):
    r=load(inputs/'network'/case/'result.json');t=r['persistent_transport'];cap=r['packet_capture'];assert cap['complete'] and r['passed'] and r['drained']
    packet=[dict(zip(cap['columns'],row)) for row in cap['events']];lineages=defaultdict(list);inventory=defaultdict(dict);dequeues={};ack={};prefixes={}
    streams={s['id']:s for s in t['streams']};works={w['id']:w for w in t['work']};ports={(p['node'],p['port']):p for p in r['ports']}
    for e in packet:
        lineages[e['packet_uid']].append(e);sid=e['message_id'];node=e['node']
        if e['event']==1 and sid in streams and node==streams[sid]['src'] and e['ip_protocol'] in (252,253):ack[sid]=max(ack.get(sid,0),e['sequence'])
        if e['event']==1 and e['ip_protocol']==255:
            for g in t['dcqcn_reference']['events']:
                if g['kind']=='cnp_generated' and g['packet_uid']==e['packet_uid'] and node==streams[g['stream']]['src']:
                    prefixes[e['packet_uid']]=ack.get(g['stream'],0)
        if node<r['gpu_count'] or e['ip_protocol']!=17:continue
        key=node,e['port'];q=inventory[key]
        if e['event']==3:
            assert e['packet_uid'] not in q;q[e['packet_uid']]=e
        elif e['event']==4:
            assert q.pop(e['packet_uid'])['sequence']==e['sequence'];remaining=Counter()
            for v in q.values():remaining[str(v['message_id'])]+=v['model_bytes']
            dequeues[e['packet_uid'],*key]=(e,dict(remaining))
    assert not any(inventory.values())
    source={x['packet_uid']:x for x in (dict(zip(t['source_packet_columns'],row)) for row in t['source_packets'])}
    expected_marks={}
    for uid,s in source.items():
        target=[p for p in lineages[uid] if p['event']==1 and p['node']==s['dst']]
        if target and target[0]['ecn']==3:assert len(target)==1;expected_marks[uid]=(s,target[0])
    marks={m['packet_uid']:m for m in report['marked_packets']};assert len(marks)==len(report['marked_packets']) and set(marks)==set(expected_marks)
    for uid,m in marks.items():
        source_row,rx=expected_marks[uid];sid=source_row['stream'];seq=source_row['sequence'];end=seq+source_row['payload_bytes']
        assert (m['stream'],m['sequence'],m['end'],m['payload_bytes'],m['source_tx_ns'],m['np_rx_ns'])==(sid,seq,end,source_row['payload_bytes'],source_row['time_ns'],rx['time_ns'])
        tx=[p for p in lineages[uid] if p['event']==0];changes=[]
        assert tx[0]['ecn']!=3
        for x in tx[1:]:
            incoming=next(p for p in lineages[uid] if p['event']==1 and p['node']==x['node'])
            if x['ecn']==3 and incoming['ecn']!=3:changes.append((x,incoming))
        assert len(changes)==1;where,entered=changes[0];site=m['first_ce'];assert (site['node'],site['port'],site['time_ns'],site['previous_ecn'])==(where['node'],where['port'],where['time_ns'],entered['ecn'])
        deq,remaining=dequeues[uid,where['node'],where['port']];p=ports[where['node'],where['port']]
        assert site['remaining_by_stream_model_bytes']==remaining and site['remaining_data_model_bytes']==sum(remaining.values())
        assert site['captured_total_before_dequeue_bytes']==deq['egress_queue_bytes']>=sum(remaining.values())+deq['model_bytes']
        assert (site['kmin_bytes'],site['kmax_bytes'])==(p['ecn_kmin_bytes'],p['ecn_kmax_bytes']) and sum(remaining.values())>p['ecn_kmin_bytes']
        intersections={w['id']:(max(seq,w['begin']),min(end,w['end'])) for w in works.values() if w['stream']==sid and max(seq,w['begin'])<min(end,w['end'])}
        assert len(intersections)==len(m['work_intersections']) and sum(b-a for a,b in intersections.values())==source_row['payload_bytes']
        for w in m['work_intersections']:
            left,right=intersections[w['work']];assert (w['begin'],w['end'],w['bytes'])==(left,right,right-left)
            assert all(w[k]==works[w['work']][k] for k in ('ready_ns','published_ns','accepted_ns','acked_ns'))
    ref=t['dcqcn_reference'];config=ref['config'];generated=[g for g in ref['events'] if g['kind']=='cnp_generated'];received={g['packet_uid']:g for g in ref['events'] if g['kind']=='cnp_received'}
    notices={n['packet_uid']:n for n in report['notices']};assert len(notices)==len(report['notices'])==len(generated)==len(received)
    assert set(notices)=={g['packet_uid'] for g in generated}==set(received)
    npmark={(g['stream'],g['time_ns'],g['packet_uid']) for g in ref['events'] if g['kind']=='np_mark'}
    assert npmark=={(m['stream'],m['np_rx_ns'],uid) for uid,m in marks.items()}
    checked_hops=0
    for sid,st in streams.items():
        remaining=sorted((m for m in marks.values() if m['stream']==sid),key=lambda m:(m['np_rx_ns'],m['packet_uid']))
        last=None
        for g in [g for g in generated if g['stream']==sid]:
            uid=g['packet_uid'];n=notices[uid];rx=received[uid];now=g['time_ns'];assert remaining
            if remaining[0]['np_rx_ns']==now:
                expected=[remaining.pop(0)];kind='immediate';assert last is None or now-last>=config['np_interval_ns']
            else:
                assert last is not None and now-last==config['np_interval_ns'];kind='periodic';expected=[]
                while remaining and remaining[0]['np_rx_ns']<now:expected.append(remaining.pop(0))
                assert expected
            last=now;assert n['np_batch']==kind and n['np_members']==[m['packet_uid'] for m in expected]
            assert (n['stream'],n['generated_ns'],n['received_ns'])==(sid,now,rx['time_ns'])
            assert n['source_ack_prefix']==prefixes[uid]==rx['snd_una'] and n['source_published_bytes']==rx['published_bytes']
            assert n['all_marked_bytes_acked_at_receive']==all(m['end']<=prefixes[uid] for m in expected)
            for field,event in (('all_marked_works_acked_at_receive','acked_ns'),('all_marked_works_accepted_at_receive','accepted_ns')):
                assert n[field]==all(works[w['work']][event]<=rx['time_ns'] for m in expected for w in m['work_intersections'])
            assert n['active_published_works']==[w['id'] for w in t['work'] if w['stream']==sid and w['published_ns']<=rx['time_ns']<w['acked_ns']]
            assert n['members_with_other_streams_queued']==sum(any(k!=str(sid) and b for k,b in m['first_ce']['remaining_by_stream_model_bytes'].items()) for m in expected)
            trace=lineages[uid];tx=[p for p in trace if p['event']==0];assert len(tx)==len(n['hops'])
            for i,(actual,h) in enumerate(zip(tx,n['hops'])):
                p=ports[actual['node'],actual['port']];dest=next(v for v in trace if v['event']==1 and v['node']==p['peer'])
                entry=now if i==0 else next(v['time_ns'] for v in trace if v['event']==1 and v['node']==actual['node'])
                assert (h['node'],h['port'],h['peer'],h['enter_ns'],h['tx_ns'],h['rx_ns'])==(actual['node'],actual['port'],p['peer'],entry,actual['time_ns'],dest['time_ns'])
                service=int(config['cnp_model_bytes']*8*10**9/p['rate_bps']+.5)
                assert h['service_ns']==service and h['wait_ns']==actual['time_ns']-entry>=0
                assert dest['time_ns']-actual['time_ns']==service+p['delay_ns']
                assert actual['ip_protocol']==dest['ip_protocol']==255 and actual['model_bytes']==dest['model_bytes']==config['cnp_model_bytes']
                checked_hops+=1
            assert (tx[0]['node'],n['hops'][-1]['peer'])==(st['dst'],st['src'])
            assert n['source_tx_ns']==tx[0]['time_ns'] and rx['time_ns']==n['hops'][-1]['rx_ns']
        assert not remaining
    validate_summary(inputs,case,r,report)
    count=rp_oracle(r,report)
    routes=check_routes(load(inputs/'topology/topology.json'),load(inputs/'inputs/observed'/(case+'-protocol.json')),load(inputs/'planned'/case/'work.json'),r)
    return dict(passed=True,marked_packets=len(marks),notices=len(notices),cnp_physical_hops=checked_hops,rp_state_records=count,
        original_np_partition=True,queue_data_inventory_and_first_ce=True,actual_ack_prefixes=True,rational_alpha_and_integer_rate_oracle=True,routes=routes)


def validate_summary(inputs,case,r,report):
    t=r['persistent_transport'];marks=report['marked_packets'];notices=report['notices']
    expected=dict(marked_packets=len(marks),notices=len(notices),immediate=sum(n['np_batch']=='immediate' for n in notices),
        periodic=sum(n['np_batch']=='periodic' for n in notices),all_marked_bytes_acked=sum(n['all_marked_bytes_acked_at_receive'] for n in notices),
        all_marked_works_acked=sum(n['all_marked_works_acked_at_receive'] for n in notices),
        source_no_outstanding_bytes=sum(n['source_ack_prefix']==n['source_published_bytes'] for n in notices),
        marked_packets_with_other_streams_queued=sum(any(k!=str(m['stream']) and b for k,b in m['first_ce']['remaining_by_stream_model_bytes'].items()) for m in marks))
    assert report['statistics']==expected
    plan=load(inputs/'planned'/case/'work.json');mapping=load(inputs/'planned'/case/'mapping.json')
    node_index={(n['graph_id'],n['rank'],n['node_id']):n for n in mapping['nodes']};labels={}
    idx=load(inputs/'inputs/observed/index.json')
    for filename in idx['waves']:
        wave=load(inputs/'inputs/observed'/filename)
        for rank,nodes in wave['rank_nodes'].items():
            for n in nodes:
                if n['kind']=='SEND':
                    refs=node_index[wave['graph_id'],int(rank),n['id']]['completion'];assert len(refs)==1
                    labels[refs[0]['work']]=(wave['graph_id'],n['name'])
    for m in marks:
        for w in m['work_intersections']:assert (w['graph_id'],w['name'])==labels[w['work']]
    ops={w['id']:w for w in plan['work']};times={w['id']:w for w in [*t['work'],*r['local_work']]}
    def at(d):return times[d['work']][dict(acked='acked_ns',accepted='accepted_ns',completed='completed_ns')[d['event']]]
    done=mapping['waves'][-1]['completion'];assert report['application_completion_ns']==max(map(at,done))
    visited=set();frontier=set();todo=list(done)
    while todo:
        d=todo.pop();key=d['work'],d['event']
        if key in visited:continue
        visited.add(key);op=ops[d['work']];row=times[d['work']];parents=op['after'];ready=max(map(at,parents),default=0)
        assert ready==row['ready_ns']
        if op['kind']=='write':frontier.add(key);todo.extend(p for p in parents if at(p)==ready)
        else:
            if row['start_ns']==ready:todo.extend(p for p in parents if at(p)==ready)
            todo.extend(dict(work=w['id'],event='completed') for w in r['local_work'] if w['rank']==row['rank'] and w['id']!=row['id'] and w['duration_ns']>0 and w['completed_ns']==row['start_ns'])
    assert {(w['work'],w['event']) for w in report['final_frontier']}==frontier
    cols=t['source_rate_capture']['columns'];source=[dict(zip(cols,row)) for row in t['source_rate_capture']['samples'] if row[cols.index('event')]==0]
    assert set(report['critical_works'])=={'99','108','132'}
    for wid,shown in report['critical_works'].items():
        w=times[int(wid)];assert shown['name']==labels[int(wid)][1]
        first=next(s for s in source if s['stream']==w['stream'] and w['begin']<=s['sequence']<w['end'])
        prior=[n for n in notices if n['stream']==w['stream'] and n['received_ns']<=w['ready_ns']]
        assert (shown['work'],shown['stream'],shown['ready_ns'],shown['first_tx_ns'],shown['first_rate_bps'])==(w['id'],w['stream'],w['ready_ns'],first['time_ns'],first['rate_bps'])
        assert shown['prior_cnp_uids']==[n['packet_uid'] for n in prior] and shown['last_prior_cnp']==(prior[-1] if prior else None)
        assert shown['in_final_declared_frontier']==any(i==w['id'] for i,e in frontier)


def main():
    p=argparse.ArgumentParser()
    for k in ('inputs','analysis','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();assert all(sha(a.inputs/n)==h for n,h in load(a.inputs/'manifest.json')['files'].items())
    data=load(a.analysis);reports={}
    for name,report in data['cases'].items():reports[name]=check(a.inputs,name,report)
    rejected=[]
    for mode in ('notification_member','first_ce','queue_bytes','work_range','rp_rate','ack_prefix','physical_wait','summary_count','critical_rate','critical_frontier','work_label'):
        bad=deepcopy(data['cases']['live-instant'])
        if mode=='notification_member':bad['notices'][0]['np_members']=[]
        elif mode=='first_ce':bad['marked_packets'][0]['first_ce']['port']+=1
        elif mode=='queue_bytes':bad['marked_packets'][0]['first_ce']['remaining_data_model_bytes']+=1
        elif mode=='work_range':bad['marked_packets'][0]['work_intersections'][0]['bytes']+=1
        elif mode=='rp_rate':bad['notices'][0]['rate_after_bps']+=1
        elif mode=='ack_prefix':bad['notices'][0]['source_ack_prefix']+=1
        elif mode=='physical_wait':bad['notices'][0]['hops'][0]['wait_ns']+=1
        elif mode=='summary_count':bad['statistics']['notices']+=1
        elif mode=='critical_rate':bad['critical_works']['108']['first_rate_bps']+=1
        elif mode=='critical_frontier':bad['critical_works']['108']['in_final_declared_frontier']=not bad['critical_works']['108']['in_final_declared_frontier']
        else:bad['marked_packets'][0]['work_intersections'][0]['name']='wrong-stage'
        try:check(a.inputs,'live-instant',bad)
        except (AssertionError,KeyError,IndexError,ValueError):rejected.append(mode)
        else:raise AssertionError('mutation accepted '+mode)
    save(a.output,dict(passed=True,cases=reports,mutations_rejected=rejected,network_executions=0,
        limitations='paper-defined functional recurrence and existing simulation records, not hardware calibration or safe CNP-discard policy'))


if __name__=='__main__':main()
