"""Audit the final compute/ACK/graph lifecycle in two saved functional cases.

This is model diagnosis, not a production workload or an optimization result.
Does not execute ns-3, alter completion semantics, or import test drivers.
"""
import argparse
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.scaleout_verify import node_events,event_time
from moe_ub_cosim.scaleout_rdma import checked_json
from moe_ub_cosim.service_terminal import service_terminal

ROOT=Path(__file__).resolve().parents[3]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(directory):
    directory=directory.resolve()
    result=checked_json(directory/'result.json',16*2**20)
    frontend=checked_json(directory/'frontend.json',2**20)
    config=checked_json(directory/'input.json',2**20)
    wave=checked_json(directory/'graphs/g0.w0/wave.json',2**20)
    events=node_events(directory)
    if len(frontend['history'])!=1 or config['model']['layers']!=1 or config['workload']['decode_steps']!=1:
        raise ValueError('diagnostic requires one finite layer/step')
    merge={};all_callbacks=[];compute_callbacks=[];sends=[]
    for rank,tasks in wave['rank_nodes'].items():
        rank=int(rank)
        for task in tasks:
            start=event_time(events,wave['graph_id'],rank,task['id'],'issue')
            finish=event_time(events,wave['graph_id'],rank,task['id'],'callback')
            if start>finish:raise ValueError('callback precedes issue')
            for dependency in task['deps']:
                if start<event_time(events,wave['graph_id'],rank,dependency,'callback'):
                    raise ValueError('dependency did not finish before issue')
            all_callbacks.append(finish)
            if task['kind']=='COMP':
                if finish-start!=task['duration_ns']:raise ValueError('declared compute differs')
                compute_callbacks.append(finish)
                if task['name']=='L0.merge':merge[rank]=dict(node_id=task['id'],finish_ns=finish)
            if task['kind']=='SEND':sends.append(dict(rank=rank,node_id=task['id'],message_id=task['message']['id'],finish_ns=finish))
    if set(merge)!=set(wave['rank_ids']):raise ValueError('missing final compute')
    terminal=service_terminal(result,frontend,require_complete=True)
    final_compute=max(row['finish_ns'] for row in merge.values())
    graph=terminal['application_finished_ns'];last_send=max(sends,key=lambda s:s['finish_ns'])
    callback=next(row for row in result['service']['message_callbacks'] if row['id']==last_send['message_id'])
    native=next(row for row in result['service_transport']['messages'] if row['id']==last_send['message_id'])
    if not (final_compute==max(compute_callbacks)<=graph==max(all_callbacks)==last_send['finish_ns']==callback['send_callback_ns']==native['ack_ns']):
        raise ValueError('graph is not retired by the observed final source ACK')
    return dict(final_merge_per_rank=merge,last_compute_callback_ns=final_compute,graph_finished_ns=graph,
        source_ack_gating_graph=last_send,last_message_receive_ns=native['receive_ready_ns'],
        last_message_ack_ns=native['ack_ns'],post_compute_graph_retirement_ns=graph-final_compute,
        terminal=terminal,input_sha256={file:sha(directory/file) for file in
            ('input.json','topology.json','rdma.json','result.json','frontend.json','graphs/g0.w0/wave.json','log/astra_nodes.log')},
        wave=wave,config=config)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ack-run',type=Path,required=True)
    parser.add_argument('--data-run',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('output exists')
    ack,data=audit(args.ack_run),audit(args.data_run)
    if ack.pop('wave')!=data.pop('wave') or ack.pop('config')!=data.pop('config'):
        raise ValueError('cases changed declared work rather than just faults')
    if ack['graph_finished_ns']!=data['graph_finished_ns']:raise ValueError('graph equality no longer holds')
    sources=['backend/rdma/service.cc','backend/rdma/astra_adapter.cc',
             'third_party/LLMServingSim/astra-sim/astra-sim/workload/Workload.cc']
    evidence=dict(schema_version='moe-astra-tail-lifecycle-diagnosis/1',passed=True,
        scope='synthetic single-layer/single-step platform counterexample; not production performance or achievable gains',
        new_network_runs=0,completion_definition_changed=False,ack_loss=ack,data_loss=data,
        identical_declared_graphs=True,identical_declared_service=True,
        different_final_compute_callback_ns=data['last_compute_callback_ns']-ack['last_compute_callback_ns'],
        source_sha256={path:sha(ROOT/path) for path in sources},
        limitations=['merge callbacks are declared model computation, not GPU-visible results',
          'no flag/notification or buffer-reuse semantics of a production library validated here',
          'removing ACK wait would change resource/lifetime constraints; no optimized run performed'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as stream:json.dump(evidence,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(dict(ack_post_compute_ns=ack['post_compute_graph_retirement_ns'],
        data_post_compute_ns=data['post_compute_graph_retirement_ns'],
        graph_finished_ns=ack['graph_finished_ns'],new_network_runs=0)))


if __name__=='__main__':main()
