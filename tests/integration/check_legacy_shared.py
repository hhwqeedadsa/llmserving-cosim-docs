"""Check retained shared runs, byte identity, isolation and native refusals."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

from moe_ub_cosim.legacy_shared_audit import audit
from moe_ub_cosim.legacy_audit import decode

if not __debug__:raise RuntimeError('Shared verification requires assertions; do not run with -O')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--backend-build',type=Path,help='New driver build directory; default ROOT/build-v2')
    p.add_argument('--prior-frozen',type=Path,help='Optional old frozen manifest to prove its original backend was not modified')
    a=p.parse_args();root=a.root.resolve();out=a.output.resolve()
    build_dir=(a.backend_build or root/'build-v2').resolve()
    assert not out.exists();out.mkdir(parents=True)
    load=lambda path:json.loads(path.read_bytes());sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    normal=load(root/'input/normal.json');fault=load(root/'input/fault.json')
    old=load(root/'old-single/result.json');single=load(root/'shared-single/result.json')
    two=load(root/'shared-two/result.json');bad=load(root/'shared-fault/result.json')
    work=load(root/'shared-two/execution.json');fault_work=load(root/'shared-fault/execution.json')
    assert work==fault_work
    old_legacy=dict(old['legacy']);old_transport=deepcopy(old_legacy.pop('transport'))
    assert old_legacy==single['legacy_shared']['groups'][0]['legacy']
    new_transport=deepcopy(single['legacy_shared']['transport'])
    assert old_transport['message_payload'].pop('capture_limit_bytes')==32*2**20
    assert new_transport['message_payload'].pop('capture_limit_bytes')==64*2**20
    assert old_transport==new_transport
    assert old['packet_capture']==single['packet_capture'] and old['finish_ns']==single['finish_ns']
    summaries={name:load(root/(name+'-audit.json')) for name in ('shared-single','shared-two','shared-fault','old-single')}
    for case,result in [('old-single',old),('shared-single',single),('shared-two',two),('shared-fault',bad)]:
        assert result['passed'] and result['drained'] and result['gpu_count']==128
        assert summaries[case]['passed']
    spans=[]
    for group,report in zip(work['groups'],two['legacy_shared']['groups']):
        events=group['execution']['events'];times=report['legacy']['event_times_ns']
        start=min(times[i] for i,e in enumerate(events) if e[0]=='kernel_begin')
        finish=max(times[i] for i,e in enumerate(events) if e[0]=='kernel_complete')
        assert start==0 and finish>0
        spans.append(dict(id=group['id'],start_ns=start,finish_ns=finish))
    assert max(row['start_ns'] for row in spans)<min(row['finish_ns'] for row in spans)
    assert bad['fault_injection']['drop_counts']==[1]
    payloads=bad['legacy_shared']['transport']['source_payload_packets']
    first=next(row for row in payloads if row['stream']==6 and row['sequence']==0)
    frame=decode(bytes.fromhex(first['hex']))
    assert len(frame)==1 and frame[0][1]==4 and int.from_bytes(frame[0][4],'big')==2**32-1
    assert summaries['shared-two']['groups'][1]['last_kernel_complete_ns']==summaries['shared-fault']['groups'][1]['last_kernel_complete_ns']
    assert [g['final_memory_sha256'] for g in summaries['shared-two']['groups']]==[g['final_memory_sha256'] for g in summaries['shared-fault']['groups']]
    changes=[]
    for mode in ('id','offset','missing-group','incomplete','foreign-work','source-uid','memory','kernel-time'):
        tampered=deepcopy(two)
        if mode=='id':tampered['legacy_shared']['groups'][1]['id']='wrong'
        elif mode=='offset':tampered['legacy_shared']['groups'][1]['stream_offset']+=1
        elif mode=='missing-group':tampered['legacy_shared']['groups'].pop()
        elif mode=='incomplete':tampered['legacy_shared']['finished']=False
        elif mode=='foreign-work':tampered['legacy_shared']['transport']['work'][0]['stream']=99999
        elif mode=='source-uid':tampered['legacy_shared']['transport']['source_packets'][0][-1]=-1
        elif mode=='memory':tampered['legacy_shared']['groups'][0]['legacy']['memory_events'][0][6]='00000000'
        else:tampered['legacy_shared']['groups'][0]['legacy']['event_times_ns'][0]+=1
        try:audit(work,tampered,normal)
        except (AssertionError,ValueError,KeyError,IndexError):changes.append(mode)
        else:raise AssertionError('Shared audit accepted tampering: '+mode)
    backend=build_dir/'moe-rdma-legacy-deepep';native=[]
    for mode,message in [('overlap','overlap'),('identity','group id'),('stream-count','context count'),
                         ('reference-cap','integer outside'),('transport','common transport'),('fields','schema/fields')]:
        candidate=deepcopy(work)
        if mode=='overlap':candidate['groups'][1]['execution']['rank_to_gpu'][0]=candidate['groups'][0]['execution']['rank_to_gpu'][0]
        elif mode=='identity':candidate['groups'][1]['id']=candidate['groups'][0]['id']
        elif mode=='stream-count':candidate['groups'][0]['execution']['limits']['streams']+=1
        elif mode=='reference-cap':
            candidate['dcqcn_reference']['max_events']=200001
            for row in candidate['groups']:row['execution']['dcqcn_reference']['max_events']=200001
        elif mode=='transport':candidate['groups'][1]['execution']['transport_recovery']['initial_rto_ns']+=1
        else:candidate['unexpected']=True
        source=out/(mode+'.json');source.write_text(json.dumps(candidate))
        result_path=out/(mode+'-result.json')
        command=list(map(str,[backend,root/'input/topology/topology.json',root/'input/normal.json',source,result_path]))
        result=subprocess.run(command,capture_output=True,text=True,timeout=5)
        (out/(mode+'-stderr.log')).write_text(result.stderr)
        assert result.returncode and message in result.stderr and not result_path.exists(),mode
        native.append(dict(case=mode,command=command,exit_code=result.returncode,stderr=result.stderr))
    build=load(build_dir/'backend.json')
    assert sha(backend)==build['backend_sha256']
    repo=Path(__file__).resolve().parents[2]
    for name,expected in build['sources'].items():assert sha(repo/name)==expected,name
    for name,expected in build['library_hashes'].items():assert sha(build_dir/'runtime/lib'/name)==expected
    prior_verified=None
    if a.prior_frozen:
        previous=load(a.prior_frozen)
        assert sha(Path(previous['backend']))==previous['backend_sha256'];prior_verified=True
    evidence=dict(passed=True,new_network_executions=4,network_endpoints=128,synthetic_only=True,
        per_group_ep_size=2,experts_per_group=4,shared_group_spans=spans,
        shared_single_matches_original_single=True,only_capture_capacity_difference=[32*2**20,64*2**20],
        global_packet_capture_identical_in_single_mode=True,group_final_memory_unchanged_under_loss=True,
        fault_was_one_zero_token_count_packet=True,unfaulted_group_change_ns=0,
        shared_audit_tamper_rejections=changes,native_before_fabric_rejections=native,
        old_study_backend_unmodified=prior_verified,backend_sha256=sha(backend),
        inputs={str(path.relative_to(root)):sha(path) for path in root.glob('*/execution.json')},
        source_sha256=sha(Path(__file__)),algorithm_innovation_validated=False,real_ep32_shared_execution_validated=False,
        scope='Functional platform evidence only; no inference/model/GPU performance or shared-load benefit claim')
    (out/'verification.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(json.dumps({key:evidence[key] for key in ('passed','new_network_executions','shared_single_matches_original_single','unfaulted_group_change_ns')}))


if __name__=='__main__':main()
