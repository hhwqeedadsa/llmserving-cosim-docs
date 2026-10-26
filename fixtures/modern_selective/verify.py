"""Independent application/frame auditor plus explicit recovery identity checks."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import struct
from fixtures.native_callback_batches.application import application_audit
from fixtures.native_callback_batches.verify import delivery


def load(p): return json.loads(p.read_text())


def verify(root,overrides=None):
    def read(name):return overrides[name] if overrides and name in overrides else load(root/name)
    r=read('result.json');initial=read('initial.json');config=read('input.json')
    expected=config['provider']
    assert initial['transport_recovery']==expected['transport_recovery']
    assert initial['dcqcn_reference']==expected['dcqcn_reference']
    assert initial['transport_recovery']['mode']=='selective_repeat/1'
    assert r['passed'] and r['drained'] and r['session']['finished']
    transport=r['transactions']['transport']
    recovery=transport['recovery']
    assert recovery['config']==expected['transport_recovery']
    assert recovery['complete'] and not recovery['failed'] and not recovery['omitted_events']
    selective=transport['selective_repeat']
    assert transport['dcqcn_reference']['config']==expected['dcqcn_reference']
    feedback=[e for e in selective['events'] if e['kind']=='feedback_received']
    generated={e['packet_uid']:e for e in selective['events'] if e['kind']=='feedback_generated'}
    assert feedback
    for e in feedback:
        magic,begin,end,valid=struct.unpack('!IIII',bytes.fromhex(e['extension_hex']))
        assert magic==0x4d535231 and valid in (0,1)
        assert (begin,end,bool(valid))==(e['sack_begin'],e['sack_end'],e['sack_valid'])
        g=generated[e['packet_uid']]
        assert g['time_ns']<=e['time_ns']
        assert all(g[k]==e[k] for k in ('stream','cumulative_ack','extension_hex'))
    assert all(s['prefix_complete'] and not s['failed'] and s['acknowledged_bytes']==s['published_bytes']
               for s in recovery['states'])
    published={s['id']:s['published_bytes'] for s in transport['streams']}
    # Recovery timers are created only when a direction first publishes data;
    # response directions with no fetch_add remain valid empty streams.
    assert published and {s['stream'] for s in recovery['states']}=={i for i,n in published.items() if n}
    assert {s['stream'] for s in selective['states']}==set(published)
    for s in selective['states']:
        assert s['receiver_prefix']==published[s['stream']] and s['reorder_bytes']==0 and not s['held_ranges']
    # Existing independent auditors decode actual source frames, memory effects,
    # replies, leases, signals and every original modern operation/dependency.
    app=application_audit(root,overrides)
    # A complete capture alone does not constitute a full physical-path audit.
    app['full_physical_path_or_ack_audit_complete']=False
    callbacks=delivery(root,overrides)
    faults=read('protocol.json').get('faults',[])
    expected_drops=sum(f['count'] for f in faults)
    assert r['fault_drops']==expected_drops
    if expected_drops:assert app['retransmitted_source_packets']>0
    # Recoveries are checked from actual results, never inferred from configuration.
    return dict(passed=True,selective_enabled=True,application=app,delivery=callbacks,
        configured_faults=faults,actual_fault_drops=r['fault_drops'],actual_feedback_received=len(feedback),
        recovery={k:v for k,v in recovery.items() if k not in ('events','receiver_events')},
        selective_summary={k:v for k,v in transport['selective_repeat'].items() if k!='events'},
        capture_complete=r['packet_capture']['complete'],omitted_events=r['packet_capture']['omitted_events'],
        full_wire_reconstruction_claimed=False,finish_ns=r['finish_ns'])


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();report=verify(a.run)
    a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
