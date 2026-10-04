"""Bounded old/new full-model storage comparison on the same first six blocks.

This smaller comparison is separate from the unchanged 61-block target retry.
Always run through host_probe. Requires the original local first-round bundle.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import time
import types

from moe_ub_cosim import full_model
from moe_ub_cosim.full_round_probe import Events
from moe_ub_cosim.plan import RunIdentities, require
from moe_ub_cosim.resources import actual_usage
from moe_ub_cosim.route_evidence import read_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('version',choices=('baseline','optimized'))
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=False)
    evidence=json.loads(Path('fixtures/m10_full_round_preflight/result.json').read_text())
    _,prepared=read_json(evidence['preparation']['prepared']);_,bundle=read_json(prepared['bundle'])
    model=deepcopy(bundle['model']);profile=deepcopy(bundle['profile'])
    model['layers']=model['layers'][:6];profile['layers']=profile['layers'][:6]
    profile['model_structure']=full_model.model_structure(model)
    configs=bundle['configs'][:3];provenance=bundle['provenance'][:3]
    golden=json.loads(Path('fixtures/m10_full_model_storage/golden.json').read_text())
    if a.version=='baseline':
        source=subprocess.check_output(['git','show',golden['baseline_commit']+':python/moe_ub_cosim/full_model.py'])
        sha=hashlib.sha256(source).hexdigest();require(sha==golden['baseline_source_sha256'],'Old source differs')
        module=types.ModuleType('moe_ub_cosim._old_full_model');module.__package__='moe_ub_cosim'
        exec(compile(source,'baseline-full-model.py','exec'),module.__dict__)
    else:
        module=full_model;sha=hashlib.sha256(Path('python/moe_ub_cosim/full_model.py').read_bytes()).hexdigest()
    events=Events(a.output_dir/'phases.jsonl');compose=module.compose_full_model;ordinal=0
    def composition(*args,**kwargs):
        nonlocal ordinal
        ordinal+=1;events('composition_begin',call=ordinal)
        result=compose(*args,**kwargs);events('composition_end',call=ordinal)
        return result
    module.compose_full_model=composition
    try:
        events('build_begin');start=time.monotonic()
        plan=module.build_full_model(configs,RunIdentities(),provenance,model,profile)
        seconds=time.monotonic()-start;events('build_end')
        digest=hashlib.sha256()
        for piece in json.JSONEncoder(separators=(',',':')).iterencode(plan):digest.update(piece.encode())
        result=dict(version=a.version,source_sha256=sha,build_seconds=seconds,plan_sha256=digest.hexdigest(),
            usage=actual_usage(plan),executed_layer_ids=plan['executed_layer_ids'],
            dependency_edges=sum(len(n['deps']) for g in plan['rank_graphs'] for n in g['nodes']),
            prepared=evidence['preparation']['prepared'],bundle=prepared['bundle'],
            production_validation_enabled=True,scope='six-block comparison only',simulation_executed=False)
        (a.output_dir/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        events('digest_end');print(json.dumps(result),flush=True)
    finally:
        module.compose_full_model=compose;events.close()


if __name__=='__main__':main()
