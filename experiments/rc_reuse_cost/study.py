"""Independent configurations and Linux wait4 for complete paired workflows."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def read(p): return json.loads(p.read_bytes())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v): p.write_text(json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


def prepare(repo, runtime, stage):
    from moe_ub_cosim.route_evidence import inspect_raw, validate_manifest
    from moe_ub_cosim.scaleout_topology import execute
    assert not stage.exists()
    manifest=read(repo/'data/moe-trace-small/manifest.json'); validate_manifest(manifest)
    source=next(r for r in manifest['files'] if r['file_id']=='local-6447')
    raw=repo/source['file']['path']; assert sha(raw)==source['file']['sha256']
    inspect_raw(raw.read_bytes(),manifest['layers'])
    experts=read(raw)[0]['3'][0]; destinations=sorted({e//64 for e in experts}-{0})
    assert destinations==[1,2,3]
    identity=read(runtime/'backend.json'); binary=Path(identity['backend'])
    assert sha(binary)==identity['backend_sha256']
    assert sha(repo/'backend/rdma/rc_concurrent_probe.cc')==identity['driver_sha256']
    stage.mkdir(); inputs=stage/'inputs'; inputs.mkdir(); (stage/'workflows').mkdir()
    copied=stage/'runtime'; (copied/'runtime/lib').mkdir(parents=True)
    shutil.copy2(binary,copied/binary.name)
    for name,digest in identity['library_hashes'].items():
        original=runtime/'runtime/lib'/name; assert sha(original)==digest
        shutil.copyfile(original,copied/'runtime/lib'/name)
    shutil.copyfile(raw,inputs/'source-6447.json')
    save(inputs/'identity.json',dict(identity,platform_commit=subprocess.check_output(
        ['git','rev-parse','HEAD'],cwd=repo,text=True).strip()))
    save(inputs/'selection.json',dict(file_id='local-6447',raw_sha256=sha(raw),original_path=source['original_path'],
        entry=0,layer=3,token=0,expert_ids=experts,destinations=destinations,EP=4,E=256,top_k=8,H=7168,
        activation_dtype='declared BF16',source_rank=0,hidden_bytes=14336,write_bytes=43008,
        scope='one real routing choice; constructed segmented/gated RC program, not actual CCL/GPU'))
    topology=dict(schema_version='moe-scaleout-topology/1',
        deployment=dict(pods=2,segments_per_pod=1,servers_per_segment=2,gpus_per_server=1,gpu_type='NONE'),
        fabric=dict(rails=1,planes=1,tors_per_segment_per_rail=1,upper_tier_scope='shared',
            aggs_per_pod_per_group=1,cores_per_group=1,tor_uplinks=1,agg_uplinks=1,core_wiring='grouped'),
        nic=dict(rails_by_gpu_slot=[[0]],total_rate_bps=100000000000),
        links={k:dict(rate_bps=100000000000,delay_ns=1000) for k in ('access','tor_agg','agg_core','tor_core')},
        port_limits=dict(gpu=16,tor=128,agg=128,core=128),budget=dict(max_nodes=32,max_links=64,max_switches=16))
    save(inputs/'topology-config.json',topology); execute(inputs/'topology-config.json',inputs/'topology')
    protocol=dict(schema_version='moe-scaleout-rdma/2',cc_mode=1,payload_bytes=4096,chunk_bytes=4096,
        ack_interval_bytes=1,base_rtt_ns=14000,switch_buffer_bytes=8388608,switch_counter_storage='port_dense',
        ecn_by_rate={'100000000000':dict(kmin_bytes=32768,kmax_bytes=131072,pmax=1.0)},seed=19,queue_metrics=False,
        limits=dict(max_gpus=4,max_switches=16,max_links=64,max_messages=64,max_queue_events=80000,
            queue_capture='summary',max_sim_ns=5000000,wall_seconds=60,rss_bytes=1073741824,
            address_bytes=2147483648,artifact_bytes=67108864,result_json_bytes=33554432))
    for kind in ('fast','capture'):
        folder=inputs/('protocol_'+kind); folder.mkdir(); value=deepcopy(protocol)
        if kind=='capture': value['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=80000,wire_headers=False)
        save(folder/'rdma.json',value)
    queries=[]
    for mode in ('terminal','gated'):
        folder=inputs/mode; folder.mkdir()
        work=dict(schema_version='moe-rc-concurrent-input/1',mtu=4096,rto_ns=25000,
            memories=[dict(gpu=g,data_hex=bytes(16384).hex()) for g in range(4)],connections=[],loss=None)
        for cid,dst in enumerate(destinations):
            data=bytes((i+sum(experts)+cid)%251 for i in range(14336))
            notify=dict(kind='fetch_add',address=14336,addend=1)
            if cid==2:
                ops=[dict(kind='write',address=0,data_hex=data.hex()),notify]
                if mode=='gated':ops.insert(0,dict(kind='fetch_add',address=14344,addend=1))
            else:
                ops=[dict(kind='write',address=0,data_hex=data[:7168].hex()),
                    dict(kind='fetch_add',address=14344,addend=1),
                    dict(kind='write',address=7168,data_hex=data[7168:].hex()),notify]
            work['connections'].append(dict(id=cid,src=0,dst=dst,source_nic=1,destination_nic=1,plane=0,operations=ops))
        save(folder/'normal.json',work)
        for cid in range(3):
            psn=0; atomics=[]
            for op in work['connections'][cid]['operations']:
                if op['kind']=='write':psn+=(len(bytes.fromhex(op['data_hex']))+4095)//4096
                else:atomics.append(psn);psn+=1
            fault=deepcopy(work); fault['loss']=dict(connection=cid,node=0,opcode=18,
                psn=atomics[-1] if mode=='terminal' else atomics[0])
            name=f'{mode}{cid}'; save(folder/(name+'.json'),fault)
            queries.append(dict(id=name,mode=mode,work=f'{mode}/{name}.json'))
    assert len({sha(inputs/q['work']) for q in queries})==6
    save(inputs/'queries.json',queries)
    algorithms=stage/'algorithms'; algorithms.mkdir()
    algorithm=repo/'experiments/rc_overlap_reuse/predict.py'
    shutil.copyfile(algorithm,algorithms/'port_time_predict.py')
    save(inputs/'algorithm.json',dict(path='algorithms/port_time_predict.py',sha256=sha(algorithm),
        source='experiments/rc_overlap_reuse/predict.py',source_commit='5f7adb4',scope='algorithm only; no old driver'))
    hashes={p.relative_to(stage).as_posix():sha(p) for p in sorted(inputs.rglob('*')) if p.is_file()}
    save(stage/'prepared-hashes.json',hashes)
    jobs=[dict(pair=pair,method=method,state='pending') for pair,methods in enumerate(
        [('direct','reuse'),('reuse','direct'),('direct','reuse')]) for method in methods]
    save(stage/'execution.json',dict(jobs=jobs,closed=False,max_networks=42))


def measure(stage,index):
    ledger=read(stage/'execution.json'); assert not ledger['closed']
    job=ledger['jobs'][index]; assert job['state']=='pending'
    for prior in range(index):
        assert ledger['jobs'][prior]['state']=='completed'
        assert read(stage/f'checked-{prior}.json')['passed']
    output=stage/'workflows'/f'{index}-{job["method"]}'
    command=[sys.executable,'-B','-m','experiments.rc_reuse_cost.workflow','--stage',str(stage),
             '--method',job['method'],'--output',str(output)]
    job['state']='started'; save(stage/'execution.json',ledger)
    before=Path('/proc/loadavg').read_text().strip(); affinity=sorted(os.sched_getaffinity(0))
    start=time.perf_counter_ns(); child=subprocess.Popen(command)
    pid,status,usage=os.wait4(child.pid,0); end=time.perf_counter_ns(); assert pid==child.pid
    child.returncode=os.waitstatus_to_exitcode(status)
    names=('ru_utime','ru_stime','ru_maxrss','ru_minflt','ru_majflt','ru_inblock','ru_oublock','ru_nvcsw','ru_nivcsw')
    save(stage/f'measure-{index}.json',dict(index=index,pair=job['pair'],method=job['method'],command=command,
        cwd=str(Path.cwd()),raw_wait_status=status,exit_code=child.returncode,
        rusage={n:getattr(usage,n) for n in names},start_perf_ns=start,end_perf_ns=end,
        elapsed_wall_ns=end-start,subtree_CPU_ns=round((usage.ru_utime+usage.ru_stime)*10**9),
        affinity=affinity,loadavg_before=before,loadavg_after=Path('/proc/loadavg').read_text().strip(),
        includes_waited_descendants=True,exact_group_peak_rss=False))
    job['state']='completed' if child.returncode==0 else 'failed'
    ledger['closed']=child.returncode!=0 or index==5; save(stage/'execution.json',ledger)
    assert child.returncode==0,'complete workflow failed; preserve output and stop'


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('action',choices=('prepare','measure'))
    p.add_argument('--stage',type=Path,required=True); p.add_argument('--repository',type=Path)
    p.add_argument('--runtime',type=Path); p.add_argument('--index',type=int); args=p.parse_args()
    if args.action=='prepare':prepare(args.repository.resolve(),args.runtime.resolve(),args.stage.resolve())
    else:measure(args.stage.resolve(),args.index)
