"""Public-CLI, bounded 256/512/1024 GPU three-tier Clos scale gates.

Each EP4 inference replica spans a local GPU pair in two different Pods.
This is a network/execution scale gate, not a fixed-work strong-scaling study.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'examples/scaleout_clos128'


def read(path):
    return json.loads(Path(path).read_text())


def save(path,value):
    path.write_text(json.dumps(value,indent=2)+'\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(directory,size,batch):
    """Prepare explicit versioned inputs, without importing platform internals."""
    directory.mkdir()
    topology=read(BASE/'clos-128.json')
    pods=min(8,size//64)
    assert pods>=2 and size%(pods*8)==0
    topology['deployment'].update(pods=pods,servers_per_segment=size//(pods*8))
    topology['fabric'].update(tors_per_segment_per_rail=4,aggs_per_pod_per_group=2,
                              cores_per_group=2,tor_uplinks=2,agg_uplinks=1)
    model=read(BASE/'qwen3-128.json')
    model['workload']['batch_per_rank']=batch
    # Pair the first and second cluster halves. Each half of an EP4 group is
    # a same-server GPU pair; the two pairs always belong to different Pods.
    half=size//2
    model['parallel']['groups']=[[r,r+1,r+half,r+half+1] for r in range(0,half,2)]
    protocol=read(BASE/'rdma.json')
    protocol['limits'].update(max_gpus=size,max_switches=64,max_links=2048,
        max_messages=1000000,wall_seconds=600,result_json_bytes=1073741824)
    for name,value in [('topology',topology),('model',model),('protocol',protocol)]:
        save(directory/f'{name}.json',value)
    return model,protocol


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-json',type=Path,required=True)
    parser.add_argument('--protoc',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--sizes',type=int,nargs='+',choices=[128,256,512,1024],default=[256,512,1024])
    parser.add_argument('--batch-per-rank',type=int,choices=range(1,17),default=1)
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    output=args.output.resolve();output.mkdir(exist_ok=False);(output/'logs').mkdir()
    backend=read(args.backend_json)
    sources=[p for folder in ('python/moe_ub_cosim','backend/rdma')
             for p in sorted((ROOT/folder).glob('*')) if p.is_file()]
    provenance=dict(script_sha256=digest(Path(__file__)),
        platform_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        sources={str(p.relative_to(ROOT)):digest(p) for p in sources},
        backend_sha256={name:digest(Path(backend[name])) for name in ('probe','service')},
        model_source=read(BASE/'model_source.json'),hardware_calibrated=False,route_source='synthetic',
        purpose='scale_gate_EP4_replicas_not_strong_scaling',batch_per_rank=args.batch_per_rank,
        sizes=args.sizes)
    save(output/'provenance.start.json',provenance)
    commands=[];summaries=[]

    def cli(name,argv,timeout=630):
        usage=sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
        if usage>=3*1024**3 or shutil.disk_usage(output).free<8*1024**3:
            raise RuntimeError('declared cumulative artifact/reserve budget exhausted')
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,argv)]
        entry=dict(name=name,argv=command,cwd=str(ROOT),started_utc=datetime.now(timezone.utc).isoformat())
        with (output/'logs'/f'{name}.stdout').open('x') as stdout,(output/'logs'/f'{name}.stderr').open('x') as stderr:
            try:
                code=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                code=None
        entry.update(exit_code=code,ended_utc=datetime.now(timezone.utc).isoformat())
        commands.append(entry);save(output/'commands.json',commands)
        if code!=0:raise RuntimeError(name+' failed; retained evidence, no automatic retry')

    for size in args.sizes:
        inputs=output/f'inputs-{size}';model,protocol=prepare(inputs,size,args.batch_per_rank)
        cli(f'topology-{size}',['topology','--config',inputs/'topology.json','--output',output/f'topology-{size}'])
        if args.prepare_only:continue
        run=output/f'qwen3-{size}'
        cli(f'service-{size}',['rdma-run','--config',inputs/'model.json','--topology',output/f'topology-{size}',
            '--protocol',inputs/'protocol.json','--backend',backend['service'],'--protoc',args.protoc,'--output',run])
        cli(f'check-{size}',['rdma-check','--run',run])
        audit=read(run/'verification.json');result=read(run/'result.json');topology=read(run/'topology.json')
        nodes={n['id']:n for n in topology['nodes']}
        cross_pod=[m for m in result['messages'] if nodes[m['src']]['pod']!=nodes[m['dst']]['pod']]
        assert cross_pod,'scale gate did not traverse Pods'
        expected=['gpu','tor','agg','core','agg','tor']
        assert all([nodes[h['node']]['kind'] for h in m['first_packet_path']]==expected for m in cross_pod)
        assert audit['passed'] and audit['waves']==len(model['parallel']['groups'])*2
        assert audit['intra_host_payload_bytes']>0 and audit['inter_host_payload_bytes']>0
        record=dict(gpus=size,ep=4,inference_replicas=len(model['parallel']['groups']),
            global_tokens_per_step=size*args.batch_per_rank,**audit,
            topology_counts=read(output/f'topology-{size}/audit.json')['counts'],
            cross_pod_messages=len(cross_pod),cross_pod_payload_bytes=sum(m['bytes'] for m in cross_pod),
            execution_resources=read(run/'execution/probe.json'),
            max_rdma_queue_bytes=max(p['max_queue_at_hook_bytes'] for p in result['ports']),
            max_local_link_wait_ns=max(p['max_wait_ns'] for p in result['intra_host_links']),
            result_bytes=(run/'result.json').stat().st_size,
            completed_requests=len(read(run/'frontend.json')['requests']))
        summaries.append(record);save(output/'summary.json',summaries)
        print(json.dumps({k:v for k,v in record.items() if k!='execution_resources'}),flush=True)
        del result,cross_pod
    assert provenance['script_sha256']==digest(Path(__file__)),'experiment script changed during execution'
    assert all(provenance['sources'][str(p.relative_to(ROOT))]==digest(p) for p in sources),'platform changed during execution'
    save(output/'provenance.json',dict(**provenance,completed=True,network_executed=not args.prepare_only))


if __name__=='__main__':main()
