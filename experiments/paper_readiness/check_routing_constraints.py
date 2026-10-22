"""Independently recount raw tokens, group conditions and every reported matrix."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import resource
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[2]


def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for flag in ('manifest','old-observed','prepared','analysis','placements','output'):
        parser.add_argument('--'+flag,type=Path,required=True)
    parser.add_argument('--repeat-prepared',type=Path)
    a=parser.parse_args();start=time.monotonic()
    resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30));resource.setrlimit(resource.RLIMIT_CPU,(60,60))
    assert not a.output.exists() and shutil.disk_usage(a.output.parent).free>=8*2**30+32*2**20
    old_summary=json.loads((ROOT/'docs/review_artifacts/route_controls_20261022/summary.json').read_text())
    assert digest(a.old_observed/'index.json')==old_summary['observed_index_sha256']
    folders=[a.old_observed,a.prepared/'observed']; indices=[]; tensors=[]
    for folder in folders:
        index=json.loads((folder/'index.json').read_text());inputs={}
        for name,info in index['artifacts'].items():
            assert Path(name).name==name
            path=folder/name
            assert path.stat().st_size==info['bytes'] and digest(path)==info['sha256']
            inputs[name]=json.loads(path.read_text())
        indices.append(index);tensors.append(inputs)
    manifest=json.loads(a.manifest.read_text()); raw_tokens={}; source_hashes=[]
    for s,item in enumerate(manifest['files']):
        path=ROOT/item['file']['path'];assert digest(path)==item['file']['sha256']
        source_hashes.append(digest(path));raw=json.loads(path.read_text())
        for l in range(3,61):
            for t in range(128):
                token=raw[t+1][str(l)];assert len(token)==1
                raw_tokens[l,s,t]=token[0]
                for inputs in tensors:assert inputs['routes-original.json']['selections'][l-3][s][t]==token[0]
    summary=json.loads((a.analysis/'summary.json').read_text())
    assert summary['observed_indices_sha256']==[digest(f/'index.json') for f in folders]
    results=iter(summary['conditions']);counts=dict(conditions=0,windows=0,matrix_entries=0,tokens=0,marginal_cells=0,within_group_count_tokens=0)
    with (a.analysis/'windows.csv').open(newline='') as f:
        records=csv.DictReader(f)
        for index,inputs in zip(indices,tensors):
            for condition in index['conditions']:
                name=condition['id'];result=next(results);assert result['condition']==name
                choices=inputs[condition['routes']]['selections'];owners=inputs[condition['placement']]['owners']
                assert len(choices)==len(owners)==58
                histogram=Counter();total=remote=changed=group_changed=changed_matrices=0
                accum=[0]*16;matrix_digest=hashlib.sha256()
                for l in range(3,61):
                    owner=owners[l-3];assert len(owner)==256 and Counter(owner)=={0:64,1:64,2:64,3:64}
                    assert len(choices[l-3])==4 and all(len(rows)==128 for rows in choices[l-3])
                    if name.startswith(('cap4_','within32_','swap_')):
                        for s in range(4):
                            observed=Counter(e for token in choices[l-3][s] for e in token)
                            actual=Counter(e for t in range(128) for e in raw_tokens[l,s,t])
                            assert observed==actual;counts['marginal_cells']+=1
                    for t in range(128):
                        matrix=[[0]*4 for _ in range(4)];base_matrix=[[0]*4 for _ in range(4)];compute=Counter()
                        for s in range(4):
                            token=choices[l-3][s][t];base=raw_tokens[l,s,t]
                            assert len(token)==8 and len(set(token))==8 and all(type(e) is int and 0<=e<256 for e in token)
                            groups=Counter(e//32 for e in token);bgroups=Counter(e//32 for e in base)
                            histogram[len(groups)]+=1;changed+=set(token)!=set(base);group_changed+=groups!=bgroups
                            if name.startswith(('cap4_','within32_')):assert len(groups)<=4
                            if name.startswith('within32_'):
                                assert groups==bgroups;counts['within_group_count_tokens']+=1
                            dest=Counter(owner[e] for e in token)
                            if name.startswith('within32_'):assert dest==Counter(owner[e] for e in base)
                            for d,n in dest.items():
                                compute[d]+=n
                                if d!=s:matrix[s][d]=1;remote+=n
                            for d in set(owner[e] for e in base):
                                if d!=s:base_matrix[s][d]=1
                            counts['tokens']+=1
                        flat=[n for row in matrix for n in row];bits=''.join(str(n) for n in flat)
                        record=next(records)
                        assert record==dict(condition=name,layer=str(l),entry=str(t+1),matrix_bits=bits,copies=str(sum(flat)),compute_max=str(max(compute.values())))
                        if name.startswith('within32_'):assert matrix==base_matrix
                        changed_matrices+=matrix!=base_matrix
                        accum=[x+y for x,y in zip(accum,flat)];total+=sum(flat);matrix_digest.update(bits.encode())
                        counts['windows']+=1;counts['matrix_entries']+=16
                assert result['token_selections']==29696 and result['windows']==7424
                assert {str(k):v for k,v in sorted(histogram.items())}==result['logical_group_histogram']
                assert result['cap_violations']==sum(v for k,v in histogram.items() if k>4)
                assert result['direct_copies']==total and result['expert_remote_contributions']==remote
                assert result['changed_token_sets']==changed and result['changed_token_group_counts']==group_changed
                assert result['changed_matrices_same_placement']==changed_matrices
                assert result['aggregate_matrix']==accum and result['matrix_sha256']==matrix_digest.hexdigest()
                if name in {r['condition'] for r in old_summary['conditions']}:
                    prior=next(r for r in old_summary['conditions'] if r['condition']==name)
                    assert prior['direct_copies']==total and prior['expert_remote_contributions']==remote
                counts['conditions']+=1
        assert next(records,None) is None and next(results,None) is None
    logs=json.loads((a.prepared/'truth/proposals.json').read_text())
    assert len(logs)==6
    for log in logs:
        assert len(log['cells'])==232
        for cell in log['cells']:
            assert cell['attempts']==4096==cell['accepted']+sum(cell['rejected'].values())
    provenance=json.loads((a.prepared/'truth/provenance.json').read_text())
    assert [r['sha256'] for r in provenance['sources']]==source_hashes
    assert provenance['manifest_sha256']==digest(a.manifest) and provenance['model_revision'] is None
    # A placement may scatter a valid logical group; do not call that a routing violation.
    assert digest(a.placements)=='dcdf9220498839182591250f114d85f75bbe851e376c9bf828a82c384964d3e7'
    placements=json.loads(a.placements.read_text())['placements'];diagnostics=[]
    for name,owners in placements.items():
        hist=Counter()
        for owner in owners:
            assert len(owner)==256
            for group in range(8):hist[len(set(owner[group*32:group*32+32]))]+=1
        assert sum(hist.values())==464
        diagnostics.append(dict(placement=name,logical_groups=464,physical_rank_span_histogram=dict(sorted(hist.items()))))
    repeat_files=0
    if a.repeat_prepared:
        first={str(p.relative_to(a.prepared)):digest(p) for p in a.prepared.rglob('*') if p.is_file() and p.name!='execution.json'}
        second={str(p.relative_to(a.repeat_prepared)):digest(p) for p in a.repeat_prepared.rglob('*') if p.is_file() and p.name!='execution.json'}
        assert first==second;repeat_files=len(first)
    a.output.mkdir()
    write(a.output/'placement_groups.json',dict(placements_sha256=digest(a.placements),conditions=diagnostics))
    write(a.output/'verification.json',dict(completed=True,counts=counts,raw_sources_sha256=source_hashes,
        summary_sha256=digest(a.analysis/'summary.json'),windows_sha256=digest(a.analysis/'windows.csv'),
        repeat_files=repeat_files,old_result_counts_unchanged=True,all_within32_matrices_identical=True,
        all_new_expert_margins_identical=True,all_new_routes_cap_consistent=True,network_executed=False,
        checker_sha256=digest(Path(__file__)),model_collector_revision_unknown=True))
    write(a.output/'execution.json',dict(argv=sys.argv,cwd=str(Path.cwd()),wall_seconds=time.monotonic()-start,
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
    print(json.dumps(dict(completed=True,counts=counts,repeat_files=repeat_files,placement_groups=diagnostics)))


if __name__=='__main__':main()
