"""Whole-result equivalence and bounded clock-prefix analysis; no controller imports."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from fixtures.session_host_timing.stage import load,save,sha
from fixtures.modern_native.audit import audit
from fixtures.native_wakeup.verify import delivery
from moe_ub_cosim.host_timing import read_trace


def normalized(result,libraries,captured_root=None):
    value=deepcopy(result);found=[]
    for index,path in enumerate(value['loaded_libraries']):
        name=Path(path).name
        if name in libraries:
            source=Path(path) if captured_root is None else captured_root/name
            assert sha(source)==libraries[name]
            found.append(name);value['loaded_libraries'][index]='verified-eight/'+name
    assert len(found)==8 and set(found)==set(libraries)
    return value


def compare(stage,case,*,captured_root=None):
    directory=stage/case;old=stage/'original'/('default-healthy' if case=='default-healthy' else 'wake-healthy')
    libraries={Path(path).name:value for path,value in load(stage/'identity.json')['libraries'].items()}
    for name in ('initial.json','input.json','protocol.json','topology.json','modern-execution.json'):
        assert load(directory/name)==load(old/name),name+' changed'
    assert normalized(load(directory/'result.json'),libraries,captured_root)==normalized(load(old/'result.json'),libraries,captured_root)
    assert (directory/'control.jsonl').read_bytes()==(old/'control.jsonl').read_bytes(),'control order/fields changed'
    result=dict(passed=True,case=case,whole_native_and_modern_result_equal=True,
        normalization='only paths of hash-checked fixed eight libraries',
        actual_delivery=delivery(directory),modern_acceptance=audit(directory))
    if case=='default-healthy':
        assert not any((directory/name).exists() for name in ('worker_host.jsonl','native_host.jsonl','host-timing.json'))
        result['no_optional_trace_artifacts']=True
    else:
        traces=tracing(directory)
        assert all(value['trace_complete'] for value in traces.values())
        decisions=result['actual_delivery']['decisions']
        assert traces['native']['phases']['session_candidate_validate']['count']==decisions
        assert traces['worker']['phases']['controller_advance']['count']==decisions
        assert traces['worker']['phases']['python_candidate_validate']['count']==decisions
        final=traces['native']['snapshots'][-1]['values']
        assert final['finished'] and final['decisions']==decisions and final['pending_callbacks']==0
        assert final['collected_callbacks']==result['actual_delivery']['callbacks']
        assert final['registered_nodes']==final['submitted_nodes']==result['actual_delivery']['native_submissions']
        result['host_traces_complete']=True
    return result


def tracing(directory):
    result={}
    for name in ('worker','native'):
        trace=read_trace(directory/(name+'_host.jsonl'))
        assert trace['header']['writer']==('python' if name=='worker' else 'native')
        for sample in trace['snapshots']:
            assert sample['name'] in ('modern_decision','session_progress_enter','session_progress_exit')
        # Snapshots describe actual prefix counters, not packet count or end state.
        samples=[r['values'] for r in trace['snapshots']]
        for previous,current in zip(samples,samples[1:]):
            assert current['now_ns']>=previous['now_ns'] and current['decisions']>=previous['decisions']
            if name=='native':
                for key in ('processed_events','registered_nodes','submitted_nodes','collected_callbacks'):
                    assert current[key]>=previous[key]
        result[name]=trace
    return result


def summarize(directory):
    traces=tracing(directory);result={}
    for name,trace in traces.items():
        records=[]
        for line in (directory/(name+'_host.jsonl')).read_bytes().splitlines(keepends=True):
            if not line.endswith(b'\n'):break
            row=json.loads(line)
            if 'wall_ns' in row:records.append(row)
        first,last=records[0],records[-1]
        closed=sum(p['exclusive_wall_ns'] for p in trace['phases'].values())
        observed=last['wall_ns']-first['wall_ns'];assert 0<=closed<=observed
        result[name]=dict(trace_complete=trace['trace_complete'],records=trace['records'],
            phases=trace['phases'],open_spans=trace['open_spans'],
            last_snapshot=trace['snapshots'][-1] if trace['snapshots'] else None,
            observed_record_interval_wall_ns=observed,
            observed_record_interval_cpu_ns=last['cpu_ns']-first['cpu_ns'],
            closed_exclusive_wall_ns=closed,unattributed_record_interval_wall_ns=observed-closed,
            scope='last recorded clocks only; no fabricated timeout end or exclusive duration for an open parent')
    native_result=directory/'result.json';modern=directory/'modern-execution.json'
    result['service_complete']=bool(native_result.exists() and load(native_result).get('passed') and modern.exists()
        and load(modern)['runtime']['all_events_fired'])
    result['scope']='host phases only; nested or cross-process wall times must not be added; simulator exclusive includes transport/memory/device/event work'
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--case',required=True);p.add_argument('--summary',action='store_true');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();assert not a.output.exists()
    value=summarize(a.stage/a.case) if a.summary else compare(a.stage,a.case)
    save(a.output,value)


if __name__=='__main__':main()
