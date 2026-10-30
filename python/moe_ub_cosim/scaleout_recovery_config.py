"""Closed user configuration for fixed and adaptive source-local recovery."""
from .scaleout_topology import require,fields,integer

def validate(recovery,max_sim_ns):
    require(isinstance(recovery,dict),'recovery must be an object')
    require(recovery.get('mode') in ('native','go_back_n'),'unknown recovery mode')
    keys='mode' if recovery['mode']=='native' else 'mode initial_rto_ns max_rto_ns max_retries max_events'
    if recovery['mode']=='go_back_n' and 'rtt_estimator' in recovery:keys+=' rtt_estimator'
    if recovery['mode']=='go_back_n' and 'history' in recovery:keys+=' history'
    if recovery['mode']=='go_back_n' and 'terminal_snapshot' in recovery:keys+=' terminal_snapshot'
    fields(recovery,keys,'recovery')
    if recovery['mode']=='native':return
    if 'terminal_snapshot' in recovery:
        require(type(recovery['terminal_snapshot']) is bool,'terminal recovery snapshot must be boolean')
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
    if 'history' in recovery:
        require('rtt_estimator' in recovery,'recovery history requires explicit RTT estimator')
        h=recovery['history'];require(isinstance(h,dict),'recovery history must be an object')
        fields(h,'max_entries_per_source max_age_ns','source recovery history')
        integer(h['max_entries_per_source'],'recovery history capacity per source',1,256)
        integer(h['max_age_ns'],'recovery evidence age',1,1000000000)
