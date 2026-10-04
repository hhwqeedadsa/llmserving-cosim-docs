"""Bounded old/new build-export comparison on four blocks of the same bundle.

Always use host_probe. This comparison does not replace the 61-block retry.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import time
import types

from moe_ub_cosim import full_model, generate
from moe_ub_cosim.full_round_probe import Events, instrument
from moe_ub_cosim.plan import RunIdentities, require
from moe_ub_cosim.resources import actual_usage
from moe_ub_cosim.route_evidence import read_json


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for piece in iter(lambda:stream.read(1024*1024),b''):h.update(piece)
    return h.hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('version',choices=('baseline','closed'))
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[1]
    evidence=json.loads(Path('fixtures/m10_full_round_preflight/result.json').read_text())
    _,prepared=read_json(evidence['preparation']['prepared']);_,bundle=read_json(prepared['bundle'])
    baseline=json.loads(Path('fixtures/m10_full_model_export/baseline.json').read_text())
    require(sha(root/'python/moe_ub_cosim/full_model.py')==baseline['source_sha256']['python/moe_ub_cosim/full_model.py'],'Full-model builder changed')
    model=deepcopy(bundle['model']);profile=deepcopy(bundle['profile'])
    model['layers']=model['layers'][:4];profile['layers']=profile['layers'][:4]
    profile['model_structure']=full_model.model_structure(model)
    configs=bundle['configs'][:1];provenance=bundle['provenance'][:1]
    if a.version=='baseline':
        source=subprocess.check_output(['git','show',baseline['baseline_commit']+':python/moe_ub_cosim/generate.py'])
        require(hashlib.sha256(source).hexdigest()==baseline['source_sha256']['python/moe_ub_cosim/generate.py'],'Old exporter changed')
        module=types.ModuleType('moe_ub_cosim._old_generate');module.__package__='moe_ub_cosim'
        module.__file__=str(root/'python/moe_ub_cosim/generate.py')
        exec(compile(source,'baseline-generate.py','exec'),module.__dict__)
    events=Events(a.output_dir/'phases.jsonl')
    try:
        with instrument(events):
            start=time.monotonic();events('build_export_begin')
            if a.version=='baseline':
                plan=full_model.build_full_model(configs,RunIdentities(),provenance,model,profile)
                plan['input_provenance']=deepcopy(bundle['source_evidence'])
                for item in [plan,*plan['layers']]:
                    item['route_source']='published_model_trace'
                    item['compute_source']='full_model_stage_reference_with_context_cost'
                events('standalone_export_begin')
                directory=module.export_plan(plan,a.output_dir/'graph')
            else:
                plan,directory=generate.build_export_full_model(configs,RunIdentities(),provenance,model,profile,a.output_dir/'graph',source_evidence=bundle['source_evidence'])
            seconds=time.monotonic()-start;events('build_export_end')
        files=[directory/'plan.json',directory/'generation.json',directory/'graph-resources.json',*sorted(directory.glob('layer.*.et'))]
        result=dict(version=a.version,build_export_seconds=seconds,usage=actual_usage(plan),
            active_exporter_sha256=hashlib.sha256(source).hexdigest() if a.version=='baseline' else sha(Path('python/moe_ub_cosim/generate.py')),
            executed_layer_ids=plan['executed_layer_ids'],files={p.name:dict(bytes=p.stat().st_size,sha256=sha(p)) for p in files},
            prepared=evidence['preparation']['prepared'],bundle=prepared['bundle'],
            source_hashes={str(p):sha(p) for p in (Path('python/moe_ub_cosim/generate.py'),Path('python/moe_ub_cosim/full_model.py'))},
            comparison_scope='four blocks, 128 ranks, 1024 requests; complete target unchanged',simulation_executed=False)
        (a.output_dir/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print('PASS: build/export finished',a.version,seconds,flush=True)
    finally:events.close()


if __name__=='__main__':main()
