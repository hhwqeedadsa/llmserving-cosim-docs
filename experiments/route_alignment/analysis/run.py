"""Run each frozen condition once through public CLI; stop globally on failure."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.host_probe import probe, disk_usage
from experiments.route_alignment.analysis.common import (conditions, case_name, load, save, sha)
from experiments.route_alignment.analysis.evaluate import audit


def execute(repo, stage):
    frozen=load(stage/'frozen.json')
    for name,digest in frozen['files'].items():assert sha(stage/name)==digest,name
    for name,digest in frozen['analysis_sources'].items():assert sha(repo/name)==digest,name
    observed=stage/'observed';truth=stage/'truth';truth.mkdir();checks=stage/'checks';checks.mkdir()
    identity=load(observed/'runtime-identity.json')
    assert sha(stage/'runtime/stream-probe')==identity['binary_sha256']
    os.environ['LD_LIBRARY_PATH']=str(stage/'runtime/lib')
    for name,digest in identity['library_hashes'].items():assert sha(stage/'runtime/lib'/name)==digest
    results=[]
    for network,alignment,phase in conditions():
        name=case_name(network,alignment,phase);entry=dict(name=name,status='starting')
        results.append(entry);save(stage/'execution.json',dict(conditions=results,complete=False))
        used=sum(load(p)['wall_seconds'] for p in stage.rglob('probe.json'))
        assert used+60<1200 and max(disk_usage(stage).values())+66*2**20<2**30
        command=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-stream-probe',
            '--topology',str(observed/(network+'-topology')),'--protocol',str(observed/(network+'-protocol.json')),
            '--config',str(observed/f'{alignment}-{phase}/work.json'),'--backend',str(stage/'runtime/stream-probe'),
            '--output',str(truth/name)]
        monitor=probe(command,stage/'monitors'/name,rss_bytes=2**30,address_bytes=2*2**30,
            artifact_bytes=2**30,wall_seconds=30,artifact_roots=[stage],min_free_bytes=8*2**30)
        entry.update(status=monitor['status']);save(stage/'execution.json',dict(conditions=results,complete=False))
        assert monitor['status']=='completed' and not monitor['remaining_live_group_pids'],name
        r=load(truth/name/'result.json')
        libs=[Path(p) for p in r['loaded_libraries'] if Path(p).name.startswith('libns3')]
        assert len(libs)==8 and {p.name:sha(p) for p in libs}==identity['library_hashes']
        assert all(p.parent==stage/'runtime/lib' for p in libs)
        metrics=audit(load(observed/f'{alignment}-{phase}/work.json'),load(observed/f'{alignment}-{phase}/meta.json'),
            load(observed/(network+'-topology/topology.json')),load(observed/(network+'-protocol.json')),r)
        save(checks/(name+'.json'),dict(passed=True,metrics=metrics,result_sha256=sha(truth/name/'result.json')))
        entry.update(status='validated',application_ns=metrics['application_ns'])
        save(stage/'execution.json',dict(conditions=results,complete=False))
        print(name,metrics['application_ns'],metrics['cnp_packets'],flush=True)
    save(stage/'execution.json',dict(conditions=results,complete=True,network_runs=len(results)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();execute(a.repository.resolve(),a.stage.resolve())
