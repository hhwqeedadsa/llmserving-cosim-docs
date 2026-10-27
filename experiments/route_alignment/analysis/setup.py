"""Generate topology through the public CLI, validate and freeze predictions."""
import argparse
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.scaleout_streams import validate
from experiments.route_alignment.analysis.common import NETWORKS, conditions, load, save, sha
from experiments.route_alignment.analysis.prepare import predictions
from experiments.route_alignment.tests.check import checks


def execute(repo,stage):
    checks()
    for name in NETWORKS:
        subprocess.run([sys.executable,'-B','-m','moe_ub_cosim','scaleout','topology',
            '--config',str(stage/'observed'/(name+'-config.json')),
            '--output',str(stage/'observed'/(name+'-topology'))],check=True)
        t=load(stage/'observed'/(name+'-topology/topology.json'))
        assert sum(n['kind']=='gpu' for n in t['nodes'])==8
        assert sum(n['kind']=='core' for n in t['nodes'])==2
    for network,alignment,phase in conditions():
        validate(load(stage/'observed'/(network+'-topology/topology.json')),
            load(stage/'observed'/(network+'-protocol.json')),
            load(stage/'observed'/f'{alignment}-{phase}/work.json'))
    predictions(stage/'observed',stage/'predictions')
    files={str(p.relative_to(stage)):sha(p) for root in ('observed','predictions','runtime')
           for p in sorted((stage/root).rglob('*')) if p.is_file()}
    sources={str(p.relative_to(repo)):sha(p) for p in sorted((repo/'experiments/route_alignment').rglob('*'))
             if p.is_file() and '__pycache__' not in p.parts}
    save(stage/'frozen.json',dict(files=files,analysis_sources=sources,
         predictions_frozen_before_network=True,new_network_runs=0))
    save(stage/'setup-checks.json',dict(passed=True,validated_conditions=32,
         alignment_variants=16,hand_estimates=1,new_network_runs=0))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();execute(a.repository.resolve(),a.stage.resolve())
