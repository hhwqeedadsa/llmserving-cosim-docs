"""Same first target layer, old/new production constructors, through host_probe."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import types

from moe_ub_cosim.plan import build_plan, validate_config, require
from moe_ub_cosim.resources import actual_usage
from moe_ub_cosim.route_evidence import read_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('version',choices=('baseline','indexed'))
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=False)
    evidence=json.loads(Path('fixtures/m10_full_round_preflight/result.json').read_text())
    _,prepared=read_json(evidence['preparation']['prepared'])
    _,bundle=read_json(prepared['bundle']);config=bundle['configs'][0]
    old=json.loads(Path('fixtures/m10_cann_indexing/golden.json').read_text())
    if a.version=='baseline':
        source=subprocess.run(['git','show',old['baseline_commit']+':python/moe_ub_cosim/cann.py'],
            capture_output=True,check=True).stdout
        source_hash=hashlib.sha256(source).hexdigest()
        require(source_hash==old['baseline_cann_sha256'],'Baseline source differs')
        module=types.ModuleType('moe_ub_cosim._baseline_cann');module.__package__='moe_ub_cosim'
        exec(compile(source,'baseline-cann.py','exec'),module.__dict__)
        def build(c):
            validate_config(c)
            return module.build_plan(c)
    else:
        source_hash=hashlib.sha256(Path('python/moe_ub_cosim/cann.py').read_bytes()).hexdigest()
        build=build_plan
    print('PHASE build-with-production-validation',flush=True)
    start=time.monotonic();plan=build(config);seconds=time.monotonic()-start
    usage=actual_usage(plan)
    require(usage=={k:prepared['layer_counts'][0][k] for k in usage},'First layer counts differ')
    # Same provenance attachment as build_children, outside the timed build.
    plan['input_provenance']=dict(route_records=bundle['provenance'][0])
    print('PHASE whole-plan-digest',flush=True)
    digest=hashlib.sha256()
    for piece in json.JSONEncoder(separators=(',',':')).iterencode(plan):digest.update(piece.encode())
    result=dict(version=a.version,source_sha256=source_hash,build_seconds=seconds,
        plan_sha256=digest.hexdigest(),usage=usage,
        dependency_edges=sum(len(n['deps']) for g in plan['rank_graphs'] for n in g['nodes']),
        prepared=evidence['preparation']['prepared'],bundle=prepared['bundle'],
        production_validation_enabled=True,simulation_executed=False)
    (a.output_dir/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
