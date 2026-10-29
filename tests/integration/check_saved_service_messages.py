"""Re-audit saved legacy service work and feedback; never starts a simulator.

Accepts native service run directories, including failed ones. Each result must
have complete capture. The first Dispatch selects an output slice; reconstruction
still cross-checks every generated/submitted message, not only the chosen slice.
"""
import argparse
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.scaleout_arrivals import reconstruct
from moe_ub_cosim.scaleout_messages import MessagePreflight
from moe_ub_cosim.scaleout_rdma import checked_json
from moe_ub_cosim.scaleout_topology import require


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(directory):
    directory=Path(directory)
    result=checked_json(directory/'result.json',128*2**20)
    topology=checked_json(directory/'topology.json',8*2**20)
    protocol=checked_json(directory/'rdma.json',65536)
    paths=sorted((directory/'graphs').glob('*/wave.json'))
    require(paths and len(paths)<=1024 and sum(p.stat().st_size for p in paths)<=64*2**20,
            'saved wave byte/count budget exceeded')
    waves=[checked_json(path,64*2**20) for path in paths]
    require(len(result['packet_capture']['events'])<=1000000,'packet audit limit exceeded')
    checker=MessagePreflight(topology,protocol)
    for wave in waves:checker.accept_wave(wave,0)
    selected=next((w,m) for w in waves for m in w['messages'] if m['phase']=='dispatch')
    wave,message=selected
    summary,rows,_,_,_=reconstruct(result,waves,wave['graph_id'],message['layer'],'dispatch')
    callbacks={row['id']:row for row in result['service']['message_callbacks']}
    require(len(callbacks)==len(result['service']['message_callbacks']),'repeated message callback record')
    actual={row['id']:row for row in result['messages']}
    for row in rows:
        if not row['submitted']:continue
        m=actual[row['message_id']];cb=callbacks[m['id']]
        require(cb['send_post_ns']==m['ready_ns'],'send post differs from actual submission')
        require(cb['send_callback_ns']==m['sender_resource_released_ns'],
                'send completion differs from source release')
        if row['ack_ns'] is not None:
            require(row['data_ready_ns']<=row['ack_ns']<=m['sender_resource_released_ns'],
                    'source release precedes independent confirmation')
    return dict(run=str(directory),network_executed_in_this_check=False,passed=True,
        original_service_passed=result['passed'],finish_ns=result['finish_ns'],
        generated_messages=len(rows),submitted_messages=len(actual),
        independently_confirmed_messages=sum(row['ack_ns'] is not None for row in rows),
        packet_events_checked=len(result['packet_capture']['events']),
        slice=summary['slice'],input_sha256={str(path.relative_to(directory)):digest(path)
            for path in [directory/'result.json',directory/'topology.json',directory/'rdma.json',*paths]})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,action='append',required=True)
    parser.add_argument('--output',type=Path,required=True,help='New compact JSON; no overwrite')
    args=parser.parse_args()
    output=dict(schema_version='moe-saved-service-message-audit/1',network_runs=0,
                runs=[check(path) for path in args.run])
    with args.output.open('x') as stream:
        json.dump(output,stream,ensure_ascii=False,indent=2);stream.write('\n')
