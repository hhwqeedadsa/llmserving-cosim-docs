"""Evaluate separately saved bounds against private evidence; no backend runs."""
import argparse
from collections import Counter
from pathlib import Path
import sys

from . import network_bounds as bounds
from .workflow import command, digest, host, read, save

TRUTH_SCOPES=('known_waiting_operations','complete_fifo_operations','partial_arrival_to_fifo_operations')


def evaluate(data, predictions, truth):
    windows={w['sample']:w for w in data['windows']}
    facts={s['sample']:s for s in truth['samples']}
    if (len(facts)!=len(truth['samples']) or len(windows)!=len(predictions) or
        len({p['sample'] for p in predictions})!=len(predictions) or set(facts)!=set(windows)):
        raise ValueError('Evaluation sample coverage differs')
    rows=[]
    for p in predictions:
        w=windows[p['sample']];t=facts[p['sample']];a=set(p['possible_operations'])
        scopes={}
        for field in TRUTH_SCOPES:
            b=set(t[field])
            scopes[field]=dict(known=sorted(b),missed=sorted(b-a),unconfirmed=sorted(a-b),
                unique_known=len(a)==1 and a==b,multiple_possible_with_known=bool(b) and len(a)>1,
                known_mixture=len(b)>1)
        rows.append(dict(sample=p['sample'],window=w,primary=t['primary'],candidate=t['candidate'],
            possible=sorted(a),queue_positive=p['plus_queue_waiting'],scopes=scopes,
            unknown_application=bool(p['plus_queue_waiting']) and not t['known_waiting_operations'],
            app_packets_enqueued_without_upstream=t['app_packets_enqueued_without_upstream'],
            protocol_packets_with_fifo_wait=t['protocol_packets_with_fifo_wait']))
    def counts(items):
        return dict(windows=len(items),positive_queue=sum(r['queue_positive'] for r in items),
            zero_queue=sum(not r['queue_positive'] for r in items),
            positive_queue_application_unknown=sum(r['unknown_application'] for r in items),
            scopes={f:dict(known_windows=sum(bool(r['scopes'][f]['known']) for r in items),
                unique_known=sum(r['scopes'][f]['unique_known'] for r in items),
                multiple_possible_with_known=sum(r['scopes'][f]['multiple_possible_with_known'] for r in items),
                known_mixture_windows=sum(r['scopes'][f]['known_mixture'] for r in items),
                missed_known_operations=sum(len(r['scopes'][f]['missed']) for r in items),
                unconfirmed_candidate_occurrences=sum(len(r['scopes'][f]['unconfirmed']) for r in items),
                windows_with_unconfirmed=sum(bool(r['scopes'][f]['unconfirmed']) for r in items)) for f in TRUTH_SCOPES})
    return dict(all=counts(rows),primary=counts([r for r in rows if r['primary']]),
        related=counts([r for r in rows if not r['primary']]),
        strata={name:counts([r for r in rows if test(r)]) for name,test in (
            ('known_application',lambda r:bool(r['scopes'][TRUTH_SCOPES[0]]['known'])),
            ('zero_queue',lambda r:not r['queue_positive']),
            ('positive_queue_application_unknown',lambda r:r['unknown_application']))},samples=rows)


def compare(prepared, output):
    if output.exists():raise ValueError('Comparison output must be new')
    info=prepared/'information';inp=info/'input.json';sem=prepared/'semantics.json'
    output.mkdir(parents=True)
    # Freeze exact rules and all judge code before opening evaluation labels.
    save(output/'rules.json',dict(rule=bounds.RULE,programs={str(p):digest(p) for p in (
        Path(bounds.__file__),Path(__file__),Path(__file__).with_name('network_information.py'))},
        input_sha256=digest(inp),semantics_sha256=digest(sem),
        unchanged_guard='destination end beyond capture remains unknown for both baselines and both relation forms',
        other_constraints='positive workload already restricts sources; device-facing static peer already restricts destination. '
            'No public route-specific minimum delay or per-plane byte budget is supplied. Destination start, zero queue, '
            'short silence and logical/wire byte subtraction cannot safely eliminate a source.'))
    module='experiments.scaleup_issue_screening.analysis.network_bounds'
    command(output,'simplify',[sys.executable,'-m',module,'simplify','--input',str(inp),
        '--relation',str(info/'relation.json'),'--output',str(output/'boundary.json')])
    for mode in ('old','strong'):
        for suffix,extra in (('',None),('_full',info/'relation.json'),('_simple',output/'boundary.json')):
            name=mode+suffix
            command(output,name,[sys.executable,'-m',module,'judge','--input',str(inp),'--semantics',str(sem),
                '--mode',mode,'--output',str(output/(name+'.json'))]+(['--relation',str(extra)] if extra else []))
    predictions={n:read(output/(n+'.json')) for n in ('old','old_full','old_simple','strong','strong_full','strong_simple')}
    equivalence={m:predictions[m+'_full']['samples']==predictions[m+'_simple']['samples'] for m in ('old','strong')}
    for m,old_name in (('old','old-prediction.json'),('old_full','old-mapping-prediction.json')):
        old=read(info/old_name)['samples']
        if old!=[{k:r[k] for k in ('sample','possible_operations','endpoint_only_waiting','plus_queue_waiting')}
                 for r in predictions[m]['samples']]:raise ValueError('Old rule changed')
    if not all(equivalence.values()):raise ValueError('Full/simple per-window outputs differ')
    data,truth=read(inp),read(info/'truth.json')
    if truth['input_sha256']!=digest(inp):raise ValueError('Evaluation input seal differs')
    evaluations={}
    for name,p in predictions.items():
        if p['input_sha256']!=digest(inp) or p['semantics_sha256']!=digest(sem):raise ValueError('Prediction identity differs')
        e=evaluate(data,p['samples'],truth)
        e['sources']={str(f):digest(f) for f in (inp,info/'truth.json',output/(name+'.json'))}
        save(output/(name+'-evaluation.json'),e);evaluations[name]=e
    samples={n:{r['sample']:r for r in e['samples']} for n,e in evaluations.items()}
    differences=[]
    for w in data['windows']:
        sid=w['sample'];values={n:samples[n][sid]['possible'] for n in ('old','old_full','strong','strong_full')}
        if len({tuple(v) for v in values.values()})>1:
            differences.append(dict(window=w,possible=values,known=samples['old'][sid]['scopes'][TRUTH_SCOPES[0]]['known']))
    old_ambiguous=[r['sample'] for r in evaluations['old']['samples'] if not r['primary'] and
        r['scopes'][TRUTH_SCOPES[0]]['multiple_possible_with_known']]
    old_gains=[r['sample'] for r in evaluations['old_full']['samples'] if not r['primary'] and
        r['scopes'][TRUTH_SCOPES[0]]['unique_known'] and not samples['old'][r['sample']]['scopes'][TRUTH_SCOPES[0]]['unique_known']]
    remaining_gains=[r['sample'] for r in evaluations['strong_full']['samples'] if
        r['scopes'][TRUTH_SCOPES[0]]['unique_known'] and not samples['strong'][r['sample']]['scopes'][TRUTH_SCOPES[0]]['unique_known']]
    relation=read(info/'relation.json');simple=read(output/'boundary.json')
    summary=dict(schema_version='issue-network-bound-comparison/1',rule=bounds.RULE,
        counts={n:{k:v for k,v in e.items() if k not in ('samples','sources')} for n,e in evaluations.items()},
        exact_full_simple_equivalence=equivalence,old_ambiguous_related=old_ambiguous,old_unique_gains=old_gains,
        old_gains_resolved_by_strong=[s for s in old_gains if samples['strong'][s]['scopes'][TRUTH_SCOPES[0]]['unique_known']],
        strong_remaining_ideal_unique_gains=remaining_gains,differences=differences,
        residual_ambiguities=[r for r in evaluations['strong']['samples'] if r['scopes'][TRUTH_SCOPES[0]]['multiple_possible_with_known']],
        compact_information=dict(full_records=len(relation['records']),full_record_fields=list(relation['records'][0]) if relation['records'] else [],
            boundary_entries=len(simple['records']),boundary_nonempty=sum(r['last_tx_window_end_ns'] is not None for r in simple['records']),
            boundary_fields=list(simple['records'][0]) if simple['records'] else [],
            process='one pass over nonzero relation rows, then one lookup per candidate operation/port; not an online or SRAM cost'),
        offline=dict(capture_start_ns=relation['start_ns'],capture_end_ns=relation['end_ns'],
            maximum_future_capture_horizon_ns=max(max(0,relation['end_ns']-w['end_ns']) for w in data['windows']),
            boundary_states={n:dict(Counter(r['boundary_state'] for p in predictions[n]['samples'] for r in p['operation_reasons'].values()))
                             for n in ('old_full','strong_full')},
            interpretation='All comparisons are retrospective. Ideal exclusions use the sealed future capture; endpoints are also complete offline observations.'),
        sources={str(p):digest(p) for p in (inp,sem,info/'truth.json',info/'relation.json',output/'rules.json',output/'boundary.json')},
        programs={str(p):digest(p) for p in (Path(bounds.__file__),Path(__file__))},
        limitations=['FIFO known set complete, upstream waiting only partially captured; unconfirmed is not automatically false.',
            'Positive queue without known application remains unknown, not automatically protocol.',
            'Related windows and nested request records are not independent statistical samples.'])
    save(output/'summary.json',summary)
    if any(e['all']['scopes'][TRUTH_SCOPES[0]]['missed_known_operations'] for e in evaluations.values()):
        raise ValueError('A condition misses known application waits; saved evidence for review')
    print({n:e['related']['scopes'][TRUTH_SCOPES[0]] for n,e in evaluations.items()})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:compare(a.prepared,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m','experiments.scaleup_issue_screening.analysis.network_bound_review',*sys.argv[1:],'--worker'],
            str(a.output)+'-resources',**host())
        if r['status']!='completed':raise SystemExit('Offline comparison failed: '+r['status'])


if __name__=='__main__':main()
