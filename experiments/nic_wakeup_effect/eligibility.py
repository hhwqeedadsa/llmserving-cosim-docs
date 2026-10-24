"""Piecewise eligibility uses rates only after their actual update events."""
import argparse
from collections import Counter
from pathlib import Path
from experiments.nic_wakeup_effect.stage import load,save


KINDS=('cnp_received','rp_timer','rp_bytes')


def summarize(result, train):
    t=result['persistent_transport'];rate=t['source_rate_capture']
    samples={x['packet_uid']:x for x in (dict(zip(rate['columns'],r)) for r in rate['samples']) if x['event']==0}
    cap=result['packet_capture'];events=[dict(zip(cap['columns'],r)) for r in cap['events']]
    port=train['source_port'];physical=[(e['time_ns'],e['time_ns']+round(e['model_bytes']*8*10**9/port['rate_bps']))
        for e in events if e['event']==0 and (e['node'],e['port'])==(port['node'],port['port'])]
    gaps=[]
    for g in train['gaps']:
        before=samples[g['previous_uid']];after=samples[g['packet_uid']];start=before['time_ns'];stop=after['time_ns']
        changes=[e for e in t['dcqcn_reference']['events'] if e['stream']==train['stream'] and e['kind'] in KINDS and start<e['time_ns']<=stop]
        rate_bps=before['rate_bps'];cursor=start;pieces=[]
        for event in [*changes,dict(time_ns=stop,kind='end',rate_bps=rate_bps)]:
            end=event['time_ns'];due=start+round(before['model_bytes']*8*10**9/rate_bps)
            eligible_start=max(cursor,due);eligible=max(0,end-eligible_start)
            busy=sum(max(0,min(end,b)-max(eligible_start,a)) for a,b in physical) if eligible else 0
            assert 0<=busy<=eligible
            pieces.append(dict(start_ns=cursor,end_ns=end,rate_bps=rate_bps,deadline_ns=due,
                eligible_ns=eligible,busy_while_eligible_ns=busy,idle_while_eligible_ns=eligible-busy,ended_by=event['kind']))
            cursor=end;rate_bps=event['rate_bps']
        totals={k:sum(p[k] for p in pieces) for k in ('eligible_ns','busy_while_eligible_ns','idle_while_eligible_ns')}
        gaps.append(dict(previous_uid=g['previous_uid'],packet_uid=g['packet_uid'],pieces=pieces,**totals,
            retrospective_overcount_ns=g['after_eligibility_ns']-totals['eligible_ns']))
    totals={k:sum(g[k] for g in gaps) for k in ('eligible_ns','busy_while_eligible_ns','idle_while_eligible_ns','retrospective_overcount_ns')}
    return dict(work=train['work'],stream=train['stream'],gaps=gaps,**totals,
        scope='continuous pending bytes, no PFC/window binding and no retry verified separately; no rate is applied before its actual update')


def main():
    p=argparse.ArgumentParser()
    for k in ('stage','analysis','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();analysis=load(a.analysis);cases={}
    for name,tr in analysis['work99_source_trains'].items():
        v,c=name.split('-',1);r=load((a.stage/'inputs/baseline' if v=='old' else a.stage/'network')/c/'result.json')
        cases[name]=summarize(r,tr)
    save(a.output,dict(schema_version='nic-wakeup-piecewise-eligibility/1',cases=cases,
        correction='final sampled next_avail can be moved into the past by an RP rate update; that past interval was not actually eligible',network_executions=0))


if __name__=='__main__':main()
