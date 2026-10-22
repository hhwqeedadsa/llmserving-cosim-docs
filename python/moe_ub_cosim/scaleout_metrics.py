"""Offline evaluation of passive queue summaries and disjoint message stages."""
from .scaleout_topology import require


def phase_intervals(flow, finish_ns):
    if 'admission_granted_ns' not in flow:
        return dict(available=False,reason='no_RDMA_lifecycle_boundaries')
    boundaries=[flow['ready_ns'],flow['admission_granted_ns'],flow['first_tx_ns'],
        flow['receive_ready_ns'],flow['ack_ns'],flow['sender_resource_released_ns']]
    names=['software_admission','sender_to_first_packet','data_delivery','confirmation_tail','sender_cleanup']
    durations={};open_stage=None;previous=-1;missing=False
    for value in boundaries:
        if value is None:missing=True;continue
        require(not missing and type(value) is int and previous<=value<=finish_ns,
                'message lifecycle boundary is missing or out of order')
        previous=value
    for name,start,end in zip(names,boundaries,boundaries[1:]):
        durations[name]=None if end is None else end-start
        if start is not None and end is None:open_stage=dict(name=name,elapsed_ns=finish_ns-start)
    complete=boundaries[-1] is not None
    if complete:require(sum(durations.values())==boundaries[-1]-boundaries[0],'stage durations do not telescope')
    progress=flow.get('sender_progress')
    if progress:
        una,nxt=progress['snd_una_bytes'],progress['snd_nxt_bytes']
        require((una is None and nxt is None and boundaries[1] is None) or
                (type(una) is int and type(nxt) is int and 0<=una<=nxt<=flow['bytes']),
                'invalid source protocol sequence snapshot')
    return dict(available=True,complete=complete,durations_ns=durations,open_stage=open_stage,
        scope='Disjoint elapsed stages, not queue delay attribution; data delivery includes serialization, propagation and recovery.')


def network_summary(result):
    rows=[p for p in result['ports'] if 'queue_metrics' in p]
    if 'queue_metrics_scope' not in result:
        require(not rows,'queue metrics lack scope')
        return dict(available=False)
    require(rows,'queue metrics have no switch ports')
    for p in rows:
        m=p['queue_metrics']
        require(all(type(m[k]) is int and m[k]>=0 for k in ('area_byte_ns','peak_bytes','final_bytes','changes')),
                'invalid passive queue metrics')
        require(m['final_bytes']<=m['peak_bytes'] and m['area_byte_ns']<=m['peak_bytes']*result['finish_ns'],
                'passive queue integral outside physical bounds')
        if result['passed']:require(m['final_bytes']==0,'successful run retained BEgressQueue bytes')
    return dict(available=True,scope=result['queue_metrics_scope'],switch_ports=len(rows),
        area_byte_ns=sum(p['queue_metrics']['area_byte_ns'] for p in rows),
        peak_port_bytes=max(p['queue_metrics']['peak_bytes'] for p in rows),
        final_bytes=sum(p['queue_metrics']['final_bytes'] for p in rows),
        queue_changes=sum(p['queue_metrics']['changes'] for p in rows))
