"""Measured worker stages. Run via moe_ub_cosim.host_probe, never unbounded."""
import argparse
import json
from pathlib import Path
import time

from moe_ub_cosim.host_preflight import count_target,ref,save,target_inputs


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('count','import','layer'))
    p.add_argument('--input',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--count-dir',type=Path);p.add_argument('--request-count',type=int,default=1024)
    a=p.parse_args();config=json.loads(a.input.read_text())
    if a.action=='count':
        r=count_target(config,a.output_dir);print('Full workload record totals:',r['total'],flush=True);return
    a.output_dir.mkdir(parents=True,exist_ok=False);values,_=target_inputs(config)
    def phase(name):print('PHASE',name,flush=True)
    if a.action=='import':
        from moe_ub_cosim.route_evidence import select_routes
        if not 1<=a.request_count<=1024:raise ValueError('Invalid import probe count')
        m=values['manifest'];m['files']=m['files'][:a.request_count];save(a.output_dir/'source-manifest.json',m)
        model=dict(name=m['model']['name'],hidden_size=m['model']['hidden_dim'],layers=m['layers'])
        phase('existing-select-routes')
        normalized,records,evidence=select_routes(ref(a.output_dir/'source-manifest.json'),values['requests'][:a.request_count],[model],list(range(3,61)))
        assert len(records)==a.request_count*64*58 and len(normalized)==a.request_count and not evidence['infer_eos_from_file_end']
        save(a.output_dir/'result.json',dict(requests=len(normalized),selected_records=len(records),target_scale_executed=False))
        phase('import-complete-retained-objects');time.sleep(.2)
    else:
        from moe_ub_cosim.plan import build_plan
        from moe_ub_cosim.resources import actual_usage
        from moe_ub_cosim.generate import export_plan
        if a.count_dir is None:raise ValueError('Layer probe needs checked count directory')
        c=json.loads((a.count_dir/'first-layer.json').read_text());phase('target-first-layer-build')
        plan=build_plan(c);plan['input_provenance']=dict(route_records=json.loads((a.count_dir/'first-layer-provenance.json').read_text()))
        plan['route_source']='published_model_trace'
        expected=json.loads((a.count_dir/'layer-counts.json').read_text())[0];actual=actual_usage(plan)
        assert actual=={k:expected[k] for k in actual}
        save(a.output_dir/'built.json',dict(actual_usage=actual,target_scale_executed=False))
        phase('target-first-layer-export');export_plan(plan,a.output_dir/'graph')
        save(a.output_dir/'result.json',dict(actual_usage=actual,dependency_edges=sum(len(n['deps']) for g in plan['rank_graphs'] for n in g['nodes']),
            plan_bytes=(a.output_dir/'graph/plan.json').stat().st_size,target_scale_executed=False,
            scope='one actual target MoE layer, first Decode entry, offline only; excludes full-model composition'))
        phase('layer-export-complete')


if __name__=='__main__':main()
