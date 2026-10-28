"""Exercise the pinned public preparation and actual upstream scheduler lifecycle."""
import argparse
import hashlib
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys

from moe_ub_cosim.simai_frontend import load


def check(source_root,output):
    assert not output.exists();output.mkdir(parents=True)
    runtime=output/'runtime'
    command=[sys.executable,'-m','moe_ub_cosim','scaleout','simai-prepare',
             '--source-root',str(source_root),'--output',str(runtime)]
    done=subprocess.run(command,capture_output=True,timeout=20)
    (output/'prepare.stdout').write_bytes(done.stdout);(output/'prepare.stderr').write_bytes(done.stderr)
    assert done.returncode==0,done.stderr.decode()
    classes=load(runtime);assert load(runtime)['Request'] is classes['Request']
    logging.getLogger('vidur').setLevel(logging.ERROR)
    generator=classes['SyntheticRequestGeneratorConfig']()
    rc=classes['ReplicaConfig'](model_name='deepseek-671B',device='h100',tensor_parallel_size=1)
    metrics=classes['MetricsConfig'](output_dir=str(output/'metrics'),write_json_trace=False)
    cluster=classes['Cluster'](classes['ClusterConfig'](num_replicas=64,replica_config=rc),metrics,generator)
    assert rc.expert_model_parallel_size==rc.prefill_world_size==rc.decode_world_size==64
    scheduler=classes['OrcaReplicaScheduler'](rc,classes['OrcaSchedulerConfig'](batch_size_cap=2),generator,
        next(iter(cluster.replicas.values())),1,None)
    requests=[]
    for i in range(3):
        request=classes['Request'](0.0,105+i,3)
        # Explicit ready-KV bootstrap; no claim that prefill computation ran.
        request.on_batch_schedule(1.0);request.on_batch_end(1.0,105+i)
        scheduler.add_request(request);requests.append(request)
    batches=[]
    for step in range(4):
        selected=scheduler.on_schedule();assert len(selected)==1
        batch=selected[0];batches.append(batch.request_ids)
        assert batch.num_prefill_tokens==0 and batch.num_tokens==[1]*len(batch.requests)
        batch.on_schedule(1.0+(step+1)/1000)
        batch.on_batch_end(1.0+(step+1.5)/1000);scheduler.on_batch_end(batch)
    ids=[r.id for r in requests]
    assert batches==[ids[:2],ids[:2],ids[2:],ids[2:]]
    assert scheduler.is_empty() and scheduler.num_allocated_blocks==0 and all(r.completed for r in requests)
    assert not scheduler.on_schedule()
    refused=[]
    for name in ('changed-method','extra-module'):
        copy=output/name;shutil.copytree(runtime,copy)
        target=copy/('vidur/scheduler/replica_scheduler/orca_replica_scheduler.py' if name=='changed-method' else 'vidur/injected.py')
        if name=='changed-method':target.write_bytes(target.read_bytes()+b'\n')
        else:target.write_text('raise RuntimeError("must not execute")\n')
        try:load(copy)
        except ValueError as error:refused.append(dict(case=name,error=str(error)))
        else:raise AssertionError('tampered runtime accepted')
    class_files={name:dict(path=str(Path(sys.modules[classes[name].__module__].__file__).relative_to(runtime)),
                 sha256=hashlib.sha256(Path(sys.modules[classes[name].__module__].__file__).read_bytes()).hexdigest())
                 for name in ('Request','Batch','Replica','Cluster','OrcaReplicaScheduler')}
    assert 'sklearn' not in sys.modules and not any(n=='serving' or n.startswith('serving.') for n in sys.modules)
    result=dict(passed=True,command=command,exit_code=done.returncode,simai_revision=classes['receipt']['revision'],
                source_files=len(classes['receipt']['files']),patched_initializers=2,
                actual_class_files=class_files,clusters=1,ep=64,requests=3,batches=batches,
                completed_requests=3,allocated_blocks_final=0,refused=refused,
                network_executed=False,ready_kv_initial_state_declared=True,
                scope='actual SimAI upstream CPU scheduling lifecycle, not native network integration')
    (output/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();check(a.source_root.resolve(),a.output.resolve())
