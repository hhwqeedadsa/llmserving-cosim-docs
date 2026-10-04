"""Independently audit target selection metadata and raw files, without loading a target graph.

Writes a two-request / three-layer input for the existing real-UB route replay.
No source route values are copied into this script or its metadata summaries.
"""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def read(p):return json.loads(Path(p).read_text())
def save(p,x):Path(p).write_text(json.dumps(x,indent=2)+'\n')
def sha(raw):return hashlib.sha256(raw).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--selection-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2];out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    config=read(args.input);policy=config['selection'];catalog=read(root/config['catalog']['path'])
    assert sha((root/config['catalog']['path']).read_bytes())==config['catalog']['sha256']
    assert (catalog['dataset_id'],catalog['dataset_revision'],catalog['complete'])==(
        'core12345/MoE_expert_selection_trace','27febb7b2d24169560a9f6f83d38eb53f94916cf',True)
    assert catalog['prefix']=='cognitivecomputations/DeepSeek-R1-AWQ/mmlu/' and len(catalog['files'])==13922
    assert (policy['seed'],policy['required_requests'],policy['min_decode_records'],policy['decode_steps'])==(0,1024,64,64)
    assert policy['rank_ids']==list(range(128)) and policy['arrival_ns']==0
    report=read(args.selection_dir/'report.json');manifest=read(args.selection_dir/'manifest.json');requests=read(args.selection_dir/'requests.json')
    assert report['state']=='ready' and report['data_ready'] and not report['eos_inferred'] and not report['target_scale_executed']
    assert sha((args.selection_dir/'manifest.json').read_bytes())==report['manifest']['sha256']
    assert sha((args.selection_dir/'requests.json').read_bytes())==report['requests_sha256']
    original=read(root/config['source_manifest']['path'])
    assert all(manifest[k]==original[k] for k in ('schema_version','source','model','layers'))
    assert manifest['source']['precision']=='awq_4bit' and not report['target_precision_match']
    assert manifest['source']['collector_model_revision'] is manifest['source']['runtime_revision'] is None
    assert manifest['layers']==[dict(layer_id=i,kind='dense' if i<3 else 'moe',num_experts=0 if i<3 else 256,top_k=0 if i<3 else 8) for i in range(61)]
    ordered=sorted(catalog['files'],key=lambda f:(sha(f"{policy['seed']}\n{f['path']}".encode('utf-8')),f['path']))
    checked=report['checked'];assert [r['original_path'] for r in checked]==[r['path'] for r in ordered[:len(checked)]]
    assert len(checked)<=config['budget']['max_scan_files']==1152
    total=0;selected=[];hashes=set();rejections=Counter();invalid=[];rank_kv=defaultdict(int);full_decode_rows=0
    for item,metadata in zip(checked,ordered):
        raw=(root/config['data_root']/item['original_path']).read_bytes();total+=len(raw)
        assert len(raw)==item['bytes']==metadata['size'] and sha(raw)==item['sha256'] and item['sha256'] not in hashes
        hashes.add(item['sha256'])
        if 'lfs_sha256' in metadata:assert sha(raw)==metadata['lfs_sha256']
        else:assert hashlib.sha1(('blob '+str(len(raw))+'\0').encode()+raw).hexdigest()==metadata['git_blob_id']
        entries=json.loads(raw);del raw
        # Independent scan of every entry, including unused steps after the requested window.
        entry_no=None;missing=[];extra=[]
        try:
            assert type(entries) is list and 1<=len(entries)<=65537
            prompt=len(entries[0]['3']);assert prompt>0
            for entry_no,entry in enumerate(entries):
                missing=sorted(set(map(str,range(61)))-set(entry));extra=sorted(set(entry)-set(map(str,range(61))))
                assert not missing and not extra
                assert all(entry[str(i)] is None for i in range(3))
                for layer in range(3,61):
                    rows=entry[str(layer)];assert type(rows) is list and len(rows)==(prompt if entry_no==0 else 1)
                    for choices in rows:
                        assert type(choices) is list and len(choices)==len(set(choices))==8
                        assert all(type(e) is int and 0<=e<256 for e in choices)
        except (AssertionError,KeyError,TypeError):
            assert not item['raw_schema_valid'] and not item['eligible'] and item['reason']=='invalid_source_file'
            rejections['invalid_source_file']+=1
            invalid.append(dict(path=item['original_path'],first_bad_entry=entry_no,missing_layers=missing,extra_layers=extra))
            del entries;continue
        steps=len(entries)-1;del entries
        assert item['raw_schema_valid'] and item['prompt_tokens']==prompt and item['available_decode_records']==steps
        full_decode_rows+=steps*58
        if steps<64:
            assert not item['eligible'] and item['reason']=='too_few_decode_records';rejections[item['reason']]+=1;continue
        i=len(selected);assert item['eligible'] and item['reason']=='selected'
        assert (item['manifest_index'],item['request_id'],item['rank_id'])==(i,i+1,i%128)
        assert (item['first_input_token_index'],item['last_input_token_index'],item['reserved_kv_tokens'],item['observed_tokens_reached'])==(prompt,prompt+63,prompt+64,prompt+65)
        f=manifest['files'][i];r=requests[i]
        assert f==dict(file_id=f'request-{i:04d}',original_path=item['original_path'],format='token_layer_map_v1',
            file=dict(path=str(Path(config['data_root'])/item['original_path']),sha256=item['sha256']))
        assert r==dict(request_id=i+1,rank_id=i%128,arrival_ns=0,file_id=f['file_id'],first_decode_entry=1,decode_steps=64,target_total_tokens=None)
        selected.append(item);rank_kv[i%128]+=((prompt+64+15)//16)*1124352
        if len(selected)%128==0:print('Independently verified eligible files:',len(selected),flush=True)
    assert len(selected)==len(manifest['files'])==len(requests)==1024 and checked[-1]['eligible']
    assert total==report['read_bytes']<=config['budget']['max_total_read_bytes']==2252054093
    assert Counter(r['rank_id'] for r in requests)==Counter({r:8 for r in range(128)})
    assert report['selected_decode_layer_records']==1024*64*58==3801088
    # Derive a bounded fragment only for the existing fixed-batch UB protocol test.
    indices=[i for i,r in enumerate(selected) if r['available_decode_records']==128][:2];assert len(indices)==2
    small=deepcopy(manifest);small['files']=[manifest['files'][i] for i in indices];save(out/'smoke-manifest.json',small)
    smoke=read(root/'fixtures/m9_evidence/published-r1-selection.json')
    smoke['manifest']=dict(path=str(out/'smoke-manifest.json'),sha256=sha((out/'smoke-manifest.json').read_bytes()))
    smoke['requests']=[dict(requests[i],rank_id=2,decode_steps=2) for i in indices];save(out/'smoke-selection.json',smoke)
    inventory=json.dumps(catalog['files'],sort_keys=True,separators=(',',':')).encode()
    summary=dict(schema_version='moe-ub-target-route-audit/1',dataset_id=catalog['dataset_id'],dataset_revision=catalog['dataset_revision'],
        catalog_files=len(ordered),catalog_inventory_sha256=sha(inventory),checked_files=len(checked),read_bytes=total,selected_files=1024,
        rejected_files=dict(rejections),invalid_file_details=invalid,
        prompt_range=[min(r['prompt_tokens'] for r in selected),max(r['prompt_tokens'] for r in selected)],
        available_decode_range=[min(r['available_decode_records'] for r in selected),max(r['available_decode_records'] for r in selected)],
        all_valid_candidate_decode_layer_records=full_decode_rows,selected_decode_layer_records=3801088,
        requests_per_rank=8,rank_count=128,conditional_window64_kv_bytes_per_rank=dict(sorted(rank_kv.items())),
        kv_formula='sum(ceil((actual_prompt+64)/16) * 1124352) per rank; 70272 B/token reference',
        conditional_kv_budget_per_rank=31325587712,conditional_kv_fits=all(v<=31325587712 for v in rank_kv.values()),
        smoke_selected_manifest_indices=indices,smoke_rank_override=2,smoke_steps_override=2,
        manifest_sha256=report['manifest']['sha256'],requests_sha256=report['requests_sha256'],
        eos_inferred=False,target_precision_match=False,target_scale_executed=False)
    save(out/'summary.json',summary)
    print('Independent selection audit passed:',len(selected),'eligible;',dict(rejections),'excluded; bytes:',total)


if __name__=='__main__':main()
