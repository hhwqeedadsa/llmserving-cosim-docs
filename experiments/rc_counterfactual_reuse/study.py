"""Independent real-route fragment and public-CLI counterfactual execution."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def read(path):
    return json.loads(Path(path).read_bytes())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


def prepare(repo, runtime, stage):
    from moe_ub_cosim.route_evidence import inspect_raw, validate_manifest
    from moe_ub_cosim.scaleout_topology import execute
    assert not stage.exists()
    manifest=read(repo/'data/moe-trace-small/manifest.json');validate_manifest(manifest)
    source=next(r for r in manifest['files'] if r['file_id']=='local-1472')
    raw=repo/source['file']['path'];assert sha(raw)==source['file']['sha256']
    prompt,steps=inspect_raw(raw.read_bytes(),manifest['layers'])
    experts=read(raw)[0]['3'][0];destinations=sorted({e//64 for e in experts}-{0})
    assert 2<=len(destinations)<=3
    identity=read(runtime/'backend.json')
    binary=Path(identity['backend']);assert sha(binary)==identity['backend_sha256']
    assert identity['driver_sha256']==sha(repo/'backend/rdma/rc_concurrent_probe.cc')
    stage.mkdir();inputs=stage/'observed';inputs.mkdir();(stage/'truth').mkdir()
    copy=stage/'runtime';copy.mkdir();(copy/'runtime/lib').mkdir(parents=True)
    shutil.copy2(binary,copy/binary.name)
    for name,digest in identity['library_hashes'].items():
        original=runtime/'runtime/lib'/name;assert sha(original)==digest
        shutil.copyfile(original,copy/'runtime/lib'/name)
    shutil.copyfile(raw,inputs/'source-1472.json')
    save(inputs/'source-manifest.json',manifest)
    save(inputs/'selection.json',dict(file_id='local-1472',entry=0,layer=3,token_row=0,
        expert_ids=experts,destinations=destinations,source_rank=0,EP=4,E=256,top_k=8,H=7168,
        activation_precision='declared BF16',hidden_bytes=14336,prompt_tokens=prompt,
        available_decode_steps=steps,raw_sha256=sha(raw),original_path=source['original_path'],
        scope='one token cross-server Dispatch data/notification fragment; no full CCL/LLMServingSim/GPU'))
    topology=dict(schema_version='moe-scaleout-topology/1',
        deployment=dict(pods=2,segments_per_pod=1,servers_per_segment=2,gpus_per_server=1,gpu_type='NONE'),
        fabric=dict(rails=1,planes=1,tors_per_segment_per_rail=1,upper_tier_scope='shared',
          aggs_per_pod_per_group=1,cores_per_group=1,tor_uplinks=1,agg_uplinks=1,core_wiring='grouped'),
        nic=dict(rails_by_gpu_slot=[[0]],total_rate_bps=100000000000),
        links={key:dict(rate_bps=100000000000,delay_ns=1000) for key in ('access','tor_agg','agg_core','tor_core')},
        port_limits=dict(gpu=16,tor=128,agg=128,core=128),budget=dict(max_nodes=32,max_links=64,max_switches=16))
    save(inputs/'topology-config.json',topology);execute(inputs/'topology-config.json',inputs/'topology')
    protocol=dict(schema_version='moe-scaleout-rdma/2',cc_mode=1,payload_bytes=256,chunk_bytes=256,
        ack_interval_bytes=1,base_rtt_ns=14000,switch_buffer_bytes=8388608,switch_counter_storage='port_dense',
        ecn_by_rate={'100000000000':dict(kmin_bytes=32768,kmax_bytes=131072,pmax=1.0)},seed=19,
        packet_capture=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=80000,wire_headers=True),queue_metrics=True,
        limits=dict(max_gpus=4,max_switches=16,max_links=64,max_messages=64,max_queue_events=80000,
          queue_capture='events',max_sim_ns=5000000,wall_seconds=60,rss_bytes=1073741824,
          address_bytes=2147483648,artifact_bytes=67108864,result_json_bytes=33554432))
    save(inputs/'rdma.json',protocol)
    work=dict(schema_version='moe-rc-concurrent-input/1',mtu=256,rto_ns=100000,
        memories=[dict(gpu=g,data_hex=bytes(16384).hex()) for g in range(4)],connections=[],loss=None)
    for cid,dst in enumerate(destinations):
        work['connections'].append(dict(id=cid,src=0,dst=dst,source_nic=1,destination_nic=1,plane=0,
          operations=[dict(kind='write',address=0,data_hex=bytes((i+cid)%251 for i in range(14336)).hex()),
                      dict(kind='fetch_add',address=14336,addend=1)]))
    for name,rule in [('healthy',None),('ack_loss',dict(connection=0,node=0,opcode=18,psn=56)),
                      ('data_loss',dict(connection=0,node=destinations[0],opcode=7,psn=1))]:
        value=deepcopy(work);value['loss']=rule;save(inputs/(name+'.json'),value)
    native=repo/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
    files=['src/network/utils/data-rate.cc','src/core/model/nstime.h',
           'src/point-to-point/model/point-to-point-net-device.cc',
           'src/point-to-point/model/qbb-net-device.cc']
    (inputs/'native-sources').mkdir()
    for name in files:shutil.copyfile(native/name,inputs/'native-sources'/Path(name).name)
    save(inputs/'identity.json',dict(platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
        native_commit=identity['native_commit'],backend_sha256=sha(copy/binary.name),
        library_hashes=identity['library_hashes'],native_source_hashes={n:sha(native/n) for n in files}))
    save(stage/'execution.json',dict(healthy='pending',ack_loss='pending',data_loss='pending',networks=0,closed=False))


def run(stage, name):
    ledger=read(stage/'execution.json')
    assert not ledger['closed'] and ledger[name]=='pending' and ledger['networks']<3
    if name!='healthy':
        assert ledger['healthy']=='completed' and (stage/'prediction.json').is_file()
        assert sha(stage/'prediction.json')==read(stage/'prediction-seal.json')['sha256']
    output=stage/'observed/healthy-run' if name=='healthy' else stage/'truth'/name
    command=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rc-fabric-probe',
      '--topology',str(stage/'observed/topology'),'--protocol',str(stage/'observed/rdma.json'),
      '--config',str(stage/'observed'/(name+'.json')),'--backend',str(stage/'runtime/moe-rdma-rc-concurrent'),
      '--output',str(output)]
    ledger[name]='started';ledger['networks']+=1;save(stage/'execution.json',ledger)
    result=subprocess.run(command,capture_output=True,timeout=90)
    (stage/(name+'.stdout')).write_bytes(result.stdout);(stage/(name+'.stderr')).write_bytes(result.stderr)
    ledger[name]='completed' if result.returncode==0 else 'failed'
    ledger['closed']=bool(result.returncode) or ledger['networks']==3
    save(stage/'execution.json',ledger)
    assert result.returncode==0,result.stderr.decode()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','healthy','ack_loss','data_loss'))
    parser.add_argument('--stage',type=Path,required=True)
    parser.add_argument('--repository',type=Path)
    parser.add_argument('--runtime',type=Path)
    args=parser.parse_args()
    if args.action=='prepare':prepare(args.repository.resolve(),args.runtime.resolve(),args.stage.resolve())
    else:run(args.stage.resolve(),args.action)
