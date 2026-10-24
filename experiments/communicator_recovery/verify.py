"""Independent integer/call-order oracle; imports neither extractor nor harness."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
from itertools import product
import json
from pathlib import Path

COMMITS={'nccl':'12df1a11afad322be5a204a2db890161cbf8131d',
         'deepep':'f6aa38fe06cd4a0489e7730365d4e0114970a2f3'}


def need(ok,why):
    if not ok:raise AssertionError(why)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def store(child,value):
    return [f'store_{name}:{value}' for name in (['child_host','child_device'] if child else [])+['host','device']]


def expected_records():
    result=[]
    stages=['set_device','host_sync','device_sync','poll_events','poll_callbacks','proxy_stop']
    for child,proxy,old,fail in product(range(2),range(3),range(2),['none']+stages):
        calls=['ptr_check'];error=fail in stages[:-1]
        end=stages.index(fail)+1 if error else len(stages);calls+=stages[:end]
        if not error:
            if proxy==2 and old==0:calls+=['join_proxy','join_uds']
            calls+=store(child,0)
        ret=3 if error else 0;flag=int(error);calls+=[f'async_error:{ret}']
        result.append(dict(kind='revoke_async',child=child,proxy=proxy,old_refs=old,fail=fail,
            **{'return':ret},async_=ret,host_flag=flag,device_flag=flag,
            child_host=flag if child else 1,child_device=flag if child else 1,trace=calls))
        result[-1]['async']=result[-1].pop('async_')
    for child,abort,fail in product(range(2),range(2),['none','group_start','device_sync','child_init','group_end']):
        calls=['group_start'];flag=0;handle=0;ret=0
        if fail=='group_start':ret=3
        else:
            if abort:
                calls+=store(child,1)+['device_sync']
                if fail=='device_sync':flag=1;ret=3
                else:calls+=store(child,0)
            if ret==0:
                calls+=['child_init']
                if fail=='child_init':ret=3
                else:handle=1
            calls+=[f'group_error:{ret}','group_end']
            if fail=='group_end':ret=3
        result.append(dict(kind='shrink',child=child,abort=abort,fail=fail,**{'return':ret},
            host_flag=flag,device_flag=flag,child_host=flag if child else 0,child_device=flag if child else 0,
            child_handle_returned=handle,trace=calls))
    for revoked,shrink,abort,split_share,shrink_share in product(range(2),repeat=5):
        shared=int(not revoked and (shrink_share and not abort if shrink else split_share))
        result.append(dict(kind='sharing',revoked=revoked,shrink=shrink,abort=abort,
                           split_share=split_share,shrink_share=shrink_share,shared=shared))
    for internal,abort,deadline,empty,done in product(range(2),range(2),[-1,4,10002],range(2),[0,2,-1]):
        polls=blocking=fences=0;ret=0;exit_='returned'
        if empty:pass
        elif deadline==-1 and not internal:
            blocking=1
            if done==0:fences=1
            else:ret=-1;exit_='recording_blocking_wait'
        elif done>=0:polls=done+1;fences=1
        elif internal and abort and (deadline<0 or deadline>10000):polls=10000;fences=1
        elif deadline>=0:polls=deadline;ret=8
        else:polls=20001;ret=-1;exit_='recording_poll_cap'
        result.append(dict(kind='gin_wait',internal=internal,abort_set=abort,deadline=deadline,empty=empty,
                           complete_after=done,abort_pointer_present=internal,**{'return':ret},exit=exit_,
                           polls=polls,blocking_calls=blocking,fences=fences))
    return result


def records_check(rows):
    encode=lambda r:json.dumps(r,sort_keys=True,separators=(',',':'))
    expected=expected_records();need(len(rows)==208 and len(rows)==len(expected),'condition coverage')
    need(Counter(map(encode,rows))==Counter(map(encode,expected)),'independent record oracle')


def source_check(inputs,evidence,generated):
    manifest=json.loads((inputs/'sources.json').read_text());need(len(manifest)==16,'source coverage')
    lookup={}
    for project in COMMITS:
        base=inputs/'provenance'/project;tree=json.loads((base/'tree.json').read_text())
        need(not tree.get('truncated',False),'truncated tree')
        if project=='nccl':
            commit=json.loads((base/'commit.json').read_text())
            need(commit['sha']==COMMITS[project] and commit['tree']['sha']==tree['sha'],'commit/tree binding')
        else:
            ref=json.loads((base/'ref.json').read_text());origin=json.loads((base/'tree-source.json').read_text())
            need(ref['object']['sha']==origin['commit']==COMMITS[project],'DeepEP pinned ref/endpoint')
            need(any(r['sha256']==sha((base/'tree.json').read_bytes()) and '/git/trees/'+COMMITS[project]+'?' in r['url'] for r in origin['requests']),'DeepEP tree response hash')
        lookup[project]={e['path']:e for e in tree['tree'] if e['type']=='blob'}
    seen=set()
    for r in manifest:
        project,path=r['project'],r['path'];raw=(inputs/'sources'/project/path).read_bytes()
        need((project,path) not in seen,'duplicate source');seen.add((project,path))
        blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        entry=lookup[project][path]
        need(r['commit']==COMMITS[project] and r['bytes']==len(raw)==entry['size'] and
             r['sha256']==sha(raw) and r['git_blob_sha']==blob==entry['sha'],'full source identity')
    need(len(evidence)==20 and len({e['id'] for e in evidence})==20,'excerpt coverage')
    for e in evidence:
        text=(inputs/'sources'/e['project']/e['path']).read_text()
        span=''.join(text.splitlines(keepends=True)[e['start_line']-1:e['end_line']])
        need(e['text'] in span and e['text'].strip()==span.strip() and sha(e['text'].encode())==e['sha256'],'original excerpt bytes')
        if e['kind']=='executed_original':need(generated.count(e['text'])==1,'compiled original byte coverage')
    # Necessary source boundaries for the conclusions below, not a whole-library proof.
    docs=' '.join((inputs/'sources/nccl/docs/userguide/source/api/comms.rst').read_text().split())
    need('caller needs to make sure the corresponding buffer within the window is not being accessed by any NCCL operation.' in docs,'legal deregistration precondition')
    need('without destroying resources' in docs and 'no resources will be shared' in docs,'strong baseline contracts')
    dev=(inputs/'sources/nccl/src/dev_runtime.cc').read_text()
    need('if (isInternal) outDevComm->abortFlag = comm->abortFlagDev;' in dev and '/*isInternal=*/false' in dev,'external abort selection')


def summary_check(summary,rows):
    need(summary['schema_version']=='communicator-recovery-summary/1' and summary['conditions']==208,'summary identity')
    need(summary['kinds']==dict(Counter(r['kind'] for r in rows)),'summary kind counts')
    need(summary['worker_proxy_stop_errors_ignored']==12,'proxy stop result handling')
    need(summary['wait_recording_stops']=={'recording_blocking_wait':4,'recording_poll_cap':1},'recording stop counts')
    for name in ('external_abort_pointer_absent','shrink_abort_never_shares','revoked_never_shares','deregistration_requires_no_access'):
        need(summary[name] is True,'source conclusion '+name)
    for name in ('revoke_success_proves_mr_retirement','arbitrary_user_kernel_quiescence_proven','hardware_or_gpu_executed',
                 'supported_failure_recovery_for_deepep_proven','performance_or_innovation_proven'):
        need(summary[name] is False,'unsupported conclusion '+name)


def verify(inputs,evidence,records,summary,generated):
    source_check(inputs,evidence,generated);records_check(records);summary_check(summary,records)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','evidence','records','summary','generated','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();evidence=json.loads(a.evidence.read_text());records=[json.loads(line) for line in a.records.read_text().splitlines()]
    summary=json.loads(a.summary.read_text());generated=a.generated.read_text()
    verify(a.inputs,evidence,records,summary,generated);tamper=[]
    for key in ('revoke_success_proves_mr_retirement','deregistration_requires_no_access','external_abort_pointer_absent','shrink_abort_never_shares'):
        value=deepcopy(summary);value[key]=not value[key]
        try:summary_check(value,records)
        except AssertionError:tamper.append(key)
        else:raise AssertionError('wrong conclusion accepted')
    value=deepcopy(records);r=next(r for r in value if r['kind']=='gin_wait' and r['polls']==10000);r['polls']=1
    try:records_check(value)
    except AssertionError:tamper.append('abort_poll_count')
    else:raise AssertionError('wrong wait trace accepted')
    result=dict(schema_version='communicator-recovery-verification/1',source_files=16,excerpts=20,whole_original_functions=5,
                original_statements=2,synthetic_conditions=208,independent_call_order_and_integer_oracle=True,
                unsupported_conclusions_and_trace_tampering_rejected=tamper,gpu_or_provider_executed=False)
    a.output.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
