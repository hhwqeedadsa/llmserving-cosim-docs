"""Independent seeded-byte and queue/drop/MMU conservation audit, stdlib only."""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path


def check(ok,why):
    if not ok:raise AssertionError(why)


def audit(result,tamper=None):
    r=deepcopy(result)
    if tamper=='changed_occupancy':r['after_take_down']['egress'][3]-=1000
    if tamper=='missing_drop':r['queue_drop_events'].pop()
    if tamper=='forged_phy_drop':r['phy_rx_drop_events'].append(dict(uid=r['late_receive_uid'],bytes=64))
    check(r['simulator_run_called'] is False and r['network_flows_executed']==0 and r['manually_seeded_state'],'probe scope')
    seeds=r['seeds'];check(len(seeds)==15 and len({s['uid'] for s in seeds})==15,'seed identity')
    expected=Counter();control=0
    for s in seeds:
        check(s['queue'] in (0,2,3) and s['bytes']==(96 if s['queue']==0 else 1000),'seed shape')
        if s['queue']:expected[s['queue']]+=s['bytes']
        else:control+=s['bytes']
    check(expected=={2:6000,3:8000} and control==96 and r['reserve_bytes']==4096,'seed totals')
    by_id={s['uid']:s for s in seeds};drops=r['queue_drop_events']
    check(len(drops)==15 and {s['uid'] for s in drops}==set(by_id),'queue drops coverage')
    check(all(e==by_id[e['uid']] for e in drops),'drop byte/priority identity')
    check(not r['phy_rx_drop_events'] and not r['phy_tx_events'],'unexpected PhyRxDrop or TX')
    check(r['all_qbb_drop_events']==drops+[dict(uid=r['late_receive_uid'],bytes=64,queue=0)],'down-port receive trace')
    occupancy=[expected[q] for q in range(8)];zero=[0]*8
    shared=sum(max(b-r['reserve_bytes'],0) for b in occupancy)
    check(shared==5808,'shared oracle')
    for name in ('before','after_take_down','after_down_receive'):
        state=r[name]
        check(state['ingress']==state['egress']==occupancy and state['headroom_used']==zero and state['shared_used_bytes']==shared,'MMU occupancy evidence')
        check(state['queued_bytes']==(14096 if name=='before' else 0),'queue byte total')
        check(state['is_link_up']==(name=='before'),'link state transition')
    released=r['after_explicit_release']
    check(released['ingress']==released['egress']==released['headroom_used']==zero and released['shared_used_bytes']==released['queued_bytes']==0 and not released['is_link_up'],'explicit accounting control')
    return dict(schema_version='native-link-down-independent-audit/1',seeded_data_bytes=14000,seeded_control_bytes=96,queue_drops=15,
        down_port_receive_drops=1,phy_rx_drops=0,source_transmissions=0,retained_ingress_bytes=14000,retained_egress_bytes=14000,retained_shared_bytes=5808,
        native_take_down_leaves_mmu_occupancy=True,qbb_and_phy_drop_are_distinct=True,explicit_release_restores_conservation=True,
        platform_link_failure_support_added=False,network_recovery_or_performance_proven=False,manually_seeded_state=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--result',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    raw=json.loads(a.result.read_text());report=audit(raw);tampering=[]
    for kind in ('changed_occupancy','missing_drop','forged_phy_drop'):
        try:audit(raw,kind)
        except AssertionError as e:tampering.append(dict(kind=kind,rejected=True,reason=str(e)))
        else:raise AssertionError('tampering accepted: '+kind)
    report['tampering']=tampering;a.output.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':main()
