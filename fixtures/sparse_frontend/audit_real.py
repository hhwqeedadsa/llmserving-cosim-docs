"""Offline real-source admission at three Clos scales; never run a network.

The input intake is an inventory, not trusted validation: every selected raw
file is hash checked and Replay validates its full original layer structure.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave,validate
from moe_ub_cosim.service_transport import message_preflight


def load(path):
    if path.stat().st_size>32*2**20:raise ValueError('oversized input')
    return json.loads(path.read_bytes())


def save(path,value):
    with path.open('x') as stream:
        json.dump(value,stream,indent=2);stream.write('\n')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(intake,routes,source_manifest,prepared,output):
    output.mkdir(parents=True,exist_ok=False)
    source=load(intake)
    if source['source']['confirmed_directory']!='cognitivecomputations/DeepSeek-R1-AWQ/mmlu/':
        raise ValueError('this functional audit requires user-confirmed R1-AWQ/MMLU inputs')
    selected=sorted((r for r in source['files'] if r['eligible_offline']),key=lambda r:int(Path(r['name']).stem))
    if not selected or len(selected)>64:raise ValueError('need 1..64 declared source files')
    manifest=load(source_manifest)
    catalogued={(row['original_path'],row['file']['sha256']) for row in manifest['files']}
    manifest['files']=[]
    manifest['source'].update(kind='user_provided_model_routes',dataset_revision=None,
        source_url='https://huggingface.co/datasets/core12345/MoE_expert_selection_trace/tree/main/cognitivecomputations/DeepSeek-R1-AWQ/mmlu',
        usage_terms='User-supplied originals; model/task directory confirmed by user. Local SHA only; no remote revision or license verification.')
    manifest['source']['unknown_fields']=sorted(set(manifest['source']['unknown_fields'])|{'dataset_revision'})
    raw_rows={};folder=output/'originals';folder.mkdir()
    for row in selected:
        name=row['name']
        identity=(row['original_path'],row['sha256'])
        if (Path(name).name!=name or not row['original_path'].startswith(source['source']['confirmed_directory']) or
                not (row['origin_directory_user_confirmed'] or identity in catalogued)):
            raise ValueError('source identity is neither user-confirmed nor present in supplied manifest')
        path=routes/name
        if sha(path)!=row['sha256']:raise ValueError('intake/source differs: '+name)
        target=folder/name;target.write_bytes(path.read_bytes())
        file_id='local-'+path.stem;raw_rows[file_id]=load(target)
        manifest['files'].append(dict(file_id=file_id,original_path=row['original_path'],
            file=dict(path=str(target),sha256=sha(target)),format='token_layer_map_v1'))
    path=output/'manifest.json';save(path,manifest)
    summaries=[]
    for gpus in (128,256,512):
        template=prepared/f'clos{gpus}'
        c=load(template/'service.json');p=load(template/'rdma.json');t=load(template/'topology/topology.json')
        c['model'].update(name='deepseek-ai/DeepSeek-R1',hidden_size=7168,ffn_hidden_size=2048,experts=256,top_k=8,layers=2)
        c['workload'].update(batch_per_rank=1,context_tokens=2048)
        c['route_replay'].update(request_layout='sparse_explicit/1',manifest=dict(path=str(path),sha256=sha(path)),
            layer_ids=[3,4],requests=[dict(rank_id=i*gpus//len(selected),slot=0,file_id=row['file_id'],first_decode_entry=1)
                                   for i,row in enumerate(manifest['files'])],
            budget=dict(max_files=len(selected),max_bytes=64*2**20))
        replay=Replay(c,output)
        waves=[make_wave(c,group,index,step,1,replay) for index,group in enumerate(c['parallel']['groups']) for step in range(2)]
        # Independent original-JSON -> expert owner -> directed message matrix.
        for wave in waves:
            group=wave['rank_ids'];expected=Counter()
            for request in c['route_replay']['requests']:
                src=request['rank_id']
                if src not in group:continue
                for layer,source_layer in enumerate((3,4)):
                    for expert in raw_rows[request['file_id']][request['first_decode_entry']+wave['decode_step']][str(source_layer)][0]:
                        dst=group[expert//(256//len(group))]
                        if src!=dst:
                            expected[layer,'dispatch',src,dst]+=14336
                            expected[layer,'combine',dst,src]+=14336
            actual={(m['layer'],m['phase'],m['src'],m['dst']):m['bytes'] for m in wave['messages']}
            if actual!=dict(expected):raise ValueError('raw-source matrix differs')
        keys=sorted({(m['src'],m['dst'],m['plane']) for wave in waves for m in wave['messages']})
        transport=c['transport']
        transport['streams']=[dict(id=i,src=s,dst=d,plane=plane,wire_port=21000+i,
            source_nic=1,destination_nic=1,queue_depth=32) for i,(s,d,plane) in enumerate(keys)]
        transport['limits']['max_streams']=len(keys);transport['limits']['max_work']=2048;p['limits']['max_messages']=2048
        validate(c,t,p,replay);preflight=message_preflight(t,p,c)
        for wave in waves:preflight.accept_wave(wave,0)
        by_stream=Counter()
        for wave in waves:
            for m in wave['messages']:by_stream[m['src'],m['dst'],m['plane']]+=m['bytes']
        participants=sorted({m[k] for wave in waves for m in wave['messages'] for k in ('src','dst')})
        sources=sorted({r['rank_id'] for r in replay.records.values()})
        summary=dict(gpu_endpoints=gpus,ep_sizes=list(map(len,c['parallel']['groups'])),
            experts_per_rank=[256//len(g) for g in c['parallel']['groups']],independent_requests=len(replay.records),
            source_ranks=sources,network_participant_ranks=participants,expert_only_network_ranks=sorted(set(participants)-set(sources)),
            messages=sum(len(w['messages']) for w in waves),logical_bytes=sum(by_stream.values()),persistent_streams=len(keys),
            max_planned_stream_bytes=max(by_stream.values()),source_layers=[3,4],decode_entries=[1,2],
            network_executed=False,independent_original_matrix_verified=True,hardware_calibrated=False,
            placement_scope='declared evenly spaced source ranks; original dataset does not provide deployment/time')
        save(output/f'service{gpus}.json',c);save(output/f'rdma{gpus}.json',p)
        save(output/f'audit{gpus}.json',summary);summaries.append(summary)
    report=dict(passed=True,network_runs=0,sources=[dict(name=r['name'],sha256=r['sha256']) for r in selected],
        rejected_intake_files=source['rejected_files'],remote_revision_verified=False,
        intake_sha256=sha(intake),source_manifest_sha256=sha(source_manifest),manifest_sha256=sha(path),scales=summaries)
    save(output/'audit.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('intake','routes-root','source-manifest','prepared-root','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    a=parser.parse_args()
    result=audit(a.intake.resolve(),a.routes_root.resolve(),a.source_manifest.resolve(),a.prepared_root.resolve(),a.output.resolve())
    print(json.dumps(dict(passed=True,files=len(result['sources']),network_runs=0,
                         scales=[{k:r[k] for k in ('gpu_endpoints','independent_requests','messages','logical_bytes','persistent_streams')}
                                 for r in result['scales']])))
