"""Bounded evidence after the full-audit artifact limit; no simulation/retry."""
import argparse
import hashlib
import json
from pathlib import Path


def source(path):
    raw=path.read_bytes(); assert len(raw)<=2**20
    return json.loads(raw),hashlib.sha256(raw).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--recovered-baseline',required=True,type=Path)
    p.add_argument('--baseline',required=True,type=Path)
    p.add_argument('--numeric',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();assert not a.output.exists();a.output.mkdir()
    recovered=json.loads(a.recovered_baseline.read_text())
    cases={}
    for name in ('baseline','numeric'):
        root=getattr(a,name).resolve();config,config_hash=source(root/'input.json')
        usage,usage_hash=source(root/'execution/probe.json')
        provenance,provenance_hash=source(root/'provenance.json')
        stdout=(root/'native.stdout.log').read_bytes(); assert len(stdout)<=2**20
        events=[json.loads(line.partition(b' ')[2]) for line in stdout.splitlines()
                if line.startswith(b'MOE_RDMA_EVENT ')]
        complete=[row for e in events for row in e['completed']]
        assert len(events)==222 and len(complete)==221
        assert all(row['graph_id'].endswith('.w0') for row in complete)
        path=root/'log/astra_nodes.log';size=path.stat().st_size
        with path.open('rb') as stream:
            stream.seek(max(0,size-2**20));tail=stream.read(2**20)
        observed=[];loads=[]
        for line in tail.splitlines(keepends=True)[1:]:
            if not line.endswith(b'\n'):continue
            line=line.decode().rstrip('\n')
            if line.startswith(('issue,sys->id=','callback,sys->id=')):
                kind,_,fields=line.partition(',')
                fields=dict(v.split('=',1) for v in fields.split(', '))
                observed.append(dict(kind=kind,rank=int(fields['sys->id']),tick=int(fields['tick']),
                    node=int(fields['node->id']),name=fields['node->name'],
                    node_type=int(fields['node->type']),graph_id=fields['graph_id']))
            elif line.startswith(('graph_load,','graph_activate,')):loads.append(line)
        assert observed
        cases[name]=dict(root=str(root),input=config,input_sha256=config_hash,usage=usage,
            usage_sha256=usage_hash,provenance=provenance,provenance_sha256=provenance_hash,
            stdout_sha256=hashlib.sha256(stdout).hexdigest(),decisions=events,
            saved_completion_notifications=len(complete),node_log_bytes=size,
            tail_bytes=len(tail),tail_sha256=hashlib.sha256(tail).hexdigest(),
            tail_complete_node_records=len(observed),tail_max_tick=max(e['tick'] for e in observed),
            tail_last_complete_node_record=observed[-1],tail_last_line=tail.splitlines()[-1].decode(),
            result_exists=(root/'result.json').exists(),tail_newline=tail.endswith(b'\n'))
        (a.output/(name+'-tail.bin')).write_bytes(tail)
        (a.output/(name+'-stdout.log')).write_bytes(stdout)
    assert cases['baseline']['input_sha256']==cases['numeric']['input_sha256']
    assert cases['baseline']['decisions']==cases['numeric']['decisions']
    counts=recovered['node_log']['counts']
    assert counts['issue']==sum(v['issue'] for v in recovered['wave_prefixes'].values())
    assert counts['callback']==sum(v['callback'] for v in recovered['wave_prefixes'].values())
    assert all(recovered['wave_prefixes'][i]['issue']==recovered['wave_prefixes'][i]['callback']
               for i in recovered['completed_wave_times'])
    assert recovered['node_log']['max_recorded_tick']==cases['baseline']['tail_max_tick']
    # A pair of future-layer RECV issues is not proof of reaching that layer's
    # actual computation. Preserve callback progress and the latest full record.
    result=dict(scope='partial historical audit; full-audit output hit resource limit',
        cases=cases,baseline_recovered_node_summary=recovered['node_log'],
        baseline_recovered_wave_prefixes=recovered['wave_prefixes'],
        full_numeric_counts_available=False,full_audit_reexecuted_after_artifact_failure=False,
        task_event_records_equal=True,stdout_byte_identical=False,new_simulation_executed=False,
        hardware_calibrated=False,complete_execution_proven=False,
        warning='saved stdout completions are a lower bound; worker buffered writes and async node tails can be lost')
    (a.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:dict(saved_notifications=v['saved_completion_notifications'],
        last_recorded_tick=v['tail_max_tick'],last_event=v['tail_last_complete_node_record']) for k,v in cases.items()}))


if __name__=='__main__':main()
