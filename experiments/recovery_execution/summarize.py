"""Summarize audited matched pairs; port service sums are not wall-clock time."""
import argparse
import json
from pathlib import Path


def summarize(audit):
    summary=[]; cases=audit['cases']
    for request in sorted({r['request'] for r in cases}):
        rr=[r for r in cases if r['request']==request]
        whole=next(r for r in rr if r['fault']=='data_tail' and r['strategy']=='whole_token')
        retain=next(r for r in rr if r['fault']=='data_tail' and r['strategy']=='owner_retention')
        assert whole['owners']==retain['owners']
        for owner in retain['owners']:
            assert whole['paths'][str(2*owner)]==retain['paths'][str(2*owner)]
            assert whole['paths'][str(2*owner)]==whole['paths'][str(2*(owner+4))]
            if owner in retain['replay_owners']:
                assert retain['paths'][str(2*owner)]==retain['paths'][str(2*(owner+4))]
                assert whole['paths'][str(2*(owner+4))]==retain['paths'][str(2*(owner+4))]
        summary.append(dict(request=request,owners=len(whole['owners']),
            whole_retry_bytes=whole['recovery_payload_tx_bytes'],retention_retry_bytes=retain['recovery_payload_tx_bytes'],
            retry_reduction_fraction=1-retain['recovery_payload_tx_bytes']/whole['recovery_payload_tx_bytes'],
            whole_ready_ns=whole['fragment_ready_ns'],retention_ready_ns=retain['fragment_ready_ns'],
            whole_all_port_model_tx_bytes=sum(p['bytes'] for p in whole['port_services']),
            retention_all_port_model_tx_bytes=sum(p['bytes'] for p in retain['port_services']),
            all_port_service_ns_reduction=sum(p['service_ns'] for p in whole['port_services'])-
                                          sum(p['service_ns'] for p in retain['port_services'])))
    retention=[r for r in cases if r['strategy']=='owner_retention']
    ack_loss=[r for r in cases if r['fault']=='ack_tail']
    return dict(data_tail_pairs=summary,
                retention_equivalent_conditions=sum(r['owner_plan_equivalent_to_operation_replay'] for r in retention),
                total_retention_conditions=len(retention),
                no_fresh_output_retry_in_all_8_ack_loss_conditions=len(ack_loss)==8 and all(not r['replay_owners'] for r in ack_loss),
                route_changes_explain_pair_difference=False,independent_network_runs=len(cases),
                raw_request_count=len(summary),source_packets=sum(r['source_packets'] for r in cases),
                target_effects=sum(r['accepted_target_effects'] for r in cases))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audit',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();result=summarize(json.loads(a.audit.read_text()))
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(pairs=len(result['data_tail_pairs']),equivalent=result['retention_equivalent_conditions'])))


if __name__=='__main__':main()
