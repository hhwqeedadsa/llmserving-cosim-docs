"""Small reviewable result summary; consumes analysis outputs only."""
import argparse
from pathlib import Path
from experiments.reordering_baseline.stage import load, save


def main():
    p = argparse.ArgumentParser()
    for k in ('analysis', 'train', 'output'): p.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args(); assert not a.output.exists(); r = load(a.analysis); trains = load(a.train)
    rows = {}
    for name, c in r['cases'].items():
        rows[name] = dict(application_completion_ns=c['group_completion_ns'],
            source_payload_bytes=c['tx_payload_bytes'], unique_payload_bytes=c['unique_payload_bytes'],
            repeated_payload_bytes=c['tx_payload_bytes']-c['unique_payload_bytes'])
    def inversion(mode): return rows[mode+'-live-instant']['application_completion_ns']-rows[mode+'-live-delayed']['application_completion_ns']
    pair = next(p for p in r['contrasts'] if p['before'] == 'grace-live-delayed' and p['after'] == 'grace-live-instant')
    selected = {str(w['work']): {k: w[k] for k in ('work', 'name', 'ready_delta_ns',
        'receive_delta_ns', 'ack_delta_ns', 'witness_send_after_ready_delta_ns',
        'witness_queue_delta_ns', 'witness_transit_delta_ns', 'final_declared_frontier_events')}
        for w in pair['writes'] if w['work'] in (10, 24, 99, 123)}
    save(a.output, dict(schema_version='reordering-baseline-summary/1',
        platform_commit='fa6f00173e6fbb320e83b368c85e9e835c8ba959',
        new_networks=3, immutable_saved_baselines=3, cases=rows,
        before_inversion_ns=inversion('immediate'), after_inversion_ns=inversion('grace'),
        fault_increment_ns={c: rows['grace-'+c]['application_completion_ns']-rows['grace-healthy']['application_completion_ns']
                            for c in ('live-delayed', 'live-instant')},
        stream12_repeat_outcomes={name: r['mechanisms'][name]['repeated_payload_by_initial_copy_outcome'].get('12', {})
                                 for name in r['cases']}, grace=r['grace'],
        selected_paired_write_accounting=selected,
        selected_source_train={name: {k: v[k] for k in ('first_tx_ns', 'last_tx_ns', 'source_train_span_ns',
            'adjusted_eligibility_intervals_ns', 'after_eligibility_ns',
            'physical_service_after_eligibility_ns', 'idle_after_eligibility_ns')}
            for name, v in trains['cases'].items()},
        verdict='classical grace removes old stream12 duplicate train, improves both fault cases, but does not eliminate timing inversion',
        research_implication='reject attribution solely to stream12 repeats; validate source eligibility/wakeup fidelity before algorithm claims',
        scope=r['scope'], warning=r['warning']))


if __name__ == '__main__': main()
