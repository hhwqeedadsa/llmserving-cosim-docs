"""Execute declared small-fabric conditions through public CLI only; no UB.

Run with a Python environment already containing the locked frontend's runtime
dependencies. This script neither builds dependencies nor installs packages.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]


def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def topologies():
    clos=json.loads((ROOT/'examples/scaleout_rdma/small-clos-100g.json').read_text())
    leaf=deepcopy(clos);leaf['fabric'].update(aggs_per_pod_per_group=0,cores_per_group=2,tor_uplinks=2,agg_uplinks=0,core_wiring='full_mesh')
    single=deepcopy(clos);single['fabric'].update(rails=2,tors_per_segment_per_rail=1,
        aggs_per_pod_per_group=1,cores_per_group=1,tor_uplinks=1,agg_uplinks=1)
    single['nic']['rails_by_gpu_slot']=[[0],[1]]
    dual=deepcopy(single);dual['fabric']['planes']=2
    for link in dual['links'].values():link['rate_bps']=50_000_000_000
    isolated=deepcopy(single);isolated['fabric']['upper_tier_scope']='rail'
    result=dict(T0=leaf,T1=clos,T2=single,T3=dual,T4=isolated)
    for rate in (50,25):
        value=deepcopy(clos);value['links']['agg_core']['rate_bps']=rate*10**9
        result[f'T1-core{rate}']=value
    return result


def conditions():
    return [('clos','T1',4,0,8),('leaf','T0',4,0,8),('single-plane','T2',4,0,8),
            ('batch1','T1',1,0,8),('batch16','T1',16,0,8),
            ('core50','T1-core50',4,0,8),('core25','T1-core25',4,0,8),
            ('dual-plane','T3',4,0,8),('hotspot','T1',4,0.5,8),('ep4','T1',4,0,4)]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-json',type=Path,required=True)
    parser.add_argument('--protoc',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve();output.mkdir(exist_ok=False)
    (output/'inputs').mkdir();(output/'logs').mkdir()
    backend=json.loads(args.backend_json.read_text());records=[]
    def cli(name,arguments,expected_success=True):
        # This task's entire experiment tree has a 256MiB result budget.
        occupied=sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
        if occupied>=256*1024*1024 or shutil.disk_usage(output).free<8*1024**3:
            raise RuntimeError('cumulative experiment storage/free-space budget exhausted; no expansion')
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,arguments)]
        with (output/'logs'/f'{name}.stdout').open('w') as stdout,(output/'logs'/f'{name}.stderr').open('w') as stderr:
            result=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=90)
        records.append(dict(name=name,argv=command,cwd=str(ROOT),exit_code=result.returncode,
                            expected_success=expected_success))
        save(output/'commands.json',records)
        if (result.returncode==0)!=expected_success:
            raise RuntimeError(f'{name} unexpected exit {result.returncode}; inspect logs; matrix stopped')
    for name,config in topologies().items():
        path=output/'inputs'/f'{name}.json';save(path,config)
        cli('topology-'+name,['topology','--config',path,'--output',output/name])
    protocol=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
    threshold=protocol['ecn_by_rate']['100000000000']
    for rate in (25,50):protocol['ecn_by_rate'][str(rate*10**9)]=deepcopy(threshold)
    save(output/'inputs/rdma.json',protocol)
    # Boundary payloads test the real native path on both sides of the MTU.
    for mtu,sizes in [(1000,[999,1000,1001,3000]),(9000,[8999,9000,9001,27000])]:
        proto=deepcopy(protocol);proto['payload_bytes']=mtu;proto['chunk_bytes']=4*mtu
        pp=output/'inputs'/f'rdma-{mtu}.json';save(pp,proto)
        mp=output/'inputs'/f'messages-{mtu}.json'
        save(mp,[dict(id=i,src=0,dst=4,bytes=n,plane=0,ready_ns=i*100000) for i,n in enumerate(sizes)])
        cli('boundary-'+str(mtu),['rdma-probe','--topology',output/'T1','--config',pp,'--messages',mp,
            '--backend',backend['probe'],'--output',output/f'boundary-{mtu}'])
    # Many-to-one incast is a protocol observation, separate from model results.
    mp=output/'inputs/incast.json'
    save(mp,[dict(id=i,src=i%7,dst=7,bytes=65536,plane=0,ready_ns=0) for i in range(64)])
    cli('incast',['rdma-probe','--topology',output/'T1','--config',output/'inputs/rdma.json','--messages',mp,
                 '--backend',backend['probe'],'--output',output/'incast'])
    incast=json.loads((output/'incast/result.json').read_text())
    if not (sum(p['ecn_ce_seen'] for p in incast['ports'])>0 and
            any(m['dcqcn_feedback_seen'] for m in incast['messages'])):
        raise RuntimeError('incast did not demonstrate actual ECN feedback; stop before model matrix')
    # NACK repairs a middle packet; a missing tail has no later packet to
    # trigger NACK in this fork. Keep that expected failure as a limitation.
    for label,bytes_,sequence,success in [('middle-loss',4000,1000,True),('tail-loss',1001,1000,False)]:
        fault=deepcopy(protocol);fault['drop_once']=dict(message_id=0,dst_gpu=4,sequence=sequence)
        fp=output/'inputs'/f'{label}-rdma.json';save(fp,fault)
        mp=output/'inputs'/f'{label}-messages.json';save(mp,[dict(id=0,src=0,dst=4,bytes=bytes_,plane=0,ready_ns=0)])
        cli(label,['rdma-probe','--topology',output/'T1','--config',fp,'--messages',mp,
            '--backend',backend['probe'],'--output',output/label],expected_success=success)
        result=json.loads((output/label/'result.json').read_text());message=result['messages'][0]
        if result['fault_drops']!=1:raise RuntimeError('requested drop did not occur exactly once')
        if success and not (message['ack_callbacks']==1 and message['source_data_payload_bytes']>bytes_):
            raise RuntimeError('middle loss did not retransmit and complete exactly once')
        if not success and not (message['receive_ready_ns'] is None and message['ack_callbacks']==0):
            raise RuntimeError('tail loss unexpectedly exposed partially received data as ready')
    template=json.loads((ROOT/'examples/scaleout_rdma/service.json').read_text())
    summaries=[]
    def run_condition(name,topology,batch,hotspot,groups,seed):
        config=deepcopy(template);config['workload'].update(batch_per_rank=batch,hotspot_fraction=hotspot,route_seed=seed)
        config['parallel']['groups']=groups
        config_path=output/'inputs'/f'{name}-{seed}.json';save(config_path,config)
        run=output/f'{name}-{seed}'
        cli(f'{name}-{seed}',['rdma-run','--config',config_path,'--topology',output/topology,
            '--protocol',output/'inputs/rdma.json','--backend',backend['service'],'--protoc',args.protoc,'--output',run])
        verify=json.loads((run/'verification.json').read_text());result=json.loads((run/'result.json').read_text())
        summary=dict(condition=name,topology=topology,batch_per_rank=batch,hotspot_fraction=hotspot,
            ep_groups=groups,route_seed=seed,run=str(run),**verify,
            max_queue_bytes=max(p['max_queue_at_hook_bytes'] for p in result['ports']),
            ecn_ce_events=sum(p['ecn_ce_seen'] for p in result['ports']),
            pfc_pause_events=sum(p['pfc_pause'] for p in result['ports']),
            plane_payload_bytes={str(p):sum(m['bytes'] for m in result['messages'] if m['plane']==p) for p in range(result['planes'])})
        summaries.append(summary);save(output/'summary.json',summaries)
        print(name,seed,summary['finish_ns'],'ns',summary['remote_payload_bytes'],'payload B',flush=True)
    for name,topology,batch,hotspot,ep in conditions():
        groups=[list(range(8))] if ep==8 else [list(range(4)),list(range(4,8))]
        for seed in (11,23,37):run_condition(name,topology,batch,hotspot,groups,seed)
    # An isolated rail cannot be bridged by a GPU. Reject before native launch.
    invalid=output/'inputs/invalid-ep8.json';save(invalid,template)
    cli('isolated-ep8-rejected',['rdma-run','--config',invalid,'--topology',output/'T4',
        '--protocol',output/'inputs/rdma.json','--backend',backend['service'],'--protoc',args.protoc,
        '--output',output/'invalid-ep8'],expected_success=False)
    if (output/'invalid-ep8').exists():raise RuntimeError('invalid rail group created a run directory')
    run_condition('isolated-ep4','T4',4,0,[[0,2,4,6],[1,3,5,7]],11)
    save(output/'provenance.json',dict(script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        platform_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        completed=True,main_runs=30,functional_service_runs=1,protocol_probes=5,network_backend='single_native_ns3',
        hardware_calibrated=False,route_source='synthetic',cases=conditions()))


if __name__=='__main__':main()
