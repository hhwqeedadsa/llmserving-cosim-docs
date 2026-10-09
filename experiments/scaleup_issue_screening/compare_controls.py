"""Compare predeclared E2 controls with one normal sealed execution.

Reports actual feedback and descriptive paired differences. It does not assign
queue causes, treat ranks as repetitions, or infer a hardware calibration range.
"""
import argparse
from pathlib import Path
import sys

from .analyze import distribution
from .compare import rows
from .workflow import CONFIG, digest, host, read, save


def same_workload(base, changed, base_rows, changed_rows):
    if base['requests']!=changed['requests'] or base['semantic_workload_sha256_by_layer']!=changed['semantic_workload_sha256_by_layer']:
        raise ValueError('Control changes request, route, context or placement workload')
    def index(values):
        result={(r['layer_id'],r['kind'],r['rank_id']):r for r in values}
        if len(result)!=len(values):raise ValueError('Duplicate participant record')
        return result
    left,right=index(base_rows),index(changed_rows)
    if set(left)!=set(right):raise ValueError('Participant coverage differs')
    if any(left[key]['workload']!=right[key]['workload'] for key in left):
        raise ValueError('Per-rank work differs')
    return left,right


def last_compute(directory, layer):
    values=[r['end_ns'] for r in rows(directory/'compute.jsonl') if r['layer_id']==layer and r['stage']=='ffn_residual']
    if len(values)!=128:raise ValueError('Expected all 128 ranks at final layer')
    return max(values)


def ports(directory):
    values=rows(directory/'ports.jsonl')
    result={(r['switch_id'],r['port_id']):r for r in values}
    if len(result)!=len(values) or any(r['observed'] is not True for r in values):
        raise ValueError('Invalid observed-port coverage')
    return result


def compare(root, output):
    root,output=Path(root),Path(output)
    if output.exists():raise ValueError('Control comparison output must be new')
    baseline=root/'g0-b2-baseline';base=read(baseline/'summary.json')
    base_rows=rows(baseline/'ranks.jsonl');base_ports=ports(baseline)
    finish=last_compute(baseline,8)
    result=[]
    for name in ('expert-half','expert-double','local-fence'):
        declared=read(CONFIG/'controls'/(name+'.json'))
        directory=root/declared['case_id'];changed=read(directory/'summary.json')
        audit=read(directory/'input-audit.json')
        if changed['case_id']!=declared['case_id'] or changed['control']!=declared or audit['control']!=declared:
            raise ValueError('Control identity or declaration mismatch')
        if not (audit['passed'] and digest(directory/'input-audit.json')==changed['input_audit_sha256'] and
                audit['baseline_case_sha256']==base['case_sha256'] and audit['control_case_sha256']==changed['case_sha256']):
            raise ValueError('Input audit is not bound to these executions')
        left,right=same_workload(base,changed,base_rows,rows(directory/'ranks.jsonl'))
        other_ports=ports(directory)
        if set(other_ports)!=set(base_ports):raise ValueError('Actual static port coverage differs')
        end=last_compute(directory,8)
        operations=[]
        for lid in (7,8):
            for kind in ('dispatch','combine'):
                keys=sorted(k for k in left if k[:2]==(lid,kind))
                b=next(o for o in base['operations'] if (o['layer_id'],o['kind'])==(lid,kind))
                c=next(o for o in changed['operations'] if (o['layer_id'],o['kind'])==(lid,kind))
                operations.append(dict(layer_id=lid,kind=kind,participants=len(keys),
                    baseline=dict(entry_spread_ns=b['relative_entry_ns']['spread'],median_duration_ns=b['duration_ns']['median'],
                        finish_spread_ns=b['relative_finish_ns']['spread'],last_finish_ns=b['last_finish_ns']),
                    control=dict(entry_spread_ns=c['relative_entry_ns']['spread'],median_duration_ns=c['duration_ns']['median'],
                        finish_spread_ns=c['relative_finish_ns']['spread'],last_finish_ns=c['last_finish_ns']),
                    paired_control_minus_baseline_ns={field:distribution([right[k][field]-left[k][field] for k in keys])
                        for field in ('start_ns','duration_ns','end_ns')},
                    baseline_last_finishing_ranks=b['last_finishing_ranks'],control_last_finishing_ranks=c['last_finishing_ranks']))
        result.append(dict(case_id=declared['case_id'],control=declared,
            source_summary_sha256=digest(directory/'summary.json'),input_audit_sha256=changed['input_audit_sha256'],
            last_layer_compute_finished_ns=end,compute_finish_delta_ns=end-finish,
            modeled_duration_reduction_percent=100*(finish-end)/finish,
            simulation_end_ns=changed['simulation_end_ns'],network_drain=changed['network_drain'],
            operations=operations,compute_relations=changed['compute_relations'],network=changed['network'],
            actual_static_port_coverage_equal=True,
            ports_with_changed_total_packet_bytes=sum(base_ports[k]['packet_bytes']!=other_ports[k]['packet_bytes'] for k in base_ports),
            ports_with_changed_queue_area=sum((base_ports[k]['voq_byte_ns'],base_ports[k]['fifo_byte_ns'])!=
                (other_ports[k]['voq_byte_ns'],other_ports[k]['fifo_byte_ns']) for k in base_ports),
            packet_path_equality_verified=False))
    save(output,dict(schema_version='issue-screening-e2-comparison/1',
        baseline_summary_sha256=digest(baseline/'summary.json'),baseline_last_layer_compute_finished_ns=finish,
        controls=result,independent_source_groups=1,rank_samples_are_independent=False,
        scope='model-internal one-factor total effects with actual completion feedback; not calibrated hardware gains',
        caveats=['same seed does not freeze packet paths','aggregate queue areas are not avoidable time',
            'no source-device queue or per-packet waiting attribution','only 256-request controls; 1024-request E1 observations are not control validation'],
        program_sha256=digest(Path(__file__))))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--analysis-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    a=p.parse_args()
    if a.worker:compare(a.analysis_root,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        report=probe([sys.executable,'-m','experiments.scaleup_issue_screening.compare_controls','--worker',
            '--analysis-root',str(a.analysis_root.resolve()),'--output',str(a.output.resolve())],
            str(a.output.resolve())+'-resources',**host(extra_roots=[a.analysis_root,a.output.resolve().parent]))
        if report['status']!='completed':raise SystemExit('Control comparison failed: '+report['status'])


if __name__=='__main__':main()
