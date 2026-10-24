"""Independent chronological-rate and physical-intersection oracle."""
import argparse
from copy import deepcopy
from pathlib import Path
from experiments.nic_wakeup_effect.stage import load,save
from experiments.reordering_baseline.verify_train import check as check_train


def check(r,tr,reported):
    check_train(r,tr);t=r['persistent_transport'];cap=r['packet_capture'];cols=cap['columns'];rows=cap['events']
    def field(row,k):return row[cols.index(k)]
    # Reconstruct source transmissions independently from transport source rows.
    sc=t['source_packet_columns'];source=[dict(zip(sc,x)) for x in t['source_packets']]
    work=next(w for w in t['work'] if w['id']==99)
    sent=[x for x in source if x['stream']==work['stream'] and work['begin']<=x['sequence']<work['end']]
    rc=t['source_rate_capture']['columns'];rate={}
    for row in t['source_rate_capture']['samples']:
        x=dict(zip(rc,row))
        if x['event']==0:rate[x['packet_uid']]=x
    assert len(reported['gaps'])==len(sent)-1
    port=tr['source_port'];alltotals={k:0 for k in ('eligible_ns','busy_while_eligible_ns','idle_while_eligible_ns','retrospective_overcount_ns')}
    for left,right,g in zip(sent,sent[1:],reported['gaps']):
        assert (g['previous_uid'],g['packet_uid'])==(left['packet_uid'],right['packet_uid'])
        begin,end=left['time_ns'],right['time_ns'];last=rate[left['packet_uid']];nbytes=last['model_bytes']
        updates=[x for x in t['dcqcn_reference']['events'] if x['stream']==work['stream'] and begin<x['time_ns']<=end and x['kind'] in ('cnp_received','rp_timer','rp_bytes')]
        assert len(g['pieces'])==len(updates)+1
        rates=[last['rate_bps'],*[e['rate_bps'] for e in updates]]
        bounds=[begin,*[e['time_ns'] for e in updates],end];totals={k:0 for k in alltotals if k!='retrospective_overcount_ns'}
        for i,piece in enumerate(g['pieces']):
            lo,hi=bounds[i:i+2];due=begin+int(nbytes*8*10**9/rates[i]+0.5)
            assert (piece['start_ns'],piece['end_ns'],piece['rate_bps'],piece['deadline_ns'])==(lo,hi,rates[i],due)
            assert piece['ended_by']==(updates[i]['kind'] if i<len(updates) else 'end')
            active=max(0,hi-max(lo,due));busy=0
            for e in rows:
                if field(e,'event')!=0 or (field(e,'node'),field(e,'port'))!=(port['node'],port['port']):continue
                x=field(e,'time_ns');y=x+int(field(e,'model_bytes')*8*10**9/port['rate_bps']+0.5)
                busy+=max(0,min(hi,y)-max(lo,due,x))
            expected=dict(eligible_ns=active,busy_while_eligible_ns=busy,idle_while_eligible_ns=active-busy)
            assert all(piece[k]==v>=0 for k,v in expected.items())
            for k,v in expected.items():totals[k]+=v
        oldgap=next(x for x in tr['gaps'] if x['packet_uid']==right['packet_uid'])
        totals['retrospective_overcount_ns']=oldgap['after_eligibility_ns']-totals['eligible_ns']
        assert all(g[k]==v for k,v in totals.items())
        for k,v in totals.items():alltotals[k]+=v
    assert all(reported[k]==v for k,v in alltotals.items())
    assert reported['work']==work['id'] and reported['stream']==work['stream']


def main():
    p=argparse.ArgumentParser()
    for k in ('stage','analysis','eligibility','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();analysis=load(a.analysis);data=load(a.eligibility);raw={}
    for name,tr in analysis['work99_source_trains'].items():
        v,c=name.split('-',1);r=load((a.stage/'inputs/baseline' if v=='old' else a.stage/'network')/c/'result.json')
        check(r,tr,data['cases'][name]);raw[name]=r
    rejected=[];name='fixed-live-instant';tr=analysis['work99_source_trains'][name]
    for kind in ('past_rate','eligibility','physical_busy'):
        bad=deepcopy(data['cases'][name]);gap=next(g for g in bad['gaps'] if g['retrospective_overcount_ns'])
        piece=gap['pieces'][0]
        if kind=='past_rate':piece['rate_bps']+=1
        elif kind=='eligibility':piece['eligible_ns']+=1
        else:piece['busy_while_eligible_ns']+=1
        try:check(raw[name],tr,bad)
        except AssertionError:rejected.append(kind)
        else:raise AssertionError('accepted '+kind)
    save(a.output,dict(passed=True,cases=4,chronological_rate_and_physical_intersections=True,
        continuous_source_bytes_no_retry_no_pfc=True,mutations_rejected=rejected,network_executions=0))


if __name__=='__main__':main()
