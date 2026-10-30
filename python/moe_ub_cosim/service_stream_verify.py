"""Actual ASTRA graph and persistent interval/packet audit for both frontends."""
import json
from pathlib import Path

from .scaleout_rdma import checked_json
from .scaleout_limits import limits
from .scaleout_topology import require
from .scaleout_verify import node_events,event_time,verify_replay_requests
from .scaleout_service import make_wave,validate as validate_service
from .service_terminal import service_terminal
from .service_transport import message_preflight
from .service_stream_records import reconstruct


def verify(directory):
    directory=Path(directory).resolve()
    c=checked_json(directory/'input.json',2**20);t=checked_json(directory/'topology.json',8*2**20)
    p=checked_json(directory/'rdma.json',65536);r=checked_json(directory/'result.json',limits(p)['result_json_bytes'])
    f=checked_json(directory/'frontend.json',4*2**20)
    terminal=service_terminal(r,f,require_complete=True,config=c)
    simai=c.get('schema_version')=='moe-simai-service/1'
    if simai:
        from .simai_service import validate
        from .simai_frontend import verify_runtime
        n,planes,replay=validate(c,t,p,directory);receipt=verify_runtime(directory/'simai_runtime');service=c['service']
        require(f['scheduler_source']=='SimAI_Vidur_real_Orca_Cluster_Batch_Request' and
                f['simai_revision']==receipt['revision'] and all(v==0 for v in f['final_allocated_blocks'].values()),
                'actual SimAI identity or final KV state differs')
    else:
        from .scaleout_replay import Replay
        service=c;replay=Replay(c,directory) if 'route_replay' in c else None
        n,planes=validate_service(c,t,p,replay)
        require(f['scheduler_source']=='pinned_LLMServingSim_ConfiguredScheduler_initialization_only',
                'actual LLMServingSim scheduler identity differs')
        if replay:verify_replay_requests(c,replay,f)
    require(r['native_network_executed'] and r['gpu_count']==n,'native endpoint count differs')
    libs=[Path(x) for x in r['loaded_libraries'] if Path(x).name.startswith('libns3')]
    require(len(libs)>=5 and len({x.parent for x in libs})==1 and
            all(x.name.startswith('libns3.42-') and 'unified' not in x.name for x in libs),'mixed native libraries')
    a=r['service_transport'];native=a['native'];declared=service['transport']
    require(native['closed'] and native['physical_drained'] and
            native['recovery']['config']==declared['transport_recovery'] and
            native['dcqcn_reference']['config']==declared['dcqcn_reference'],'effective transport differs')
    observed={s['id']:s for s in native['streams']}
    require(set(observed)=={s['id'] for s in declared['streams']},'missing or extra physical stream')
    for stream in declared['streams']:
        require(all(observed[stream['id']][k]==v for k,v in stream.items()),'physical stream binding differs')
    callbacks={m['id']:m for m in r['service']['message_callbacks']};messages={m['id']:m for m in a['messages']}
    helper_messages={mid for attempt in r['service'].get('inflight_helpers',{}).get('attempts',[])
                     for mid in (attempt['candidate']['input_message'],attempt['candidate']['result_message'])}
    require(len(callbacks)==len(r['service']['message_callbacks']) and len(messages)==len(a['messages']) and
            set(callbacks)==set(messages)-helper_messages and helper_messages<=set(messages),
            'duplicate or missing ASTRA callbacks')
    checker=message_preflight(t,p,service);events=node_events(directory);waves=[];ids=set();count=0
    for history in f['history']:
        index,step=history['group_index'],history['step'];group=service['parallel']['groups'][index]
        wave=checked_json(directory/'graphs'/history['graph_id']/'wave.json',16*2**20)
        expected=make_wave(service,group,index,step,planes,replay)
        require(json.loads(json.dumps(expected))==wave,'saved graph differs from selected source/declared template')
        from .token_transport import verify_wave
        verify_wave(wave,service)
        checker.accept_wave(wave,history['start_ns']);waves.append(wave)
        for m in wave['messages']:
            identity=m['id'];require(identity not in ids and identity in messages,'missing or duplicate submitted message')
            ids.add(identity);actual=messages[identity];cb=callbacks[identity]
            require(all(actual[k]==m[k] for k in m),'submitted message differs from generated work')
            require(history['start_ns']<=actual['ready_ns']<=actual['published_ns']<=actual['receive_ready_ns']<=
                    actual['ack_ns']<=history['finished_ns'],'message lifecycle order violated')
            require(cb['send_post_ns']==actual['ready_ns'] and cb['send_callback_ns']==actual['ack_ns'] and
                    cb['recv_callback_ns']==max(cb['recv_post_ns'],actual['receive_ready_ns']),
                    'ASTRA callbacks differ from actual acceptance/source ACK')
        for rank,tasks in wave['rank_nodes'].items():
            rank=int(rank)
            for task in tasks:
                start=event_time(events,wave['graph_id'],rank,task['id'],'issue')
                end=event_time(events,wave['graph_id'],rank,task['id'],'callback')
                require(history['start_ns']<=start<=end<=history['finished_ns'],'node escaped actual wave')
                for dep in task['deps']:
                    require(start>=event_time(events,wave['graph_id'],rank,dep,'callback'),'ASTRA dependency violated')
                if task['kind']=='COMP':
                    require(end-start>=task['duration_ns'] if 'inflight_helpers' in service else end-start==task['duration_ns'],
                            'declared compute did not execute')
                elif task['kind']=='GATE':pass  # Waiting time is checked by the full EP join below.
                else:
                    cb=callbacks[task['message']['id']];prefix='send' if task['kind']=='SEND' else 'recv'
                    require(start==cb[prefix+'_post_ns'] and end==cb[prefix+'_callback_ns'],'ASTRA issue/callback differs')
                count+=1
    require(ids==set(callbacks)==set(messages)-helper_messages and len(ids)+len(helper_messages)==a['registered_messages'],
            'extra/missing actual service work')
    from .phase_completion import actual as verify_phases
    verify_phases(waves,service,r,events)
    for index in range(len(service['parallel']['groups'])):
        history=sorted((h for h in f['history'] if h['group_index']==index),key=lambda h:h['step'])
        if not simai and replay and replay.request_layout=='sparse_active_groups/1' and index not in replay.active_group_indices:
            require(not history,'inactive EP group executed undeclared work')
            continue
        require([h['step'] for h in history]==list(range(service['workload']['decode_steps'])) and
                history[0]['start_ns']==0 and all(b['start_ns']==a['finished_ns'] for a,b in zip(history,history[1:])),
                'next batch did not follow actual preceding completion')
    require(all(q['state'] in ('FINISHED','OBSERVATION_ENDED') for q in f['requests']),'frontend requests did not finish')
    chosen=next((w,m) for w in waves for m in w['messages'] if m['phase']=='dispatch')
    audit,*_=reconstruct(r,waves,chosen[0]['graph_id'],chosen[1]['layer'],'dispatch')
    report=dict(passed=True,transport='astra_persistent_streams/1',frontend='actual_SimAI_Vidur' if simai else 'actual_LLMServingSim',
        gpu_count=n,messages=len(ids),remote_payload_bytes=audit['submitted_logical_bytes'],verified_nodes=count,
        waves=len(waves),finish_ns=r['finish_ns'],packet_events_checked=len(r['packet_capture']['events']),
        source_payload_bytes=audit['source_tx_payload_bytes'],receiver_accepted_bytes=audit['accepted_bytes'],
        hardware_calibrated=False,scope='actual ASTRA scheduling and persistent message intervals; finite service; not calibrated hardware',**terminal)
    from .scaleout_switch_drops import attach
    return attach(report,r,t,p)
