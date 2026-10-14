"""Compare C1 descriptions only after exact equal-work and equal-budget checks."""
import argparse
from collections import Counter
from pathlib import Path
from .workflow import read,save,digest


def compare(left,right,left_ports,right_ports,output):
    a,b=read(left),read(right)
    if not a['passed'] or not b['passed'] or (a['mode'],b['mode'])!=('fifo','producer_round_robin'):
        raise ValueError('Expected successful FIFO/RR pair')
    invariant=('plan_files','executed_nodes','compute_cost_digest','request_range_digest','planned_transfer_digest',
               'bytes_by_role','random','effective_readback')
    for field in invariant:
        if a[field]!=b[field]:raise ValueError('Control changed invariant: '+field)
    for field in ('original_archive_sha256','slice_sha256','control_sha256','backend_sha256','queue_policy'):
        if a['selection'][field]!=b['selection'][field]:raise ValueError('Control input binding changed: '+field)
    ports=[]
    for review,path in [(a,left_ports),(b,right_ports)]:
        if digest(path)!=review['ports_sha256']:raise ValueError('Port summary identity mismatch')
        rows=read(path);index={(r['switch_id'],r['port_id']):r for r in rows}
        if len(index)!=len(rows):raise ValueError('Duplicate port')
        ports.append(index)
    ap,bp=ports
    if set(ap)!=set(bp):raise ValueError('Observed port coverage differs')
    def totals(rows):
        c=Counter()
        for r in rows.values():c.update({k:v for k,v in r.items() if k not in ('switch_id','port_id')})
        return dict(c)
    common=sorted({t['transfer_id'] for t in a['selected_status_waits']} & {t['transfer_id'] for t in b['selected_status_waits']})
    def statuses(value):return {r['transfer_id']:r for r in value['selected_status_waits']}
    aa,ba=statuses(a),statuses(b)
    original=[4295045018,4295045021,4295045024,4295045027,4295086585,4295086588,4295086591,4295086594]
    if not set(original)<=set(common):raise ValueError('Missing original status contributions')
    original_data=[4295045029,4295086595]
    def contributions(v):return {t['transfer_id']:t for t in v['contributions']}
    ac,bc=contributions(a),contributions(b)
    result=dict(schema_version='issue-screening-organization-effect/1',passed=True,
        baseline=dict(path=str(left),sha256=digest(left),manifest_sha256=a['manifest_sha256']),
        control=dict(path=str(right),sha256=digest(right),manifest_sha256=b['manifest_sha256']),
        invariant_fields=list(invariant),all_native_STORE_ranges_identical=a['request_range_digest'],
        all_native_initiators=a['effective_readback']['native_initiators'],
        original_status_contributions=[dict(transfer_id=tid,baseline=aa[tid],control=ba[tid],wait_change_ns=ba[tid]['wait_ns']-aa[tid]['wait_ns']) for tid in original],
        original_data_contributions=[dict(transfer_id=tid,baseline=ac[tid],control=bc[tid]) for tid in original_data],
        last_model_compute_end_ns=dict(baseline=a['last_model_compute_end_ns'],control=b['last_model_compute_end_ns']),
        modeled_end_reduction_percent=100*(a['last_model_compute_end_ns']-b['last_model_compute_end_ns'])/a['last_model_compute_end_ns'],
        model_layer_end_ns=dict(baseline=a['model_layer_end_ns'],control=b['model_layer_end_ns']),
        actual_last_consumers=dict(baseline=a['actual_last_consumers'],control=b['actual_last_consumers']),
        simulation_end_ns=dict(baseline=a['simulation_end_ns'],control=b['simulation_end_ns']),
        physical_packets=dict(baseline=a['network_drain'],control=b['network_drain']),
        port_totals=dict(baseline=totals(ap),control=totals(bp)),ports_compared=len(ap),
        ports_with_changed_packets=sum(ap[k].get('packets',0)!=bp[k].get('packets',0) for k in ap),
        ports_with_changed_wire_bytes=sum(ap[k].get('packet_bytes',0)!=bp[k].get('packet_bytes',0) for k in ap),
        ports_with_changed_business_byte_hops=sum(ap[k].get('business_data',0)!=bp[k].get('business_data',0) for k in ap),
        scope='One dependency-driven fixed-budget organization control; network feedback included; no hardware calibration or independent statistical repeat',
        program_sha256=digest(__file__))
    save(output,result)
    print(dict(passed=True,end=result['last_model_compute_end_ns'],reduction_percent=result['modeled_end_reduction_percent']))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline','control','baseline-ports','control-ports','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();compare(a.baseline,a.control,a.baseline_ports,a.control_ports,a.output)


if __name__=='__main__':main()
