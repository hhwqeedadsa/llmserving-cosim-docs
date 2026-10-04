"""Small EP structural extremes through Python, Chakra and the real UB backend."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from moe_ub_cosim.generate import build_export_full_model
from moe_ub_cosim.plan import RunIdentities


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2];out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    fixture=root/'fixtures/m10_full_model'
    source=json.loads((fixture/'service.json').read_text())['groups'][0]
    template=json.loads((fixture/'layers.json').read_text())['groups'][0]['layers'][0]
    commands=[];cases={}
    for label,kinds,shared in [('61-blocks',['dense']*3+['moe']*58,1),('dense-between',['moe','dense','moe'],0)]:
        model=deepcopy(source['model_spec']);profile=deepcopy(source['full_model_profile']);configs=[]
        blocks=[];costs=[]
        for lid,kind in enumerate(kinds):
            index=0 if kind=='dense' else 1;row=deepcopy(model['layers'][index]);row['layer_id']=lid;blocks.append(row)
            cost=deepcopy(profile['layers'][index]);cost['layer_id']=lid;costs.append(cost)
            if kind=='moe':
                row.update(shared_experts=shared,shared_intermediate_size=256 if shared else 0)
                cost['per_token_ns']['shared_expert']=500 if shared else 0
                cost['per_token_ns']['shared_routed_add']=29 if shared else 0
                cfg=deepcopy(template);cfg['layer_id']=lid;cfg['tokens'][0]['layer_id']=lid;configs.append(cfg)
        model['layers']=blocks;profile['layers']=costs;profile['model_structure']=deepcopy(model)
        for row in profile['model_structure']['layers']:row.pop('expert_placement',None)
        plan,directory=build_export_full_model(configs,RunIdentities(),[[] for _ in configs],model,profile,out/f'{label}-plan')
        argv=[str(v) for v in (args.build_dir.resolve()/'output/cosim/ns3.44-cosim-layer',directory,
            root/'fixtures/m1_compute/system.json',root/'fixtures/m3_shared_bottleneck/network','-',out/label)]
        start=datetime.now(timezone.utc).isoformat();p=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=120)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(p,stream))
        commands.append(dict(label=label,argv=argv,cwd=str(root),started_utc=start,ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=p.returncode))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n');assert p.returncode==0,(label,p.stderr)
        result=json.loads((out/label/'result.json').read_text())
        assert result['later_event_executed'] and len(result['graph_completions'])==3
        assert all(r['finished'] and r['pending_events']==r['pending_tasks']==r['send_slots']==0 for r in result['rank_resources'])
        events={(e['rank_id'],e['node_id']):e for e in result['node_execution']}
        assert len(events)==sum(len(g['nodes']) for g in plan['rank_graphs'])
        assert len(result['transfers'])==sum('ub_task_id' in t for t in plan['transfers'])
        assert all(t['submitted_tasks']==t['send_callbacks']==t['receive_callbacks']==1 for t in result['transfers'])
        for graph in plan['rank_graphs']:
            for node in graph['nodes']:
                e=events[graph['rank_id'],node['node_id']]
                assert all(e['start_ns']>=events[graph['rank_id'],d]['end_ns'] for d in node['deps'])
                if node['kind']=='COMP':assert e['end_ns']-e['start_ns']==node['duration_ns']
            assert len([s for s in graph['model_stages'] if s['stage']=='mla_attention'])==len(kinds)
            if not shared:assert all(s['node_id'] is None for s in graph['model_stages'] if s['stage'] in ('shared_expert','shared_routed_add'))
        cases[label]=dict(blocks=len(kinds),moe_blocks=kinds.count('moe'),nodes=len(events),writes=len(result['transfers']),
            all_transfers=len(plan['transfers']),finished_ns=max(e['end_ns'] for e in events.values()),EP=3,experts=6,hidden_size=1024)
    (out/'summary.json').write_text(json.dumps(dict(passed=True,cases=cases,target_scale_executed=False),indent=2)+'\n')
    print('PASS: 61 small synthetic blocks and interspersed dense/absent-shared structure through real UB')


if __name__=='__main__':main()
