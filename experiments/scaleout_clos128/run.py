"""Bounded public-CLI gates and 16/32/128-GPU full-shape GQA MoE executions."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
EXAMPLES=ROOT/'examples/scaleout_clos128'


def read(path):return json.loads(path.read_text())
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-json',type=Path,required=True)
    parser.add_argument('--protoc',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--sizes',nargs='+',type=int,choices=[16,32,128],default=[16,32,128])
    args=parser.parse_args();out=args.output.resolve();out.mkdir(exist_ok=False);(out/'logs').mkdir()
    backend=read(args.backend_json);records=[];summaries=[]
    sources=[p for folder in ('python/moe_ub_cosim','backend/rdma') for p in sorted((ROOT/folder).glob('*')) if p.is_file()]
    start=dict(script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        platform_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        backend_sha256={name:hashlib.sha256(Path(backend[name]).read_bytes()).hexdigest() for name in ('probe','service')})
    save(out/'provenance.start.json',start)
    def cli(name,argv):
        if sum(p.stat().st_size for p in out.rglob('*') if p.is_file())>=3*1024**3 or shutil.disk_usage(out).free<8*1024**3:
            raise RuntimeError('declared artifact/reserve budget exhausted')
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,argv)]
        with (out/'logs'/f'{name}.stdout').open('w') as stdout,(out/'logs'/f'{name}.stderr').open('w') as stderr:
            result=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=330)
        records.append(dict(name=name,argv=command,cwd=str(ROOT),exit_code=result.returncode))
        save(out/'commands.json',records)
        if result.returncode:raise RuntimeError(name+' failed; inspect retained logs')
    cli('gate-topology',['topology','--config',EXAMPLES/'clos-16.json','--output',out/'gate-topology'])
    probes=[dict(id=0,src=0,dst=8,bytes=65536,plane=0,ready_ns=0),
            dict(id=50000,src=1,dst=8,bytes=65536,plane=0,ready_ns=0),
            dict(id=1,src=2,dst=4,bytes=1048576,plane=0,ready_ns=0),
            dict(id=2,src=3,dst=4,bytes=1048576,plane=0,ready_ns=0)]
    save(out/'probes.json',probes);save(out/'solo.json',[dict(id=3,src=2,dst=4,bytes=1048576,plane=0,ready_ns=0)])
    for name in ('probes','solo'):
        cli(name,['rdma-probe','--topology',out/'gate-topology','--config',EXAMPLES/'rdma.json',
            '--messages',out/f'{name}.json','--backend',backend['probe'],'--output',out/name])
    flows={m['id']:m for m in read(out/'probes/result.json')['messages']}
    assert all(m['ack_callbacks']==1 and m['source_data_payload_bytes']==m['bytes'] for m in flows.values())
    assert flows[0]['port']==flows[50000]['port']
    assert flows[0]['first_packet_path'] and flows[50000]['first_packet_path']
    solo=read(out/'solo/result.json')['messages'][0]
    assert max(flows[i]['receive_ready_ns'] for i in (1,2))>solo['receive_ready_ns']
    assert all(flows[i]['transport']=='intra_host_chunked_crossbar' for i in (1,2))
    gate=dict(passed=True,same_wire_port_distinct_endpoints=[0,50000],solo_local_finish_ns=solo['receive_ready_ns'],
              incast_local_finish_ns=[flows[i]['receive_ready_ns'] for i in (1,2)])
    save(out/'gates.json',gate)
    print('micro gates passed',gate,flush=True)
    for size in args.sizes:
        cli(f'topology-{size}',['topology','--config',EXAMPLES/f'clos-{size}.json','--output',out/f'topology-{size}'])
        run=out/f'qwen3-{size}'
        cli(f'service-{size}',['rdma-run','--config',EXAMPLES/f'qwen3-{size}.json','--topology',out/f'topology-{size}',
            '--protocol',EXAMPLES/'rdma.json','--backend',backend['service'],'--protoc',args.protoc,'--output',run])
        cli(f'check-{size}',['rdma-check','--run',run])
        verification=read(run/'verification.json');result=read(run/'result.json')
        record=dict(gpus=size,**verification,capacity=read(run/'capacity.json'),
            max_rdma_queue_bytes=max(p['max_queue_at_hook_bytes'] for p in result['ports']),
            queue_observations=result['queue_observations'],
            max_local_link_wait_ns=max(p['max_wait_ns'] for p in result['intra_host_links']),
            execution_resources=read(run/'execution/probe.json'))
        assert record['intra_host_payload_bytes']>0 and record['inter_host_payload_bytes']>0
        assert len([n for n in read(run/'topology.json')['nodes'] if n['kind']=='gpu'])==size
        summaries.append(record);save(out/'summary.json',summaries)
        print(size,verification,flush=True)
    assert start['script_sha256']==hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'experiment source changed during execution'
    assert all(start['source_sha256'][str(p.relative_to(ROOT))]==hashlib.sha256(p.read_bytes()).hexdigest() for p in sources),'platform source changed during execution'
    save(out/'provenance.json',dict(completed=True,**start,
        model_source=read(EXAMPLES/'model_source.json'),hardware_calibrated=False,route_source='synthetic'))


if __name__=='__main__':main()
