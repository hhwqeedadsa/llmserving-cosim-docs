"""Closed user configuration for fixed and adaptive source-local recovery."""
from .scaleout_topology import require,fields,integer

def validate(recovery,max_sim_ns):
    require(isinstance(recovery,dict),'recovery must be an object')
    require(recovery.get('mode') in ('native','go_back_n'),'unknown recovery mode')
    keys='mode' if recovery['mode']=='native' else 'mode initial_rto_ns max_rto_ns max_retries max_events'
    if recovery['mode']=='go_back_n' and 'rtt_estimator' in recovery:keys+=' rtt_estimator'
    fields(recovery,keys,'recovery')
    if recovery['mode']=='native':return
    integer(recovery['initial_rto_ns'],'initial recovery RTO',1,max_sim_ns)
    integer(recovery['max_rto_ns'],'maximum recovery RTO',recovery['initial_rto_ns'],1000000000)
    integer(recovery['max_retries'],'recovery retries without ACK progress',1,16)
    integer(recovery['max_events'],'recovery capture events',0,100000)
    if 'rtt_estimator' in recovery:
        r=recovery['rtt_estimator'];require(isinstance(r,dict),'RTT estimator must be an object')
        fields(r,'kind min_rto_ns granularity_ns','RTT estimator')
        require(r['kind']=='karn','only explicit conservative Karn RTT sampling is supported')
        integer(r['min_rto_ns'],'minimum adaptive RTO',1,recovery['initial_rto_ns'])
        integer(r['granularity_ns'],'RTT clock granularity',1,recovery['max_rto_ns'])
