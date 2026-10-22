"""Source capacity audit and one fixed software-credit removal experiment.

Research only: public byte-stream execution is not a modern GIN runtime.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from feedback_parameters import analyze as analyze_parameters

ROOT = Path(__file__).resolve().parents[2]
DEEP = 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
NCCL = '12df1a11afad322be5a204a2db890161cbf8131d'
DOCA = 'src/transport/net_ib/gdaki/doca-gpunetio/include/'


def load(path): return json.loads(path.read_text())
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def source_audit():
    """Validate cached primary source identities; extract reviewable source passages."""
    selections = [
        ('deepep_layout_20261022', 'docs/review_artifacts/modern_layout_20261022/sources.json', DEEP,
         [('deep_ep/include/deep_ep/common/compiled.cuh', 'constexpr int kDefaultQPDepth = 1024;', 1)]),
        ('deepep_modern_20261022', 'docs/review_artifacts/deepep_modern_20261022/sources.json', DEEP, [
            ('csrc/kernels/comm/context.cpp', 'reqs.ginQueueDepth = qp_depth > 0 ? qp_depth : kDefaultQPDepth;', 1),
            ('deep_ep/include/deep_ep/comm/handle.cuh', 'num_bytes, remote_action, ncclGin_None(), ncclCoopThread(), ncclGin_None(),', 2),
            ('deep_ep/include/deep_ep/comm/barrier.cuh', 'barrier_gin.signal<team_t>', 3),
            ('deep_ep/include/deep_ep/impls/ep/dispatch.cuh', 'comm::gpu_barrier<kIsScaleupNVLink, 1, kNumRanks,', 3),
            ('deep_ep/include/deep_ep/impls/ep/combine.cuh', 'comm::gpu_barrier<kIsScaleupNVLink, 1, kNumRanks,', 3)]),
        ('nccl_gin_20261022', 'docs/review_artifacts/nccl_gin_20261022/sources.json', NCCL, [
            ('src/transport/net_ib/gdaki/gin_host_gdaki.cc', 'gdaki_ctx->qp_sq_size = queueDepth > 0 ?', 8),
            ('src/include/nccl_device/gin/gdaki/gin_gdaki.h', 'doca_gpu_dev_verbs_put<resource_sharing_mode>(qp, raddr, laddr, bytes, codeOpt);', 2),
            (DOCA+'device/doca_gpunetio_dev_verbs_onesided.cuh', 'num_chunks = num_chunks > 1 ? num_chunks : 1;', 14),
            (DOCA+'device/doca_gpunetio_dev_verbs_qp.cuh', 'const uint16_t nwqes = doca_gpu_dev_verbs_load_const(&qp->sq_wqe_num);', 5),
            (DOCA+'common/doca_gpunetio_verbs_def.h', '#define DOCA_GPUNETIO_VERBS_MAX_TRANSFER_SIZE_SHIFT 30', 3),
        ]),
    ]
    records = []
    for cache_name, manifest_name, commit, wanted in selections:
        cache = ROOT / 'data/research' / cache_name
        manifest = load(ROOT / manifest_name)
        tree = load(cache / 'tree.json')
        assert not tree['truncated']
        if commit == NCCL:
            assert load(cache / 'commit.json')['sha'] == commit
            assert load(cache / 'commit.json')['tree']['sha'] == tree['sha']
        else:
            assert tree['sha'] == commit
        blobs = {x['path']: x for x in tree['tree'] if x['type'] == 'blob'}
        for name, anchor, lines in wanted:
            item = next(x for x in manifest['sources'] if x['path'] == name)
            path = cache / 'sources' / name
            raw = path.read_bytes()
            assert item['commit'] == commit and sha(path) == item['sha256']
            assert len(raw) == item['bytes'] == blobs[name]['size']
            assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == blobs[name]['sha'] == item['git_blob_sha']
            text = raw.decode(); source_lines = text.splitlines()
            excerpts = [dict(first_line=i+1, text='\n'.join(source_lines[i:i+lines]))
                        for i,line in enumerate(source_lines) if anchor in line]
            assert excerpts, (name, anchor)
            records.append(dict(path=str(path.relative_to(ROOT)), sha256=sha(path), bytes=len(raw),
                commit=commit, git_blob_sha=item['git_blob_sha'], url=item['public_url'],
                manifest=manifest_name, tree=str((cache/'tree.json').relative_to(ROOT)), excerpts=excerpts))
    return dict(sources=records, requested_sq_depth=1024, maximum_put_bytes_per_wqe=2**30,
        source_identity_verified=True, hardware_readback=False, cuda_or_nccl_executed=False,
        assumptions=['effective SQ size 1024', 'initially empty SQ', 'fresh direct, no inter-rank NVLink',
                     'one dispatch and local-reduction combine', 'no extra counters or unrelated jobs'],
        conclusion_scope='Conditional SQ capacity bound; not actual provider timing or GPU scheduling')


def prepare(args):
    out, baseline = args.output.resolve(), args.baseline.resolve()
    assert not out.exists()
    source = source_audit()
    manifest = load(args.manifest)
    expected, pair_counts, tokens = [], Counter(), []
    for src, item in enumerate(manifest['files']):
        path = ROOT / item['file']['path']
        assert sha(path) == item['file']['sha256'] and path.stat().st_size < 4*2**20
        choices = load(path)[0]['4'][:32]; tokens.append(len(choices))
        for chosen in choices:
            assert len(chosen) == len(set(chosen)) == 8 and all(0 <= e < 256 for e in chosen)
            for dst in sorted({e//64 for e in chosen} - {src}):
                expected.append(dict(id=len(expected), stream=src*4+dst, bytes=14464, after=[]))
                pair_counts[src,dst] += 1
    assert tokens == [32,28,10,6] and len(expected) == 180
    per_pair = []
    for src in range(4):
        for dst in range(4):
            data = pair_counts[src,dst]
            # No data GIN for self; bulk count and barrier requests still use self GIN.
            components = dict(dispatch_put=data, combine_put=pair_counts[dst,src], barrier_signal=4,
                              rank_count=int(src!=dst), expert_count_block=1)
            per_pair.append(dict(src=src, dst=dst, components=components,
                                 cold_sq_work_upper_bound=sum(components.values())))
    total = sum(r['cold_sq_work_upper_bound'] for r in per_pair)
    assert total == 452 and total < source['requested_sq_depth']
    out.mkdir(); observed=out/'observed'; truth=out/'truth'; observed.mkdir(); truth.mkdir()
    save(truth/'source-audit.json', source)
    save(truth/'capacity.json', dict(source_token_counts=tokens, remote_dispatch_writes=180,
        total_provider_work=total, per_sender_peer_upper_bounds=per_pair,
        maximum_per_sender_peer=max(r['cold_sq_work_upper_bound'] for r in per_pair),
        effective_depth_condition=1024, must_recycle_sq_slot_from_empty=False,
        network_packet_count_is_not_sq_count=True, modeled_modern_network=False))
    shutil.copyfile(args.manifest, truth/'manifest.json')
    for name in ('topology.json', 'protocol-p001.json'):
        shutil.copyfile(baseline/'input/observed'/name, observed/name)
    cases=[]
    for depth in (1,8,1024):
        name=f'd{depth}-ai40-p001'
        oldname=f'd{min(depth,8)}-ai40-p001'
        work=load(baseline/'input/observed'/(oldname+'.json'))
        assert work['work']==expected
        for stream in work['streams']: stream['queue_depth']=depth
        save(observed/(name+'.json'),work)
        cases.append(dict(name=name, depth=depth, additive_bps=40000000, pmax=0.01,
                          protocol='protocol-p001.json', baseline_case=name if depth!=1024 else None))
    save(observed/'index.json', dict(schema_version='moe-queue-underfill/1', cases=cases,
        artifacts={p.name:dict(sha256=sha(p),bytes=p.stat().st_size) for p in sorted(observed.iterdir())},
        source_token_counts=tokens, work_count=180, payload_bytes=2603520,
        software_credits_are_hardware_sq=False, hardware_calibrated=False))
    print(json.dumps(dict(prepared=True,provider_work_upper_bound=total,
                         maximum_pair_work=max(r['cold_sq_work_upper_bound'] for r in per_pair))))


def run(args):
    observed, baseline, backend, out = (p.resolve() for p in (args.observed,args.baseline,args.backend,args.output))
    assert not out.exists()
    index=load(observed/'index.json')
    for name,item in index['artifacts'].items(): assert sha(observed/name)==item['sha256']
    out.mkdir(); commands=[]; executed=[]; binary_sha=sha(backend)
    def cli(label, tail):
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,tail)]
        row=dict(label=label,command=command,cwd=str(ROOT));commands.append(row);started=time.monotonic()
        try:
            with (out/(label+'.stdout.log')).open('w') as stdout,(out/(label+'.stderr.log')).open('w') as stderr:
                result=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=60)
            row['exit_code']=result.returncode
            return result.returncode
        finally:
            row['wall_seconds']=time.monotonic()-started;save(out/'commands.json',commands)
    assert cli('topology',['topology','--config',observed/'topology.json','--output',out/'topology'])==0
    assert sha(out/'topology/topology.json')==sha(baseline/'run/topology/topology.json')
    previous=load(baseline/'analysis/summary.json'); failure=None
    for case in index['cases']:
        name=case['name']; assert sha(backend)==binary_sha
        entry=dict(name=name,kind='new');executed.append(entry)
        if case['baseline_case']:
            old=baseline/'run'/name
            assert sha(observed/(name+'.json'))==sha(baseline/'input/observed'/(name+'.json'))
            assert sha(observed/case['protocol'])==sha(baseline/'input/observed'/case['protocol'])
            inputs_path=old/'inputs.json'
            if not inputs_path.exists():inputs_path=old/'prior-inputs.json'
            inputs=load(inputs_path); command=inputs['command']
            for i,path in enumerate((backend,baseline/'run/topology/topology.json',observed/case['protocol'],observed/(name+'.json'))):
                assert sha(path)==inputs['hashes'][command[i]]
            expected=next(c for c in previous['cases'] if c['name']==name)
            assert sha(old/'result.json')==expected['result_sha256']
            r=load(old/'result.json'); assert r['passed'] and r['drained'] and r['completed_work']==180
            target=out/name;target.mkdir();shutil.copyfile(old/'result.json',target/'result.json')
            shutil.copyfile(inputs_path,target/'prior-inputs.json')
            entry.update(kind='reused_success',result_sha256=sha(old/'result.json'),exit_code=0)
            save(target/'reuse.json',entry)
        else:
            entry['exit_code']=cli(name,['rdma-stream-probe','--topology',out/'topology','--protocol',observed/case['protocol'],
                '--config',observed/(name+'.json'),'--backend',backend,'--output',out/name])
            if entry['exit_code']:
                failure='public_cli_failed';entry['result_available']=(out/name/'result.json').is_file();break
            r=load(out/name/'result.json')
        if not r['packet_capture']['complete']:failure='capture_truncated'
        elif r['fault_drops']:failure='fault_drop'
        elif any(s['source_payload_bytes']!=s['submitted_bytes'] for s in r['persistent_transport']['streams']):failure='retransmission'
        elif any(p['pfc_pause'] or p['pfc_resume'] for p in r['ports']):failure='unsupported_pfc'
        if failure: break
    save(out/'execution.json',dict(completed=failure is None,executed=executed,stop_reason=failure,
        unexecuted=[c['name'] for c in index['cases'][len(executed):]],backend_path=str(backend),backend_sha256=binary_sha,
        new_network_runs=sum(c['kind']=='new' for c in executed),reused_successful_runs=sum(c['kind']=='reused_success' for c in executed),
        base_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),retry=False))
    print(json.dumps(dict(completed=failure is None,stop_reason=failure)))
    if failure: raise SystemExit(1)


def analyze(args):
    # Reuse only the public-result metrics reader; this function never opens truth.
    analyze_parameters(args)
    summary=load(args.output/'summary.json')
    for row in summary['cases']:
        result=args.run/row['name']/'result.json'
        if not result.exists(): continue
        works=load(result)['persistent_transport']['work']
        row['published_at_zero']=sum(w['published_ns']==0 for w in works)
        row['credit_delayed_work']=sum(w['published_ns']>w['ready_ns'] for w in works)
        row['maximum_credit_wait_ns']=max((w['published_ns']-w['ready_ns'] for w in works),default=0)
    summary.update(software_credits_are_hardware_sq=False,
                   source='Known inference route window; software-credit removal, not a modern provider execution')
    new = next((r for r in summary['cases'] if r['depth']==1024 and r['status']=='complete'), None)
    summary['capacity_comparisons'] = [dict(low_case=r['name'],high_case=new['name'],
        ack_delta_ns=new['acked_ns']-r['acked_ns'],
        same_static_service_bound=new['minimum_port_service_ns']==r['minimum_port_service_ns'])
        for r in summary['cases'] if new is not None and r['depth']!=1024 and r['status']=='complete']
    save(args.output/'summary.json',summary)


def explain(args):
    """Describe actual per-QP feedback overlap; do not invent counterfactual timing."""
    assert not args.output.exists();records=[]
    execution=load(args.run/'execution.json')
    for case in execution['executed']:
        path=args.run/case['name']/'result.json'
        if case['exit_code'] or not path.exists():continue
        r=load(path);t=r['persistent_transport'];events=t['dcqcn_reference']['events']
        packets=defaultdict(list);notices=defaultdict(list)
        for row in t['source_packets']:
            packet=dict(zip(t['source_packet_columns'],row));packets[packet['stream']].append(packet)
        for event in events:
            if event['kind']=='cnp_received':notices[event['stream']].append(event['time_ns'])
        rates=defaultdict(list)
        for row in t['source_rate_capture']['samples']:
            sample=dict(zip(t['source_rate_capture']['columns'],row))
            if sample['event']==0:rates[sample['stream']].append(sample['rate_bps'])
        rows=[]
        for sid,source in sorted(packets.items()):
            last=max(p['time_ns'] for p in source);first=min(notices[sid],default=None)
            rows.append(dict(stream=sid,last_data_tx_ns=last,first_cnp_rx_ns=first,
                cnp_before_last_data_tx=sum(tm<last for tm in notices[sid]),
                cnp_at_last_data_tx=sum(tm==last for tm in notices[sid]),
                cnp_after_last_data_tx=sum(tm>last for tm in notices[sid]),
                minimum_rate_at_actual_tx_bps=min(rates[sid]),
                post_first_feedback_packets=sum(first is not None and p['time_ns']>=first for p in source)))
        ack=max(w['acked_ns'] for w in t['work'])
        all_notices=[e for e in events if e['kind']=='cnp_received']
        last_notice=max((e['time_ns'] for e in all_notices),default=None)
        records.append(dict(case=case['name'],streams=rows,
            last_business_ack_ns=ack,physical_drain_ns=r['finish_ns'],
            drain_after_business_ack_ns=r['finish_ns']-ack,last_cnp_rx_ns=last_notice,
            cnp_after_global_business_ack=sum(e['time_ns']>ack for e in all_notices),
            cnp_before_own_last_tx=sum(s['cnp_before_last_data_tx'] for s in rows),
            cnp_at_own_last_tx=sum(s['cnp_at_last_data_tx'] for s in rows),
            cnp_after_own_last_tx=sum(s['cnp_after_last_data_tx'] for s in rows)))
    args.output.mkdir();save(args.output/'feedback.json',dict(cases=records,public_results_only=True,
        interpretation='Actual CNP/TX overlap; after-own-last-TX notifications cannot pace later data in this finite condition'))
    print(json.dumps(dict(explained=True,conditions=len(records))))


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='stage',required=True)
    p=sub.add_parser('prepare')
    for key in ('baseline','manifest','output'):p.add_argument('--'+key,type=Path,required=True)
    p=sub.add_parser('run')
    for key in ('observed','baseline','backend','output'):p.add_argument('--'+key,type=Path,required=True)
    p=sub.add_parser('analyze')
    for key in ('observed','run','output'):p.add_argument('--'+key,type=Path,required=True)
    p=sub.add_parser('explain')
    for key in ('run','output'):p.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();globals()[args.stage](args)


if __name__=='__main__':main()
