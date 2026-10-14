"""Conservative offline boundary recovery from public transmit windows only.

Preparation, restricted recovery and private evaluation are separate commands.
No packet identity, ideal relation, dependency, class byte count or raw path is
opened by recover(). Silence now never certifies absence of future injection.
"""
import argparse
from pathlib import Path
import sys

from . import network_bounds as bounds
from .workflow import digest, host, read, save

RULE=dict(version='public-nonzero-window-envelope/1',
    hypothesis='An application packet waiting at an outlet must start transmission in a later compatible nonzero public packet window.',
    envelope='Intersect positive packet windows with each positive source/destination temporal envelope; union sources. '
             'The last compatible window end is a conservative upper bound, not an estimated last application packet.',
    safe_unknown='Missing windows or destination completion beyond capture cannot certify a final boundary.',
    control='All protocol/metadata traffic remains eligible public traffic; do not subtract logical payload from wire bytes.',
    alternative='If other application or protocol traffic fills all temporal opportunities, counters cannot tighten these bounds.',
    scope='One fixed simple rule; a failed attempt does not prove universal unidentifiability.')


def prepare(prepared, archive, output):
    from moe_ub_cosim.research_storage import sparse_windows
    from .consumer_review import Archive
    from .organization import resolved_members
    if output.exists():raise ValueError('Recovery preparation output must be new')
    source=Archive(archive);identity=read(prepared/'source/source.json')
    if source.index['archive_sha256']!=identity['archive_sha256']:raise ValueError('Counter archive differs')
    output.mkdir(parents=True)
    names=['run/observed/storage.json','run/observed/public_config.json','run/observed/records/port_windows.csv.gz']
    for n,data in resolved_members(source,names):
        path=output/'source'/n;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    inp=prepared/'information/input.json';data=read(inp)
    keys={(w['switch_id'],w['port_id'],w['start_ns']):w for w in data['windows']};counts={}
    obs=output/'source/run/observed';public=read(obs/'public_config.json')
    configs={(p['switch_id'],p['port_id']):p for p in public['ports']}
    for row in sparse_windows(obs,public):
        key=row['switch_id'],row['port_id'],row['local_window_start_ns']
        if key in keys:
            if row['local_window_end_ns']!=keys[key]['end_ns']:raise ValueError('Counter time precision differs')
            counts[key]=row
    windows=[]
    for key,w in keys.items():
        conf=configs[key[:2]]
        if not conf['start_local_ns']<=w['start_ns']<w['end_ns']<=conf['end_local_ns']:
            raise ValueError('Window outside counter coverage')
        row=counts.get(key,{})
        windows.append(dict(sample=w['sample'],counted_packets=row.get('counted_packets',0),
            counted_packet_bytes=row.get('counted_packet_bytes',0),capture_complete=True))
    save(output/'counters.json',dict(schema_version='issue-network-public-counts/1',input_sha256=digest(inp),
        start_ns=min(w['start_ns'] for w in data['windows']),end_ns=max(w['end_ns'] for w in data['windows']),windows=windows))
    save(output/'preparation.json',dict(archive_sha256=digest(archive),members=source.used,
        counter_sha256=digest(output/'counters.json'),input_sha256=digest(inp),program_sha256=digest(__file__)))


def recover_input(data, semantics, counters):
    baseline=bounds.judge_input(data,semantics)
    if (set(counters)!={'schema_version','input_sha256','start_ns','end_ns','windows'} or
        counters['schema_version']!='issue-network-public-counts/1' or
        counters['start_ns']!=min(w['start_ns'] for w in data['windows']) or
        counters['end_ns']!=max(w['end_ns'] for w in data['windows'])):
        raise ValueError('Unexpected public counter input or coverage')
    windows={w['sample']:w for w in data['windows']};counts={}
    for r in counters['windows']:
        if (set(r)!={'sample','counted_packets','counted_packet_bytes','capture_complete'} or
            r['sample'] in counts or r['sample'] not in windows or type(r['capture_complete']) is not bool or
            any(type(r[k]) is not int or r[k]<0 for k in ('counted_packets','counted_packet_bytes')) or
            (r['counted_packets']==0)!=(r['counted_packet_bytes']==0) or r['counted_packet_bytes']<r['counted_packets']):
            raise ValueError('Invalid public packet/byte window')
        counts[r['sample']]=r
    candidates={r['sample']:r['possible_operations'] for r in baseline}
    ports={(w['switch_id'],w['port_id']) for w in windows.values()}
    operations=sorted({e['operation'] for e in data['endpoints']})
    records=[];intervals=[]
    for switch,port in sorted(ports):
        group=sorted((w for w in windows.values() if (w['switch_id'],w['port_id'])==(switch,port)),key=lambda w:w['start_ns'])
        continuous=(group[0]['start_ns']==counters['start_ns'] and group[-1]['end_ns']==counters['end_ns'] and
                    all(a['end_ns']==b['start_ns'] for a,b in zip(group,group[1:])))
        complete=continuous and all(w['sample'] in counts and counts[w['sample']]['capture_complete'] for w in group)
        for o in operations:
            possible=[w for w in group if counts.get(w['sample'],{}).get('counted_packets',0)>0 and
                      o in candidates[w['sample']]]
            end=max((w['end_ns'] for w in possible),default=None)
            records.append(dict(operation=o,switch_id=switch,port_id=port,
                last_tx_window_end_ns=end,capture_complete=complete))
            intervals.append(dict(operation=o,switch_id=switch,port_id=port,
                earliest_possible_tx_window_start_ns=min((w['start_ns'] for w in possible),default=None),
                last_possible_tx_window_end_ns=end,compatible_nonzero_windows=len(possible),
                capture_complete=complete,may_have_no_application_transmission=True))
    boundary=dict(schema_version='issue-network-last-window/1',input_sha256=counters['input_sha256'],
        start_ns=counters['start_ns'],end_ns=counters['end_ns'],records=records)
    return dict(samples=bounds.judge_input(data,semantics,extra=boundary),intervals=intervals)


def recover(input_path, semantics_path, counters_path, output):
    if output.exists():raise ValueError('Recovery output must be new')
    data,semantics,counters=read(input_path),read(semantics_path),read(counters_path)
    identity=digest(input_path)
    if semantics['input_sha256']!=identity or counters['input_sha256']!=identity:raise ValueError('Recovery input identity differs')
    save(output,dict(rule=RULE,**recover_input(data,semantics,counters),input_sha256=identity,
        semantics_sha256=digest(semantics_path),counters_sha256=digest(counters_path),
        programs={str(Path(p)):digest(p) for p in (__file__,bounds.__file__)}))


def evaluate(prepared, comparison, prediction, output):
    from .network_bound_review import evaluate as score, TRUTH_SCOPES
    if output.exists():raise ValueError('Recovery evaluation output must be new')
    info=prepared/'information';data=read(info/'input.json');truth=read(info/'truth.json');p=read(prediction)
    if p['input_sha256']!=digest(info/'input.json') or truth['input_sha256']!=p['input_sha256']:
        raise ValueError('Recovery evaluation identity differs')
    result=score(data,p['samples'],truth)
    relation=read(info/'relation.json');actual={}
    for r in relation['records']:
        key=r['operation'],r['switch_id'],r['port_id'];actual[key]=max(actual.get(key,0),r['end_ns'])
    intervals=[]
    for row in p['intervals']:
        b=actual.get((row['operation'],row['switch_id'],row['port_id']));upper=row['last_possible_tx_window_end_ns']
        intervals.append(dict(**row,ideal_last_tx_window_end_ns=b,
            early_upper_bound=row['capture_complete'] and b is not None and (upper is None or upper<b),
            excess_upper_ns=upper-b if upper is not None and b is not None else None))
    base={r['sample']:r for r in read(comparison/'strong.json')['samples']}
    ideal={r['sample']:r for r in read(comparison/'strong_full.json')['samples']}
    all_rows={r['sample']:r for r in result['samples']}
    cases=[]
    for r in p['samples']:
        sid=r['sample']
        if r['possible_operations']!=ideal[sid]['possible_operations'] or r['possible_operations']!=base[sid]['possible_operations']:
            cases.append(dict(sample=sid,window=all_rows[sid]['window'],baseline=base[sid]['possible_operations'],
                recovered=r['possible_operations'],ideal=ideal[sid]['possible_operations'],
                known=all_rows[sid]['scopes'][TRUTH_SCOPES[0]]['known'],reasons=r['operation_reasons']))
    value=dict(schema_version='issue-network-recovery-evaluation/1',rule=RULE,evaluation=result,
        intervals=intervals,early_upper_bounds=sum(r['early_upper_bound'] for r in intervals),cases=cases,
        sources={str(f):digest(f) for f in (info/'input.json',info/'truth.json',info/'relation.json',prediction,
            comparison/'strong.json',comparison/'strong_full.json')},program_sha256=digest(__file__))
    save(output,value)
    if value['early_upper_bounds'] or result['all']['scopes'][TRUTH_SCOPES[0]]['missed_known_operations']:
        raise ValueError('Recovery is unsafe; preserved evaluation')


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    a=sub.add_parser('prepare')
    for n in ('prepared','archive','output'):a.add_argument('--'+n,type=Path,required=True)
    a=sub.add_parser('recover')
    for n in ('input','semantics','counters','output'):a.add_argument('--'+n,type=Path,required=True)
    a=sub.add_parser('evaluate')
    for n in ('prepared','comparison','prediction','output'):a.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.action=='recover':recover(a.input,a.semantics,a.counters,a.output)
    elif a.worker:
        if a.action=='prepare':prepare(a.prepared,a.archive,a.output)
        else:evaluate(a.prepared,a.comparison,a.prediction,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m','experiments.scaleup_issue_screening.analysis.network_recovery','--worker',*sys.argv[1:]],
            str(a.output)+'-resources',**host())
        if r['status']!='completed':raise SystemExit('Recovery action failed: '+r['status'])


if __name__=='__main__':main()
