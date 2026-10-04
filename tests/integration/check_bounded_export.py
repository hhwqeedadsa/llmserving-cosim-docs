"""Compare new bounded exports with the original research algorithms."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil

from moe_ub_cosim.bounded_export import export_bounded
from moe_ub_cosim.research_export import export_records
from moe_ub_cosim.record_storage import iter_result,iter_port_records
from moe_ub_cosim.research_storage import logical_windows,stored_rows


def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--graph-replay-dir',type=Path)
    parser.add_argument('--retirement-dir',type=Path)
    args=parser.parse_args();base=args.baseline_dir.resolve();out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    replay=args.graph_replay_dir.resolve() if args.graph_replay_dir else base/'graph-replay/work'
    retired=args.retirement_dir.resolve() if args.retirement_dir else base/'lifecycle-storage/work'
    limits=dict(schema_version='moe-ub-research-export-policy/1',max_index_bytes=128*2**20,
        max_compressed_bytes=128*2**20,max_record_bytes=2**20,max_endpoint_records=100000,
        max_operations=10000,max_layouts=1000,max_id_ranges=10000,max_graphs=1000,cache_rows=16)
    results={}
    cases=[('closed-service-'+name,base/'closed-service/work'/name,
            (retired if name=='provenance-off' else replay)/('closed-service-'+name))
           for name in ('reference','provenance-off','window')]
    for name in ('rounds-8','rounds-32'):
        old=base/'lifecycle-stress/work'/name;new=replay/name
        # The old frontend kept all plans. Rehydrate only its small result/2
        # record representation for the unchanged legacy export algorithm.
        legacy=out/(name+'-legacy');legacy.mkdir();(legacy/'execution/ports').mkdir(parents=True)
        (legacy/'graphs').symlink_to(old/'graphs',target_is_directory=True)
        shutil.copyfile(old/'service_result.json',legacy/'service_result.json')
        report=read(old/'execution/result.json');fields=list(report['record_storage']['fields'])
        expanded=deepcopy(report)
        for field in fields:expanded[field]=list(iter_result(old/'execution',field,report))
        expanded.pop('record_storage');expanded['schema_version']='moe-ub-service-result/1'
        save(legacy/'execution/result.json',expanded)
        for directory in (old/'execution/ports').iterdir():
            target=legacy/'execution/ports'/directory.name;target.mkdir()
            for name2 in ['packet-events.jsonl','observed/endpoint_ops.jsonl','truth/operation_registry.jsonl',
                          'truth/transfer_registry.jsonl','truth/packet_ranges.jsonl']:
                path=target/name2;path.parent.mkdir(exist_ok=True)
                path.write_text(''.join(json.dumps(row)+'\n' for row in iter_port_records(old/'execution',directory,name2)))
            shutil.copyfile(directory/'observation-audit.json',target/'observation-audit.json')
        cases.append((name,legacy,new))
    for name,old,new in cases:
        ranks=sorted(r for g in read(new/'deployment.json')['groups'] for r in g['rank_ids'])
        ports={}
        for directory in sorted((new/'execution/ports').iterdir()):
            switch,port=map(int,directory.name.split('-'));ports.setdefault(switch,[]).append(port)
        provenance=name!='closed-service-provenance-off';max_ns=1000000
        observation=dict(clocks=[dict(device_type='rank',device_id=r,offset_ns=r-20,drift_ppb=-456) for r in ranks]+
            [dict(device_type='switch',device_id=s,offset_ns=7,drift_ppb=123) for s in ports],
            switches=[dict(switch_id=s,port_ids=p,start_local_ns=7,end_local_ns=max_ns+7,period_ns=1001) for s,p in ports.items()],
            record_provenance=provenance)
        save(out/(name+'-observation.json'),observation)
        expected=export_records(old,observation,ranks,max_ns)
        result=export_bounded(new,observation,ranks,max_ns,out/name,limits)
        for actual,wanted in [('endpoint_ops.jsonl','endpoints'),('port_windows.csv','counters')]:
            rows=list(logical_windows(out/name/'observed')) if actual=='port_windows.csv' else list(stored_rows(out/name/'observed',actual))
            assert rows==expected[wanted],(name,wanted)
        for actual,wanted in [('operation_registry.jsonl','operations'),('transfer_registry.jsonl','transfers'),('attribution_windows.csv','attribution')]:
            assert list(stored_rows(out/name/'truth',actual))==expected[wanted],(name,wanted)
        for field in ('unique_delivery_bytes','transfers','endpoint_records','port_windows','zero_windows','attribution_status','port_audits'):
            assert result[field]==expected['validation'][field],(name,field)
        nonzero={tuple(r[k] for k in ('switch_id','port_id','local_window_start_ns')):r for r in stored_rows(out/name/'metrics','byte-classification.jsonl')}
        for row in expected['validation']['byte_classification']:
            key=tuple(row[k] for k in ('switch_id','port_id','local_window_start_ns'))
            if key in nonzero:assert row==nonzero.pop(key),(name,'classification')
            else:assert all(v==0 for k,v in row.items() if k.endswith('_bytes'))
        assert not nonzero and not (out/name/'metrics/transfer-index.bin').exists()
        results[name]=result
    # Reject target-write corruption using new test-owned files only.
    _,_,source=cases[0];corrupt=out/'corrupt-raw';shutil.copytree(source,corrupt)
    report=read(corrupt/'execution/result.json');report['record_storage']['fields']['target_writes']['files'][0]['crc32']^=1
    save(corrupt/'execution/result.json',report)
    obs=read(out/'closed-service-reference-observation.json');ranks=sorted(r for g in read(source/'deployment.json')['groups'] for r in g['rank_ids'])
    try:export_bounded(corrupt,obs,ranks,1000000,out/'corrupt-export',limits)
    except ValueError as error:assert 'CRC mismatch' in str(error)
    else:raise AssertionError('Corrupt writes accepted')
    assert not (out/'corrupt-export/metrics/validation.json').exists()
    save(out/'summary.json',dict(passed=True,cases=results,target_scale_executed=False,corruption_rejected=True))
    print('PASS: bounded research metadata, clocks, complete windows, truth and byte conservation')


if __name__=='__main__':main()
