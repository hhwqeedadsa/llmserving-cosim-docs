"""Frozen public legacy mappings -> two preregistered modern identity checks."""
import argparse
from pathlib import Path
import re
from experiments.reordering_baseline.checkpoint import admit
from experiments.provider_binding.stage import load,save,sha

HASH='90761e2270373a4858a69fa402c85c80e9cb458e5be91f17d09f9f12e0901c2c'


def prepare(archive,out):
    assert sha(archive)==HASH and not out.exists();data,manifest=admit(archive)
    assert manifest['experiment']=='feedback_ancestry';out.mkdir()
    paths={f'g0.w{s}.json':f'evidence/inputs/inputs/observed/g0.w{s}.json' for s in (0,1)}
    paths.update({'work.json':'evidence/inputs/planned/healthy/work.json',
        'mapping.json':'evidence/inputs/planned/healthy/mapping.json','feedback-summary.json':'evidence/summary.json'})
    for name,src in paths.items():(out/name).write_bytes(data[src])
    save(out/'identity.json',dict(archive_sha256=HASH,all_members_verified=len(data),
        files={name:sha(out/name) for name in paths},scope='public original graph/work and saved analysis, no truth or new network'))


def compare(stage,analysis):
    cfg=load(stage/'inputs/input.json');out=stage/'legacy';mapping=load(out/'mapping.json')
    work=load(out/'work.json');streams={s['id']:s for s in work['streams']};works={w['id']:w for w in work['work']}
    labels={}
    for step in (0,1):
        wave=load(out/f'g0.w{step}.json');assert wave['decode_step']==step
        for route in wave['routes']:
            call=cfg['calls'][2*step+route['layer']]
            for t in route['tokens']:
                assert t['rank']%2==0 and t['experts']==call['tokens'][t['rank']//2][0]
                assert t['source']['source_entry']==step+1 and t['source']['source_layer_id']==3+route['layer']
        lookup={(r['rank'],r['node_id']):r for r in mapping['nodes'] if r['graph_id']==wave['graph_id']}
        for rank,nodes in wave['rank_nodes'].items():
            for n in nodes:
                if n['kind']!='SEND':continue
                refs=lookup[int(rank),n['id']]['completion'];assert len(refs)==1 and refs[0]['event']=='acked'
                wid=refs[0]['work'];w=works[wid];stream=streams[w['stream']];m=n['message']
                assert [stream['src'],stream['dst']]==[m['src'],m['dst']]
                assert w['bytes']==m['bytes'] and int(rank)==m['src']
                match=re.fullmatch(r'L([01])\.(dispatch|combine)\.(\d+)-(\d+)\.send',n['name']);assert match
                layer,phase,src,dst=match.groups();src=int(src);dst=int(dst)
                assert [2*src,2*dst]==[m['src'],m['dst']] and int(layer)==m['layer'] and phase==m['phase']
                ci=2*step+int(layer)
                oid=(f'm{ci}.dispatch.data.s{src}.t0.d{dst}' if phase=='dispatch' else f'm{ci}.combine.data.s{src}.d{dst}.t0')
                row=next(r for r in analysis['rows'] if r['operation']==oid)
                labels[wid]=dict(work=wid,legacy_stream=w['stream'],physical_ranks=[m['src'],m['dst']],
                    original_ranks=[src,dst],modern_operation=oid,modern_qp=row['qp'],context_index=row['context_index'])
    summary=load(out/'feedback-summary.json')['cases']['live-instant']['critical_works'];pairs=[]
    for origin,target in ((90,108),(80,132)):
        notice=summary[str(target)]['last_prior_notice'];assert [r['work'] for r in notice['work_origins']]==[origin]
        a,b=labels[origin],labels[target];assert a['legacy_stream']==b['legacy_stream']
        pairs.append(dict(origin=a,target=b,old_same_qp=True,
            modern_same_qp_under_declared_shared_devcomm=a['modern_qp']==b['modern_qp'],
            transferred_rates_or_cnp=False))
    return dict(pairs=pairs,matched_legacy_writes=len(labels),raw_route_rows_matched=16,
        scope='conditional source identity comparison; modern dedup/layout/control differs; no latency or CNP transfer')


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','compare'])
    for k in ('archive','output','stage','analysis'):p.add_argument('--'+k,type=Path)
    a=p.parse_args()
    if a.action=='prepare':prepare(a.archive,a.output)
    else:
        assert not a.output.exists();save(a.output,compare(a.stage,load(a.analysis)))


if __name__=='__main__':main()
