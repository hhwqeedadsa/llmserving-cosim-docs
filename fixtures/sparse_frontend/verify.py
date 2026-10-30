"""Independent original-route arithmetic for the completed sparse Clos case."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.scaleout_verify import node_events,event_time


def load(path):return json.loads(path.read_bytes())
def require(value,message):
    if not value:raise ValueError(message)


def verify(root,output):
    native=root/'native128';c=load(native/'input.json');f=load(native/'frontend.json');r=load(native/'result.json')
    public=load(native/'verification.json');events=node_events(native)
    require(public['passed'] and r['passed'] and r['drained'],'actual run did not pass')
    require(r['gpu_count']==128,'wrong native endpoint count')
    manifest_path=native/c['route_replay']['manifest']['path'];manifest=load(manifest_path)
    sources={}
    for row in manifest['files']:
        path=native/row['file']['path']
        require(hashlib.sha256(path.read_bytes()).hexdigest()==row['file']['sha256'],'changed raw input')
        sources[row['file_id']]=load(path)
    requests=c['route_replay']['requests'];source_ranks={row['rank_id'] for row in requests}
    require(len(requests)==len(sources)==4 and set(f['active_source_ranks'])==source_ranks,'fake/missing source request')
    require({q['id'] for q in f['requests']}=={row['rank_id']*c['workload']['batch_per_rank']+row['slot'] for row in requests},
            'request slots changed')
    total=0;message_count=0;expert_receivers=set();zero_local=0
    for history in f['history']:
        wave=load(native/'graphs'/history['graph_id']/'wave.json');group=wave['rank_ids']
        require(group==list(range(128)),'wave omitted expert-only ranks')
        expected=Counter();expert_counts=Counter()
        for request in requests:
            raw=sources[request['file_id']];entry=request['first_decode_entry']+history['step'];src=request['rank_id']
            for layer,lid in enumerate(c['route_replay']['layer_ids']):
                for expert in raw[entry][str(lid)][0]:
                    dst=group[expert//(c['model']['experts']//len(group))]
                    expert_counts[layer,dst]+=1;expert_receivers.add(dst)
                    if src!=dst:
                        size=c['model']['hidden_size']*c['model']['activation_bytes']
                        expected[layer,'dispatch',src,dst]+=size
                        expected[layer,'combine',dst,src]+=size
        actual={(m['layer'],m['phase'],m['src'],m['dst']):m['bytes'] for m in wave['messages']}
        require(actual==dict(expected) and len(actual)==len(wave['messages']),'original-route byte matrix differs')
        for rank,tasks in wave['rank_nodes'].items():
            rank=int(rank)
            for task in tasks:
                if task['kind']!='COMP':continue
                if task['name'].endswith('.experts'):
                    layer=int(task['name'].split('.')[0][1:]);duration=expert_counts[layer,rank]*c['compute']['expert_ns_per_contribution']
                    require(task['duration_ns']==duration,'missing/fake expert compute')
                    require(event_time(events,wave['graph_id'],rank,task['id'],'callback')-
                            event_time(events,wave['graph_id'],rank,task['id'],'issue')==duration,
                            'declared expert work did not execute')
                elif rank not in source_ranks:
                    require(task['duration_ns']==0,'expert-only rank has fake local-token work');zero_local+=1
        total+=sum(expected.values());message_count+=len(actual)
    require(message_count==public['messages'] and total==public['remote_payload_bytes']==
            public['source_payload_bytes']==public['receiver_accepted_bytes'],'source/receiver logical bytes differ')
    require(all(q['state']=='OBSERVATION_ENDED' and len(q['intertoken_ns'])==2 for q in f['requests']),
            'source observation did not finish two steps')
    require(len(expert_receivers-source_ranks)>0,'no expert-only rank exercised')
    binding=load(root/'native-attempt.json')
    for name,digest in binding['input_hashes'].items():
        require(hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest,'native input changed')
    audit=load(root/'real_v2/audit.json')
    require(audit['passed'] and audit['network_runs']==0 and len(audit['sources'])==15,'real-source scope differs')
    reports=[dict(gpu_endpoints=row['gpu_endpoints'],ep_sizes=row['ep_sizes'],experts_per_rank=row['experts_per_rank'],
        independent_requests=row['independent_requests'],network_participant_ranks=len(row['network_participant_ranks']),
        expert_only_network_ranks=len(row['expert_only_network_ranks']),messages=row['messages'],logical_bytes=row['logical_bytes'],
        persistent_streams=row['persistent_streams'],max_planned_stream_bytes=row['max_planned_stream_bytes'],
        network_executed=False,independent_original_matrix_verified=row['independent_original_matrix_verified']) for row in audit['scales']]
    require([row['gpu_endpoints'] for row in reports]==[128,256,512],'wrong scale scope')
    summary=dict(passed=True,native_runs=1,native_gpu_endpoints=128,native_source_kind='synthetic_test',
        native_requests=4,messages=message_count,logical_bytes=total,packet_events=public['packet_events_checked'],
        verified_nodes=public['verified_nodes'],application_finished_ns=public['application_finished_ns'],
        expert_receiver_ranks=len(expert_receivers),expert_only_receivers=len(expert_receivers-source_ranks),
        zero_cost_local_token_nodes_checked=zero_local,real_source_offline_scales=reports,
        real_source_network_runs=0,new_algorithm_gain_measured=False,hardware_calibrated=False)
    with output.open('x') as stream:json.dump(summary,stream,indent=2);stream.write('\n')
    print(json.dumps(summary))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();verify(args.root.resolve(),args.output.resolve())
