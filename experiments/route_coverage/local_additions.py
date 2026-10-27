"""Receive user-supplied R1/MMLU files without asserting remote revision proof.

Outputs are local intake evidence and offline traffic demand, NOT an executable
route manifest. Original files and the existing manifest are never modified.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.data_inputs import ROOT
from moe_ub_cosim.route_evidence import inspect_raw, unique_object, validate_manifest
from experiments.route_coverage.analyze import distribution, histogram_metrics, save, require


def digest(raw): return hashlib.sha256(raw).hexdigest()


def receive(directory, manifest_path, catalog_path):
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes, object_pairs_hook=unique_object); validate_manifest(manifest)
    catalog_bytes = catalog_path.read_bytes()
    require(digest(catalog_bytes)=='d6e25aa5d6d583d1dae72f78e010abad5839e8a85b3a53f0715ceba0178eb3c5', 'Historical visible catalog changed')
    catalog = json.loads(catalog_bytes)
    prefix = 'cognitivecomputations/DeepSeek-R1-AWQ/mmlu/'
    require(manifest['source']['collector_model_id']=='cognitivecomputations/DeepSeek-R1-AWQ', 'Unsupported collection model')
    require(manifest['model']['hidden_dim']==7168 and len(manifest['layers'])==61, 'Require the declared R1 structure')
    paths = sorted(directory.glob('[0-9]*.json'), key=lambda p:int(p.stem))
    sizes = [p.stat().st_size for p in paths]
    require(1 <= len(paths) <= 64 and max(sizes) <= 8*2**20 and sum(sizes) <= 64*2**20, 'Source budget exceeded')
    old = {str((ROOT/f['file']['path']).resolve()):f for f in manifest['files']}
    require(set(old) <= {str(p.resolve()) for p in paths}, 'Existing source file missing from intake directory')
    layers = [l for l in manifest['layers'] if l['kind']=='moe']
    require(len(layers)==58 and all(l['num_experts']==256 and l['top_k']==8 for l in layers), 'Require R1 E256/Top8')
    files = []; eligible = []; seen = {}; counts = {l['layer_id']:{phase:[0]*256 for phase in ('prefill','decode')} for l in layers}
    for path in paths:
        raw = path.read_bytes(); sha = digest(raw); existing = old.get(str(path.resolve()))
        candidates = [f['rfilename'] for f in catalog['siblings'] if f['rfilename'].startswith(prefix)
                      and f['rfilename'].endswith('/'+path.name)]
        row = dict(name=path.name, local_path=str(path.resolve()), bytes=len(raw), sha256=sha,
                   group='existing_four' if existing else 'new_user_supplied',
                   origin_candidates=candidates, origin_directory_user_confirmed=not bool(existing),
                   source_identity='existing_manifest' if existing else 'user_directory_and_historical_path_only',
                   remote_revision_content_verified=bool(existing))
        require(len(candidates)==1, 'Original path ambiguous or absent: '+path.name)
        row['original_path'] = candidates[0]
        if existing:
            require(existing['file']['sha256']==sha and existing['original_path']==candidates[0], 'Existing source identity changed')
        row['duplicate_of'] = seen.get(sha); seen.setdefault(sha, path.name)
        decoded = json.loads(raw, object_pairs_hook=unique_object)
        row['observed_entries'] = len(decoded)
        row['layer_set_errors'] = [dict(entry_index=i, missing=sorted(set(str(n) for n in range(61))-set(entry),key=int),
                                         extra=sorted(set(entry)-set(str(n) for n in range(61))))
                                   for i,entry in enumerate(decoded) if isinstance(entry,dict) and set(entry)!={str(n) for n in range(61)}]
        try:
            prompt, steps = inspect_raw(raw, manifest['layers'])
            row.update(structure_valid=True, prompt_tokens=prompt, decode_entries=steps,
                       token_layer_rows=(prompt+steps)*58, expert_choices=(prompt+steps)*58*8)
        except (ValueError,TypeError) as error:
            row.update(structure_valid=False, rejection_reason=str(error))
        row['eligible_offline'] = row['structure_valid'] and row['duplicate_of'] is None
        files.append(row)
        if not row['eligible_offline']: continue
        eligible.append((row,decoded))
        for l in layers:
            for phase, entries in (('prefill',decoded[:1]),('decode',decoded[1:])):
                for entry in entries:
                    for token in entry[str(l['layer_id'])]:
                        for e in token: counts[l['layer_id']][phase][e] += 1
    populations = {}
    for group in ('existing_four','new_user_supplied','all'):
        rows = [f for f in files if f['eligible_offline'] and (group=='all' or f['group']==group)]
        populations[group] = dict(files=len(rows), prompt_tokens=sum(f['prompt_tokens'] for f in rows),
            decode_steps=sum(f['decode_entries'] for f in rows), token_layer_rows=sum(f['token_layer_rows'] for f in rows),
            expert_choices=sum(f['expert_choices'] for f in rows))
    report = dict(schema_version='moe-local-route-intake/1', input_files=len(files), input_bytes=sum(sizes),
        source=dict(dataset_id=manifest['source']['dataset_id'], collector_model_id=manifest['source']['collector_model_id'],
            confirmed_directory=prefix, reference_catalog_revision=catalog['sha'],
            newly_supplied_download_revision=None, newly_supplied_remote_identity_verified=False,
            catalog_complete=False, selection='user_convenience_sample_not_random_or_heldout'),
        existing_manifest_sha256=digest(manifest_bytes), populations=populations,
        rejected_files=[f['name'] for f in files if not f['eligible_offline']], files=files,
        layers=[dict(layer_id=lid, **{phase:histogram_metrics(v) for phase,v in phases.items()}) for lid,phases in counts.items()],
        infer_eos_from_eof=False, formal_network_input_admitted=False, network_simulations=0)
    vector = 7168*2
    demands = dict(schema_version='moe-local-route-demand/1', input_scope='structure_valid_files_only_remote_identity_pending_for_new_files',
        batch_definition='distinct_requests_per_EP_group', ranks=4, expert_placement='contiguous_64_experts_per_rank',
        source_assignment='eligible_order_modulo4', activation='declared_BF16_not_collection_evidence', hidden_size=7168,
        excluded=['metadata','headers','control','ACK','Combine','compute','arrivals','queues','latency'],
        eligible_order=[f['name'] for f,_ in eligible], source_count_changes_with_batch=True, batches=[])
    for batch in (1,2,4,8,16):
        if len(eligible)<batch:
            demands['batches'].append(dict(batch=batch,state='insufficient_distinct_valid_requests',available=len(eligible)));continue
        groups=[list(range(i,i+batch)) for i in range(0,len(eligible)-batch+1,batch)]
        values=[]; lengths=[]; unused=0
        for group in groups:
            steps=min(eligible[i][0]['decode_entries'] for i in group);lengths.append(steps)
            unused+=sum(eligible[i][0]['decode_entries']-steps for i in group)
            for lid in counts:
                for step in range(1,steps+1):
                    values.append(sum(len({e//64 for e in eligible[i][1][step][str(lid)][0]}-{i%4}) for i in group)*vector)
        demands['batches'].append(dict(batch=batch,state='offline_demand_only',
            groups=[[eligible[i][0]['name'] for i in group] for group in groups],
            common_decode_steps_per_group=lengths, unused_decode_steps=unused,
            unused_request_files=[f['name'] for f,_ in eligible[len(groups)*batch:]],
            dispatch_payload_bytes=distribution(values), layer_step_group_waves=len(values),
            zero_remote_demand_waves=sum(v==0 for v in values)))
    require(digest(manifest_path.read_bytes())==digest(manifest_bytes), 'Old manifest modified')
    return report, demands


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',type=Path,required=True);p.add_argument('--existing-manifest',type=Path,required=True)
    p.add_argument('--catalog-cache',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();require(not a.output_dir.exists(),'Output directory exists')
    report,demands=receive(a.directory,a.existing_manifest,a.catalog_cache)
    a.output_dir.mkdir(parents=True)
    save(a.output_dir/'intake.json',report);save(a.output_dir/'traffic-demand.json',demands)
    print(json.dumps(dict(populations=report['populations'],rejected=report['rejected_files']),ensure_ascii=False))


if __name__=='__main__':main()
