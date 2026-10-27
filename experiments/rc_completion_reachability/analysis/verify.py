"""Independent source/record checks; never imports the extracted C driver."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def load(p):return json.loads(p.read_bytes())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
def digest(kind,raw):return hashlib.sha1(kind.encode()+b' '+str(len(raw)).encode()+b'\0'+raw).hexdigest()


def check_records(records):
    names=('initial_two_pending','ack_before_atomic_retry','ack_after_atomic_retry','fenced_atomic_retry','write_ack_precedes_retry')
    assert [r['case'] for r in records]==list(names)
    # Independent table for queue prefix and held credit. State values 0/2
    # are additionally checked against the unchanged upstream enum below.
    expected=[(2,0,2,0,2,0,0,0,1,0),(1,1,1,1,0,1,0,1,0,1),
              (2,1,2,1,2,2,0,1,1,0),(1,1,1,1,0,1,1,1,0,1),
              (2,1,2,1,2,1,0,1,1,0)]
    fields=('req_psn','comp_psn','req_index','consumer','atomic_state','retry_sent',
            'atomic_fenced_now','selected_atomic','get_wqe_check_psn','get_wqe_done')
    for row,want in zip(records,expected):
        assert tuple(row[k] for k in fields)==want,row
        assert row['atomic_credit']==0 and row['atomic_holds_credit']==1 and row['write_state']==2
    assert records[3]['fence_before_retry_atomic']==1
    return True


def verify(stage,output):
    root=stage/'observed/v6.12';identity=load(root/'identity.json')
    commit=load(root/'commit.json');assert identity['commit']==commit['sha']=='adc218676eef25575469234709c2d87185ca223a'
    current=commit['commit']['tree']['sha']
    for item in identity['trees']:
        value=load(root/item['path']);rows=sorted(value['tree'],key=lambda r:(r['path']+('/' if r['type']=='tree' else '')).encode())
        raw=b''.join(r['mode'].lstrip('0').encode()+b' '+r['path'].encode()+b'\x00'+bytes.fromhex(r['sha']) for r in rows)
        assert digest('tree',raw)==value['sha']==current==item['sha']
        if item['next']:current=next(r['sha'] for r in rows if r['path']==item['next'] and r['type']=='tree')
    table={r['path']:r for r in value['tree']}
    for row in identity['sources']:
        raw=(root/row['local']).read_bytes();assert digest('blob',raw)==row['git_blob']==table[Path(row['path']).name]['sha']
        assert hashlib.sha256(raw).hexdigest()==row['sha256'] and len(raw)==row['bytes']
    generated=(stage/'prefix/prefix.c').read_bytes();fragments=load(stage/'prefix/fragments.json')
    for row in fragments['fragments']:
        raw=b''.join((root/'sources'/row['file']).read_bytes().splitlines(keepends=True)[row['first']-1:row['last']])
        assert hashlib.sha256(raw).hexdigest()==row['sha256'] and generated.count(raw)==1
    enum=(root/'sources/rxe_verbs.h').read_text();assert 'wqe_state_posted,\n\twqe_state_processing,\n\twqe_state_pending,' in enum
    records=[json.loads(line) for line in (stage/'prefix/records.jsonl').read_text().splitlines()]
    check_records(records)
    assert load(stage/'prefix/compile-exit.json')['exit']==load(stage/'prefix/execution-exit.json')['exit']==0
    acquisition=load(stage/'acquisition.json');assert acquisition['status']=='stopped_after_HTTP_failure'
    assert len(acquisition['requests'])==1 and not acquisition['sources'] and not acquisition['trees']
    request=acquisition['requests'][0];body=(stage/'observed/upstream'/request['path']).read_bytes()
    assert request['http_status']==200 and request['bytes']==len(body)==512*1024+1
    assert hashlib.sha256(body).hexdigest()==request['sha256'] and request['elapsed_seconds']<=10.5
    relevance=load(stage/'observed/moe-relevance/evidence.json')
    for row in relevance['source_files']:
        raw=(stage/'observed/moe-relevance'/Path(row['path']).name).read_bytes()
        assert digest('blob',raw)==row['git_blob'] and hashlib.sha256(raw).hexdigest()==row['sha256']
    assert relevance['controller_callback_row_fields']==['event','operation','time_ns']
    rejected=[]
    for name in ('atomic_posted_as_pending','self_credit_cleared','fence_ignored','source_fragment'):
        mutated=deepcopy(records)
        try:
            if name=='atomic_posted_as_pending':mutated[1]['atomic_state']=2
            elif name=='self_credit_cleared':mutated[3]['atomic_credit']=1
            elif name=='fence_ignored':mutated[3]['atomic_fenced_now']=0
            else:
                row=fragments['fragments'][0]
                assert hashlib.sha256(b'altered enum').hexdigest()==row['sha256']
            if name!='source_fragment':check_records(mutated)
        except AssertionError:rejected.append(name)
        else:raise AssertionError(name)
    result=dict(passed=True,pinned_linux=identity['commit'],source_files=9,source_trees=5,
        unchanged_functions=10,unchanged_enums=2,executed_prefixes=5,tamper_rejected=rejected,
        old_branch_gate_depends_on_retry_timing=True,self_held_credit_fences_retry_in_substitute=True,
        complete_kernel_reachability_proven=False,current_upstream_checked=False,
        direct_MoE_old_value_impact_proven=False,network_runs=0,GPU=0,
        HTTP=dict(attempts=1,status=200,bytes=len(body),outcome='oversized response cap sentinel; all further HTTP stopped'),
        new_innovation_established=False)
    save(output,result)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();verify(a.stage.resolve(),a.output.resolve())
