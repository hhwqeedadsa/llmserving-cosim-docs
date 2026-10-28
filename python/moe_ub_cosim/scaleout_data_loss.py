"""Evaluation-only replay of destination packet loss, including failed services."""
from .scaleout_topology import require


def audit(result):
    if 'data_loss' not in result:return dict(available=False)
    record=result['data_loss'];c=record['configuration'];cap=result.get('packet_capture',{})
    require(cap.get('complete') and cap.get('omitted_events')==0,'data loss audit needs complete capture')
    selection=cap['selection']
    require(selection['start_ns']==0 and selection['end_ns']>result['finish_ns'] and not selection['nodes'],
            'data loss audit needs all-node full-time capture')
    selected=set(c['nodes']);eligible=window=dropped=affected=0;messages=set()
    for row in cap['events']:
        p=dict(zip(cap['columns'],row));node=p['node']
        if node>=result['gpu_count'] or (selected and node not in selected) or p['ip_protocol']!=17 or p['event'] not in (1,2):continue
        eligible+=1;expected=False
        if c['start_ns']<=p['time_ns']<c['end_ns']:
            expected=(window+c['offset'])%c['denominator']<c['numerator'];window+=1
        require((p['event']==2)==expected,'actual receive/drop differs from periodic loss configuration')
        if expected:dropped+=1;messages.add(p['message_id'])
    require((eligible,window,dropped)==tuple(record[k] for k in ('eligible_destination_data_packets','window_packets','dropped_packets')),
            'packet replay differs from loss counters')
    require(result['fault_drops']==dropped,'unexpected other fault source')
    return dict(available=True,passed=True,eligible_data_packets=eligible,window_data_packets=window,dropped_packets=dropped,
        window_loss_fraction=dropped/window if window else None,whole_observed_loss_fraction=dropped/eligible if eligible else None,
        affected_message_count=len(messages),scope='actual destination data arrivals including retransmissions; deterministic artificial loss; no congestion causality inference')


def check(directory):
    from pathlib import Path
    from .scaleout_rdma import checked_json
    from .scaleout_limits import limits
    directory=Path(directory);protocol=checked_json(directory/'rdma.json',65536)
    result=checked_json(directory/'result.json',limits(protocol)['result_json_bytes'])
    require(protocol.get('data_loss')==result.get('data_loss',{}).get('configuration'),'loss input/result configuration differs')
    return audit(result)
