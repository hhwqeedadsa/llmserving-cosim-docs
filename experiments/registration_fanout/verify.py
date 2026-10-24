"""Independent integer oracle for registration coverage and partial error records."""
import argparse
from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path


MODES=('register_error','null_handle','multisegment_unsupported','mr_error',
       'metadata_handle_error','metadata_keys_error')


def need(ok,why):
    if not ok:raise AssertionError(why)


def oracle(row):
    counts=row['counts'];mode=row['mode'];fail=row['failure_slot'];devices=row['devices_per_backend']
    slots=[b*4+c for b,n in enumerate(counts) for c in range(n)]
    need(devices in (1,2) and 1<=len(counts)<=2 and all(1<=n<=4 for n in counts),'layout')
    need(mode in MODES+('success',),'unknown mode')
    stop=len(slots) if mode=='success' else slots.index(fail)
    created=slots;reg_calls=slots;dereg_calls=[];retired=[];freed=[];meta=[];result=0;reg_result=0
    if mode in ('register_error','null_handle'):
        reg_calls=slots[:stop+1];created=slots[:stop];result=reg_result=1
    elif mode=='multisegment_unsupported':
        created=reg_calls=[s for s in slots if s//4<fail//4];result=reg_result=2
    else:
        dereg_calls=slots if mode=='success' else slots[:stop+1]
        retired=dereg_calls[:-1] if mode=='mr_error' else dereg_calls
        freed=dereg_calls if mode=='success' else dereg_calls[:-1]
        meta=[2*s+k for s in freed for k in range(2)]
        if mode=='metadata_handle_error':meta+=[2*fail]
        elif mode=='metadata_keys_error':meta+=[2*fail,2*fail+1]
        if mode!='success':result=1
    expected=dict(registration_calls=reg_calls,created=created,register_result=reg_result,result=result,
        deregistration_calls=dereg_calls,mr_calls=dereg_calls,retired=retired,freed=freed,metadata_calls=meta,
        alias_device_mapping=[[b*4+c,b,c%devices] for b,n in enumerate(counts) for c in range(n)],
        same_address_and_extent_checked=True,actual_provider_executed=False)
    for key,value in expected.items():need(row[key]==value,'oracle '+key)
    return expected


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','build','records','summary','evidence','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();sources={}
    for row in json.loads((a.inputs/'sources.json').read_text()):
        raw=(a.inputs/'sources'/row['alias']).read_bytes();sources[row['alias']]=raw.decode()
        need(hashlib.sha256(raw).hexdigest()==row['sha256'],'source sha256')
        need(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['git_blob_sha'],'source blob')
        if row['alias']!='context.cpp':
            provenance=a.inputs/'provenance/nccl_gin_20261022'
            commit=json.loads((provenance/'commit.json').read_text());tree=json.loads((provenance/'tree.json').read_text())
            need(commit['sha']==row['commit'] and commit['tree']['sha']==tree['sha'],'commit/tree binding')
            blob=next(x['sha'] for x in tree['tree'] if x['path']==row['path'])
            need(blob==row['git_blob_sha'],'tree blob binding')
    build=json.loads((a.build/'build.json').read_text());generated=(a.build/'selected.cc').read_text()
    need(hashlib.sha256(generated.encode()).hexdigest()==build['generated_sha256'],'generated hash')
    selected=build['functions'];need(len(selected)==3 and len({r['name'] for r in selected})==3,'function coverage')
    evidence=json.loads(a.evidence.read_text());need(len(evidence)==7,'static evidence coverage')
    for row in selected+evidence:
        excerpt='\n'.join(sources[row['source']].splitlines()[row['line']-1:row['end_line']])
        need(hashlib.sha256(excerpt.encode()).hexdigest()==row['sha256'],'exact source interval')
        if row in selected:need(generated.count(excerpt)==1,'compiled original function')
        need(all(x in excerpt for x in row.get('required',[])),'static evidence removed')
    rows=[json.loads(x) for x in a.records.read_text().splitlines()];actual=set()
    counts=dict(partial_retirement_errors=0,all_created_mrs_retired_but_error=0,
        registration_error_with_successful_prefix=0,cases_with_multiple_created_aliases_per_device=0)
    for row in rows:
        oracle(row);key=(tuple(row['counts']),row['devices_per_backend'],row['mode'],row['failure_slot'])
        need(key not in actual,'duplicate case');actual.add(key)
        created=set(row['created']);retired=set(row['retired'])
        counts['partial_retirement_errors']+=int(bool(row['result'] and retired and retired!=created))
        counts['all_created_mrs_retired_but_error']+=int(bool(row['result'] and created and retired==created))
        counts['registration_error_with_successful_prefix']+=int(bool(row['register_result'] and created))
        device_owners={(s//4,(s%4)%row['devices_per_backend']) for s in created}
        counts['cases_with_multiple_created_aliases_per_device']+=int(len(created)>len(device_owners))
    expected=set()
    for backends in (1,2):
        for layout in itertools.product(range(1,5),repeat=backends):
            slots=[b*4+c for b,n in enumerate(layout) for c in range(n)]
            for devices in (1,2):
                expected.add((layout,devices,'success',-1))
                expected.update((layout,devices,mode,slot) for mode in MODES for slot in slots)
    need(actual==expected and len(rows)==1120,'enumerated layout/mode coverage')
    summary=json.loads(a.summary.read_text())
    need(summary['cases']==len(rows) and all(summary[k]==v for k,v in counts.items()),'summary counters')
    need(summary['records_sha256']==hashlib.sha256(a.records.read_bytes()).hexdigest(),'records hash')
    need(summary['actual_provider_executed'] is False and not summary['paper_innovation_established'],'scope')
    rejected=[]
    for kind in ('any_success_means_all_retired','all_errors_mean_none_retired','same_device_has_one_alias'):
        if kind=='any_success_means_all_retired':
            row=next(r for r in rows if r['retired'] and set(r['retired'])!=set(r['created']));wrong=deepcopy(row);wrong['retired']=wrong['created']
        elif kind=='all_errors_mean_none_retired':
            row=next(r for r in rows if r['result'] and r['retired']);wrong=deepcopy(row);wrong['retired']=[]
        else:
            row=next(r for r in rows if r['counts']==[2] and r['devices_per_backend']==1 and r['mode']=='success')
            wrong=deepcopy(row);wrong['alias_device_mapping']=wrong['alias_device_mapping'][:1]
        try:oracle(wrong)
        except AssertionError:rejected.append(kind)
        else:raise AssertionError('semantic tampering accepted')
    result=dict(schema_version='registration-fanout-verification/1',source_files=len(sources),
        exact_functions=len(selected),static_excerpts=len(evidence),independent_cases=len(rows),
        independently_counted=counts,semantic_tampering_rejected=rejected,actual_provider_executed=False)
    a.output.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
