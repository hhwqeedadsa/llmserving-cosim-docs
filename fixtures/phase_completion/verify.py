"""Verify real originals and phase ordering without using gate generation."""
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
    native=root/'native128';c=load(native/'input.json');p=load(native/'rdma.json')
    r=load(native/'result.json');f=load(native/'frontend.json');public=load(native/'verification.json')
    require(r['gpu_count']==128 and r['passed'] and r['drained'] and public['passed'],'native case did not pass')
    require(not any(k in p for k in ('data_loss','drop_once','faults')),'unexpected injected fault')
    require(c['phase_completion']==dict(mode='ep_transport_flush/1'),'wrong phase mode')
    manifest=load(native/c['route_replay']['manifest']['path']);originals={}
    hashes=set()
    for row in manifest['files']:
        path=native/row['file']['path'];digest=hashlib.sha256(path.read_bytes()).hexdigest()
        require(digest==row['file']['sha256'] and digest not in hashes,'changed/cloned original')
        hashes.add(digest);originals[row['file_id']]=load(path)
    requests=c['route_replay']['requests'];require(len(requests)==len(originals)==15,'wrong independent request count')
    require(len(f['requests'])==15 and all(q['state']=='OBSERVATION_ENDED' and len(q['intertoken_ns'])==2 for q in f['requests']),
            'real requests did not finish observed two steps')
    events=node_events(native);descriptors={m['id']:m for m in r['service_transport']['messages']}
    gates={(row['graph_id'],row['id']):row for row in r['service']['phase_completion']['gates']}
    require(len(gates)==8,'missing/duplicate effective gate')
    total_bytes=0;total_messages=0;checks=[]
    for history in f['history']:
        wave=load(native/'graphs'/history['graph_id']/'wave.json');group=wave['rank_ids']
        require(group==list(range(128)),'changed EP ranks')
        expected=Counter()
        for request in requests:
            raw=originals[request['file_id']];entry=request['first_decode_entry']+history['step'];src=request['rank_id']
            for layer,lid in enumerate(c['route_replay']['layer_ids']):
                for expert in raw[entry][str(lid)][0]:
                    dst=group[expert//2]
                    if src!=dst:
                        expected[layer,'dispatch',src,dst]+=14336
                        expected[layer,'combine',dst,src]+=14336
        actual={(m['layer'],m['phase'],m['src'],m['dst']):m['bytes'] for m in wave['messages']}
        require(actual==dict(expected) and len(actual)==len(wave['messages']),'original-route contribution matrix differs')
        total_bytes+=sum(expected.values());total_messages+=len(actual)
        for layer in range(2):
            for phase,consumer_name in (('dispatch','experts'),('combine','merge')):
                row=gates[wave['graph_id'],f'L{layer}.{phase}']
                require([m['rank'] for m in row['members']]==group,'gate omitted ranks')
                arrivals=[];callbacks=[];consumers=[]
                for rank in group:
                    tasks=wave['rank_nodes'][str(rank)]
                    gate=next(n for n in tasks if n['name']==f'L{layer}.{phase}.flush')
                    consumer=next(n for n in tasks if n['name']==f'L{layer}.{consumer_name}')
                    arrivals.append(event_time(events,wave['graph_id'],rank,gate['id'],'issue'))
                    callbacks.append(event_time(events,wave['graph_id'],rank,gate['id'],'callback'))
                    consumers.append(event_time(events,wave['graph_id'],rank,consumer['id'],'issue'))
                selected=[descriptors[m['id']] for m in wave['messages'] if m['layer']==layer and m['phase']==phase]
                ack=max(m['ack_ns'] for m in selected);accepted=max(m['receive_ready_ns'] for m in selected)
                require(max(arrivals)==row['released_ns'] and set(callbacks)=={max(arrivals)},'gate completion is not last actual arrival')
                require(accepted<=ack<=row['released_ns']<=min(consumers),'consumer crossed actual transfer completion')
                checks.append(dict(graph_id=wave['graph_id'],layer=layer,phase=phase,members=len(group),
                    last_receiver_ready_ns=accepted,last_source_ack_ns=ack,released_ns=row['released_ns'],
                    earliest_consumer_ns=min(consumers),arrival_spread_ns=max(arrivals)-min(arrivals)))
    require(total_messages==public['messages'] and total_bytes==public['remote_payload_bytes']==
            public['source_payload_bytes']==public['receiver_accepted_bytes'],'logical/source/receiver bytes differ')
    summary=dict(passed=True,network_runs=1,gpu_endpoints=128,ep_sizes=[128],experts_per_rank=2,
        independent_real_requests=15,source_kind=manifest['source']['kind'],remote_revision_verified=False,
        source_layers=[3,4],decode_entries=[1,2],messages=total_messages,logical_bytes=total_bytes,
        packet_events_checked=public['packet_events_checked'],verified_nodes=public['verified_nodes'],
        gates_checked=len(checks),gate_members_checked=sum(x['members'] for x in checks),phase_timing=checks,
        application_finished_ns=public['application_finished_ns'],network_drained_ns=public['network_drained_ns'],
        post_application_drain_ns=public['post_application_drain_ns'],
        complete_DeepEP=False,coordination_cost_modeled=False,hardware_calibrated=False,
        new_algorithm_gain_measured=False,network_injected_fault=False,
        scope='finite real expert choices, declared compute/deployment and necessary transport phase ordering')
    with output.open('x') as stream:json.dump(summary,stream,indent=2);stream.write('\n')
    print(json.dumps({k:summary[k] for k in ('passed','independent_real_requests','messages','logical_bytes','gates_checked','gate_members_checked')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args();verify(a.root.resolve(),a.output.resolve())
