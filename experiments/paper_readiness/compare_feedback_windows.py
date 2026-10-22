"""Post-result comparison of the two declared windows; no independent holdout claim."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('short-task','long-task','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();out=a.output;assert not out.exists()
    short,long=a.short_task,a.long_task
    so=short/'input/observed';lo=long/'input/observed'
    assert load(so/'topology.json')==load(lo/'topology.json')
    assert load(so/'protocol.json')==load(lo/'protocol.json')
    se=load(short/'run/execution.json');le=load(long/'run/execution.json')
    assert se['backend_sha256']==le['backend_sha256']
    sm=load(short/'input/truth/manifest.json');lm=load(long/'input/truth/manifest.json')
    assert sm==lm
    sr=load(short/'analysis/summary.json');lr=load(long/'analysis/summary.json')
    comparisons=[];identities=[]
    for depth in (1,8):
        old_name=f'd{depth}-n50000-r55000-b48';new_name=f'd{depth}-r55000'
        sw=load(so/(old_name+'.json'));lw=load(lo/(new_name+'.json'))
        assert sw['streams']==lw['streams']
        assert {k:v for k,v in sw['dcqcn_reference'].items() if k!='max_events'}=={k:v for k,v in lw['dcqcn_reference'].items() if k!='max_events'}
        assert {k:v for k,v in sw['source_rate_capture'].items() if k!='max_events'}=={k:v for k,v in lw['source_rate_capture'].items() if k!='max_events'}
        old=next(r for r in sr['cases'] if r['name']==old_name)
        new=next(r for r in lr['cases'] if r['name']==new_name)
        assert sha(short/'run'/old_name/'result.json')==old['result_sha256']
        assert sha(long/'run'/new_name/'result.json')==new['result_sha256']
        incoming=[]
        for w in (sw,lw):
            streams={s['id']:s for s in w['streams']};outgoing=Counter();dest=Counter()
            for item in w['work']:
                s=streams[item['stream']];outgoing[s['src']]+=1;dest[s['dst']]+=1
            incoming.append(dict(work=len(w['work']),source_copies=[outgoing[n] for n in (0,2,4,6)],destination_copies=[dest[n] for n in (0,2,4,6)]))
        comparisons.append(dict(depth=depth,short_acked_ns=old['acked_ns'],long_acked_ns=new['final_acked_ns'],
            short_post_feedback_packets=sum(s['data_packets_after_first_cnp'] for c in load(short/'checked-final/verification.json')['cases'] if c['case']==old_name for s in c['stream_feedback_timing']),
            long_post_feedback_packets=new['packets_after_first_cnp'],short_shape=incoming[0],long_shape=incoming[1]))
        for root,paths in ((short,[so/(old_name+'.json'),short/'run'/old_name/'result.json']),
                           (long,[lo/(new_name+'.json'),long/'run'/new_name/'result.json'])):
            for path in paths:identities.append(dict(task=str(root),path=str(path.relative_to(root)),sha256=sha(path)))
    old_refs={(w['source_rank'],w['token'],w['destination_rank']) for w in load(short/'input/truth/provenance.json')['work']}
    new_refs={(w['source_rank'],w['token'],w['destination_rank']) for w in load(long/'input/truth/provenance.json')['work'] if w['token']<6}
    assert old_refs==new_refs
    short_delta=comparisons[1]['short_acked_ns']-comparisons[0]['short_acked_ns']
    long_delta=comparisons[1]['long_acked_ns']-comparisons[0]['long_acked_ns']
    out.mkdir()
    (out/'comparison.json').write_text(json.dumps(dict(verified_matching_network_and_controller=True,
        comparisons=comparisons,depth8_minus_depth1_short_ns=short_delta,depth8_minus_depth1_long_ns=long_delta,
        ranking_reversed=short_delta*long_delta<0,raw_identities=identities,backend_sha256=se['backend_sha256'],
        limitations=['post-result comparison, not independent holdout','prefix expansion changes total work and routed matrix, not a pure scalar-length intervention',
            'only source work counts and passive recording caps differ beyond selected workload','not a production tuning recommendation']),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(compared=True,short_delta_ns=short_delta,long_delta_ns=long_delta,ranking_reversed=short_delta*long_delta<0)))


if __name__=='__main__':main()
