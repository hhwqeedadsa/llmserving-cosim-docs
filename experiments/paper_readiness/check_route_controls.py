"""Independent raw-source, invariant and destination-enumeration review."""
import argparse
from collections import Counter
import csv
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import resource
import shutil
import time


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def quantiles(hist):
    size=sum(hist.values())
    if not size:return dict(count=0)
    def percentile(p):
        target=(size*p+99)//100;total=0
        for value,n in sorted(hist.items()):
            total+=n
            if total>=target:return float(value)
    return dict(count=size,minimum=float(min(hist)),p50=percentile(50),p95=percentile(95),maximum=float(max(hist)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('prepared','analysis','repeat_prepared','repeat_analysis','source_root','output'):
        parser.add_argument('--'+key.replace('_','-'),type=Path,required=True)
    a=parser.parse_args();start=time.monotonic()
    resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30));resource.setrlimit(resource.RLIMIT_CPU,(60,60))
    assert not a.output.exists() and a.output.parent.is_dir() and shutil.disk_usage(a.output.parent).free>=8*2**30
    prepared=a.prepared.resolve();observed=prepared/'observed';truth=prepared/'truth'
    index=json.loads((observed/'index.json').read_text());cfg=json.loads((truth/'config.json').read_text())
    manifest=json.loads((truth/'manifest.json').read_text());provenance=json.loads((truth/'provenance.json').read_text())
    assert sha(truth/'manifest.json')==provenance['manifest_sha256'] and sha(truth/'config.json')==provenance['config_sha256']
    assert len(index['conditions'])==8 and manifest['source']['dataset_revision']==cfg['dataset_revision']
    files={};total=0
    for name,info in index['artifacts'].items():
        assert Path(name).name==name
        path=observed/name;total+=path.stat().st_size
        assert total<=16*2**20 and sha(path)==info['sha256'] and path.stat().st_size==info['bytes']
        assert path.read_bytes()==(a.repeat_prepared/'observed'/name).read_bytes()
        files[name]=json.loads(path.read_text())
    assert (observed/'index.json').read_bytes()==(a.repeat_prepared/'observed/index.json').read_bytes()
    for name in ('summary.json','windows.csv'):
        assert (a.analysis/name).read_bytes()==(a.repeat_analysis/name).read_bytes()
    original=files['routes-original.json']['selections'];source_hashes=[]
    for src,item in enumerate(manifest['files']):
        p=a.source_root.resolve()/item['file']['path'];assert p.stat().st_size<=4*2**20 and sha(p)==item['file']['sha256']
        raw=json.loads(p.read_text());assert len(raw)==129
        for li,layer in enumerate(range(3,61)):
            assert original[li][src]==[r[str(layer)][0] for r in raw[1:]]
        source_hashes.append(sha(p));del raw
    assert len(set(source_hashes))==4
    logs=json.loads((truth/'swap_summary.json').read_text());swapped_rows=0
    for seed,log in zip(cfg['swap_seeds'],logs):
        assert log['seed']==seed and len(log['cells'])==232
        tensor=files['routes-swap_'+str(seed)+'.json']['selections']
        for li in range(58):
            for src in range(4):
                before,after=original[li][src],tensor[li][src]
                assert len(after)==128 and all(len(row)==len(set(row))==8 and all(type(e) is int and 0<=e<256 for e in row) for row in after)
                assert Counter(e for row in before for e in row)==Counter(e for row in after for e in row)
                cell=log['cells'][li*4+src]
                changed=sum(frozenset(x)!=frozenset(y) for x,y in zip(before,after));swapped_rows+=changed
                assert cell['changed_token_sets']==changed and cell['attempts']==4096 and 0<=cell['accepted']<=4096
    shifted=files['routes-phase.json']['selections']
    for li in range(58):
        for src,offset in enumerate(cfg['phase_offsets']):
            for t in range(128):assert shifted[li][src][t]==original[li][src][(t+offset)%128]
    for name,value in files.items():
        if value['schema_version']=='moe-route-control-placement/1':
            assert len(value['owners'])==58
            for mapping in value['owners']:
                assert len(mapping)==256 and all(type(v) is int and 0<=v<4 for v in mapping)
                assert [mapping.count(rank) for rank in range(4)]==[64]*4
                if name=='placement-contiguous.json':assert mapping==[i//64 for i in range(256)]
    summary=json.loads((a.analysis/'summary.json').read_text())
    assert summary['observed_index_sha256']==sha(observed/'index.json')
    rows_checked=matrix_entries=0;results=[];original_windows={};changes={};diagnostics={}
    with (a.analysis/'windows.csv').open(newline='') as f:
        reader=csv.DictReader(f)
        for condition in index['conditions']:
            tensor=files[condition['routes']]['selections'];owners=files[condition['placement']]['owners']
            h_in=Counter();h_compute=Counter();h_expert=Counter();h_absolute=Counter();matrix_total=[0]*16;expert_matrix=[0]*16
            transitions=Counter();ratio_down=Counter()
            n_direct=n_expert=n_local=equal=full=zeros=0;layers=[]
            changes[condition['id']]=dict(matrix=0,direct_copies=0,incoming_ratio=0,compute_ratio=0)
            for li in range(58):
                layer_copies=layer_expert=0;layer_hist=Counter()
                for step in range(128):
                    expected=[];expert_counts=[]
                    for src in range(4):
                        selected=tensor[li][src][step]
                        for dst in range(4):
                            hits=sum(owners[li][e]==dst for e in selected)
                            expert_counts.append(hits if src!=dst else 0)
                            expected.append(int(hits>0 and src!=dst));matrix_entries+=1
                    copies=sum(expected);expert_n=sum(expert_counts)
                    local=sum(any(owners[li][e]==src for e in tensor[li][src][step]) for src in range(4))
                    incoming=[sum(expected[dst::4]) for dst in range(4)]
                    compute=[sum(owners[li][e]==dst for selected in tensor[li] for e in selected[step]) for dst in range(4)]
                    in_ratio=Fraction(4*max(incoming),copies) if copies else None
                    compute_ratio=Fraction(4*max(compute),32)
                    hottest=max(Counter(e for selected in tensor[li] for e in selected[step]).values())
                    row=next(reader);rows_checked+=1
                    assert (row['condition'],int(row['layer']),int(row['entry']))==(condition['id'],li+3,step+1)
                    assert row['matrix_bits']==''.join(str(v) for v in expected)
                    assert (int(row['direct_copies']),int(row['expert_remote_contributions']),int(row['local_token_copies']),int(row['expert_max_selected']))==(copies,expert_n,local,hottest)
                    assert (float(row['incoming_max_to_mean']) if row['incoming_max_to_mean'] else None)==(float(in_ratio) if in_ratio is not None else None)
                    assert float(row['compute_max_to_mean'])==float(compute_ratio)
                    absolute=max(incoming);h_absolute[absolute]+=1
                    current=(tuple(expected),copies,in_ratio,compute_ratio);key=li,step
                    if condition['id']=='original':original_windows[key]=current
                    else:
                        before=original_windows[key]
                        for name,x,y in zip(changes[condition['id']],current,before):changes[condition['id']][name]+=x!=y
                        previous_absolute=max(sum(before[0][dst::4]) for dst in range(4))
                        transitions[str(previous_absolute)+'->'+str(absolute)]+=1
                        if in_ratio is not None and before[2] is not None and in_ratio<before[2]:
                            ratio_down['windows']+=1
                            ratio_down['absolute_decreased' if absolute<previous_absolute else
                                       'absolute_increased' if absolute>previous_absolute else 'absolute_unchanged']+=1
                            ratio_down['copies_increased' if copies>before[1] else
                                       'copies_decreased' if copies<before[1] else 'copies_unchanged']+=1
                    if in_ratio is not None:h_in[in_ratio]+=1;layer_hist[in_ratio]+=1
                    h_compute[compute_ratio]+=1;h_expert[hottest]+=1
                    n_direct+=copies;n_expert+=expert_n;n_local+=local;layer_copies+=copies;layer_expert+=expert_n
                    equal+=len(set(incoming))==1;full+=copies==12;zeros+=copies==0
                    matrix_total=[x+y for x,y in zip(matrix_total,expected)]
                    expert_matrix=[x+y for x,y in zip(expert_matrix,expert_counts)]
                layers.append(dict(layer=li+3,direct_copies=layer_copies,expert_remote_contributions=layer_expert,
                                   incoming_max_to_mean=quantiles(layer_hist)))
                assert time.monotonic()-start<55
            diagnostics[condition['id']]=dict(absolute_incoming_max_histogram=dict(sorted(h_absolute.items())),
                absolute_incoming_max=quantiles(h_absolute),paired_absolute_max_transitions=dict(sorted(transitions.items())),
                windows_with_lower_ratio_than_original=dict(ratio_down))
            results.append(dict(condition=condition['id'],windows=7424,direct_copies=n_direct,
                expert_remote_contributions=n_expert,local_token_copies=n_local,
                all_destination_token_copies=n_direct+n_local,aggregate_direct_matrix=matrix_total,
                aggregate_expert_matrix=expert_matrix,incoming_max_to_mean=quantiles(h_in),
                compute_max_to_mean=quantiles(h_compute),expert_max_selected=quantiles(h_expert),
                equal_incoming_windows=equal,full_remote_fanout_windows=full,zero_remote_windows=zeros,by_layer=layers))
        assert next(reader,None) is None
    assert summary['conditions']==results and len(original_windows)==7424
    by_id={r['condition']:r for r in results};base=by_id['original'];phase=by_id['phase_offset']
    for key in ('direct_copies','expert_remote_contributions','local_token_copies','aggregate_direct_matrix','aggregate_expert_matrix'):
        assert phase[key]==base[key]
    for seed in cfg['swap_seeds']:
        assert by_id['swap_'+str(seed)]['aggregate_expert_matrix']==base['aggregate_expert_matrix']
    result=dict(passed=True,source_files_checked=4,raw_token_layer_rows_checked=29696,
        condition_window_rows_checked=rows_checked,destination_matrix_entries_checked=matrix_entries,
        degree_preserved_cells_checked=232*3,phase_rows_checked=29696,changed_swap_token_sets=swapped_rows,
        capacity_placement_layers_checked=58*4,paired_window_changes=changes,
        post_result_diagnostic='Absolute demand and paired ratio-denominator checks from existing matrices; not preregistered primary outcomes.',
        post_result_diagnostics=diagnostics,
        preparation_observed_byte_identical=True,analysis_relocated_byte_identical=True,
        observed_index_sha256=sha(observed/'index.json'),summary_sha256=sha(a.analysis/'summary.json'),
        windows_sha256=sha(a.analysis/'windows.csv'),script_sha256=sha(Path(__file__)),
        no_uniform_null_or_population_significance_claim=True,network_executed=False,
        hardware_calibrated=False,goal_complete=False,wall_seconds=time.monotonic()-start)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
