"""Freeze source-checked concurrent MoE tasks before any network execution."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave, validate
from moe_ub_cosim.scaleout_topology import generate

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
GROUPS=[[0,1,4,5],[2,3,6,7]]
CASES=('gbn-normal','gbn-local','sr-normal','sr-local')


def load(p):
    if p.stat().st_size>32*2**20:raise ValueError('input too large: '+str(p))
    return json.loads(p.read_bytes())


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def save(p,value):
    with p.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')


def prepare(intake_path,routes,output):
    output.mkdir(parents=True,exist_ok=False)
    source_root=output/'routes';source_root.mkdir()
    intake=load(intake_path)
    selected=sorted((r for r in intake['files'] if r['eligible_offline']),
                    key=lambda r:int(Path(r['name']).stem))[:8]
    if len(selected)!=8 or len({r['sha256'] for r in selected})!=8:raise ValueError('need eight distinct valid inputs')
    manifest=load(ROOT/'experiments/moe_trace_hotspots/results/four_requests_20261018/manifest.json')
    manifest['source'].update(kind='user_provided_model_routes',dataset_revision=None,
        source_url='https://huggingface.co/datasets/core12345/MoE_expert_selection_trace/tree/main/cognitivecomputations/DeepSeek-R1-AWQ/mmlu',
        usage_terms='User supplied files with confirmed model/task directory; local SHA-256 only. Remote revision and license not independently verified.')
    manifest['source']['unknown_fields']=sorted(set(manifest['source']['unknown_fields'])|{'dataset_revision'})
    manifest['files']=[]
    sources=[]
    for rank,row in enumerate(selected):
        name=row['name']
        if Path(name).name!=name or not row['origin_directory_user_confirmed']:raise ValueError('invalid source identity')
        path=routes/name
        if sha(path)!=row['sha256']:raise ValueError('intake hash differs: '+name)
        target=source_root/name;target.write_bytes(path.read_bytes())
        manifest['files'].append(dict(file_id='local-'+path.stem,original_path=row['original_path'],
            file=dict(path=str(target),sha256=sha(target)),format='token_layer_map_v1'))
        sources.append(dict(rank=rank,name=name,sha256=sha(target),bytes=target.stat().st_size,
            original_path=row['original_path'],remote_revision_content_verified=False))
    manifest_path=output/'manifest.json';save(manifest_path,manifest)
    topology_config=load(ROOT/'examples/astra_stream_service/topology.json')
    topology_config['deployment'].update(servers_per_segment=2,gpu_type='H100')
    for name in ('tor_agg','agg_core','tor_core'):topology_config['links'][name]['rate_bps']=25000000000
    save(output/'topology-config.json',topology_config)
    topology,report=generate(topology_config)
    topology_root=output/'topology';topology_root.mkdir();save(topology_root/'topology.json',topology)
    save(topology_root/'report.json',report)
    config=load(ROOT/'examples/astra_stream_service/service.json')
    config['model'].update(name='deepseek-ai/DeepSeek-R1',hidden_size=7168,ffn_hidden_size=2048,
        experts=256,top_k=8,gpu_type='H100')
    config['workload'].update(batch_per_rank=1,context_tokens=2048)
    config['parallel']['groups']=deepcopy(GROUPS)
    config['route_replay'].update(manifest=dict(path=str(manifest_path),sha256=sha(manifest_path)),
        layer_ids=[3,4],requests=[dict(rank_id=rank,slot=0,file_id=row['file_id'],first_decode_entry=1)
            for rank,row in enumerate(manifest['files'])],budget=dict(max_files=8,max_bytes=32*2**20))
    transport=config['transport'];transport['streams']=[]
    for group in GROUPS:
        for src in group:
            for dst in group:
                if src==dst:continue
                sid=len(transport['streams'])
                transport['streams'].append(dict(id=sid,src=src,dst=dst,plane=0,wire_port=21000+sid,
                    source_nic=1,destination_nic=1,queue_depth=32))
    transport['limits'].update(max_streams=24,max_work=512,max_link_transmissions=100000)
    transport['transport_recovery'].update(receive_buffer_bytes=2**20+8999,inflight_bytes=2**20,max_events=30000)
    transport['dcqcn_reference']['max_events']=30000
    transport['source_rate_capture']=dict(start_ns=0,end_ns=5000000,max_events=30000)
    protocol=load(ROOT/'examples/astra_stream_service/rdma.json')
    protocol.update(schema_version='moe-scaleout-rdma/2',payload_bytes=9000,chunk_bytes=9000,
        limits=dict(max_gpus=8,max_switches=8,max_links=32,max_messages=512,max_queue_events=100000,
            queue_capture='events',max_sim_ns=5000000,wall_seconds=60,rss_bytes=2**30,
            address_bytes=2**31,artifact_bytes=64*2**20,result_json_bytes=16*2**20),
        queue_metrics=True,switch_drop_capture=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=10000))
    protocol['ecn_by_rate']['25000000000']=dict(kmin_bytes=16384,kmax_bytes=65536,pmax=1.0)
    replay=Replay(config,ROOT)
    planned=output/'planned';planned.mkdir();totals=Counter();logical=0;messages=0
    pair_to_stream={(s['src'],s['dst']):s['id'] for s in transport['streams']}
    hotspot=None
    for gi,group in enumerate(GROUPS):
        for step in range(2):
            wave=make_wave(config,group,gi,step,1,replay)
            # Independently enumerate original choices; do not trust make_wave's matrix.
            expected=Counter()
            for rank in group:
                raw=load(source_root/selected[rank]['name'])
                for local,lid in enumerate((3,4)):
                    for expert in raw[step+1][str(lid)][0]:
                        dst=group[expert//64]
                        if dst!=rank:
                            expected[local,'dispatch',rank,dst]+=14336
                            expected[local,'combine',dst,rank]+=14336
            actual={(m['layer'],m['phase'],m['src'],m['dst']):m['bytes'] for m in wave['messages']}
            if dict(expected)!=actual:raise ValueError('independent contribution matrix differs')
            if gi==0 and step==0:
                incoming=Counter({rank:0 for rank in group})
                for m in wave['messages']:
                    if m['layer']==0 and m['phase']=='dispatch':incoming[m['dst']]+=m['bytes']
                hotspot=min(group,key=lambda rank:(-incoming[rank],rank))
                save(output/'hotspot.json',dict(group=0,graph='g0.w0',layer=0,source_layer=3,
                    incoming_bytes=dict(incoming),rank=hotspot,selection='largest original first-slice remote input; tie lowest rank'))
            for m in wave['messages']:
                totals[pair_to_stream[m['src'],m['dst']]]+=m['bytes'];logical+=m['bytes'];messages+=1
                if m['bytes']>2**20:raise ValueError('message exceeds platform capacity')
            save(planned/(wave['graph_id']+'.json'),wave)
    if max(totals.values())>=2**20:raise ValueError('SR window might bind planned total')
    if messages>512:raise ValueError('too many planned messages')
    for name in CASES:
        c=deepcopy(config);p=deepcopy(protocol)
        if name.startswith('gbn'):
            r=c['transport']['transport_recovery'];r['mode']='go_back_n/1'
            del r['receive_buffer_bytes'];del r['inflight_bytes']
        if name.endswith('local'):
            p['data_loss']=dict(kind='periodic_destination_data/1',numerator=1,denominator=1,offset=0,
                start_ns=0,end_ns=100000,nodes=[hotspot])
        validate(c,topology,p,Replay(c,ROOT))
        save(output/(name+'-service.json'),c);save(output/(name+'-protocol.json'),p)
    save(output/'preparation.json',dict(schema_version='moe-local-transient-preparation/1',
        platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        intake_sha256=sha(intake_path),sources=sources,groups=GROUPS,cases=list(CASES),hotspot_rank=hotspot,
        logical_bytes=logical,messages=messages,planned_stream_bytes=dict(totals),
        sr_window_bytes=2**20,sr_window_nonbinding_by_all_planned_bytes=True,
        sr_per_stream_receive_capacity=2**20+8999,sr_all_stream_capacity=24*(2**20+8999),
        method_hashes={p.name:sha(p) for p in HERE.iterdir() if p.suffix in ('.py','.md')},
        input_hashes={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()},
        hardware_calibrated=False,network_executed=False,remote_revision_verified=False))
    print(json.dumps(dict(passed=True,requests=8,messages=messages,logical_bytes=logical,
        max_stream_bytes=max(totals.values()),hotspot_rank=hotspot,network_runs=0)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('intake','routes-root','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();prepare(a.intake.resolve(),a.routes_root.resolve(),a.output.resolve())
