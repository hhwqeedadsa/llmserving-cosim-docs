"""Bind retirement claims to pinned source excerpts and recording results."""
import argparse
import hashlib
import json
from pathlib import Path
from extract import function


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','harness','build','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();sources=json.loads((a.inputs/'sources.json').read_text());by={r['alias']:r for r in sources}
    texts={name:(a.inputs/'sources'/name).read_text() for name in by}
    for name,raw in texts.items():assert hashlib.sha256(raw.encode()).hexdigest()==by[name]['sha256']
    claims=[]
    def claim(identity,alias,name,need,description,status='source_path'):
        entry=function(texts[alias],name)
        assert all(s in entry['text'] for s in need),(identity,need)
        claims.append(dict(id=identity,status=status,source=alias,function=name,line=entry['line'],end_line=entry['end_line'],
            function_sha256=hashlib.sha256(entry['text'].encode()).hexdigest(),commit=by[alias]['commit'],
            url=by[alias]['url'],description=description))
        return entry['text']
    claim('fast_invalidate_type','rxe_mr.c','rxe_invalidate_mr',
        ['mr->ibmr.type != IB_MR_TYPE_MEM_REG','mr->state = RXE_MR_STATE_FREE','atomic_read(&mr->num_mw) > 0'],
        'RXE fast invalidate only accepts MEM_REG and rejects bound MWs; do not apply to ordinary USER MR')
    lookup=claim('lookup_authority','rxe_mr.c','lookup_mr',
        ['mr->rkey != key','mr_pd(mr) != pd','mr->state != RXE_MR_STATE_VALID','rxe_pool_get_index'],
        'New lookup checks full key, PD, access and VALID state after acquiring a reference')
    copy=claim('copy_after_lookup','rxe_mr.c','rxe_mr_copy',['rxe_mr_copy_xarray','mr_check_range'],
        'Selected copy dispatcher has no state recheck; recording call order reaches copy after fast invalidation')
    assert 'mr->state' not in copy and 'rxe_put(mr)' in lookup
    claim('responder_reference','rxe_resp.c','check_rkey',['lookup_mr(qp->pd, access, rkey','qp->resp.mr = mr'],
        'Responder holds the resolved MR reference before EXECUTE; this is not a measured concurrent-app witness')
    claim('responder_copy','rxe_resp.c','write_data_in',['rxe_mr_copy(qp->resp.mr'],
        'Actual responder write calls copy with the held reference')
    cleanup=claim('rxe_drain','rxe_pool.c','__rxe_cleanup',
        ['xa_erase(xa, elem->index)','__rxe_put(elem)','wait_for_completion_timeout','pool->cleanup(elem)','err = -EINVAL'],
        'Normal cleanup hides lookup, drops owner reference and waits; exceptional timeout still invokes cleanup')
    assert cleanup.index('xa_erase(')<cleanup.index('__rxe_put(')<cleanup.index('wait_for_completion_timeout(')<cleanup.index('pool->cleanup(elem)')
    dereg=claim('rxe_timeout_return','rxe_verbs.c','rxe_dereg_mr',
        ['cleanup_err = rxe_cleanup(mr)','if (cleanup_err)','kfree_rcu_mightsleep(mr)','return 0;'],
        'In v6.12 this wrapper logs cleanup failure and returns 0; timeout control branch recorded, no real UAF executed')
    assert dereg.index('cleanup_err =')<dereg.index('kfree_rcu_mightsleep')<dereg.index('return 0;')
    reg=claim('fast_key_reuse','rxe_mr.c','rxe_reg_fast_mr',
        ['(mr->lkey & ~0xff) != (key & ~0xff)','mr->rkey = key','RXE_MR_STATE_FREE'],
        'Fast REG_MR preserves index and accepts reused low key; cannot infer unbounded generation uniqueness')
    assert 'key != mr->rkey' not in reg
    claim('random_key_scope','rxe_mr.c','rxe_get_next_key',['u8 key','key == last_key'],
        'Eight-bit random selector excludes only last_key, not every historical key')
    comment='Upon return the NIC will not be doing any DMA to the pages under the MR'
    assert comment in texts['mlx5_umr.c'] and 'and any DMA in progress will be completed' in texts['mlx5_umr.c']
    claim('mlx5_strong_fence','mlx5_umr.c','mlx5r_umr_revoke_mr',
        ['get_umr_disable_mr_mask','mlx5r_umr_post_send_wait','MLX5_DEVICE_STATE_INTERNAL_ERROR'],
        'Documented MR-wide DMA retirement boundary; original WQE construction/wait call recorded, actual DMA not tested',
        'documented_driver_contract')
    claim('mlx5_wait','mlx5_umr.c','mlx5r_umr_post_send_wait',
        ['wait_for_completion(&umr_context.done)','IB_WC_SUCCESS','MLX5_UMR_STATE_RECOVER'],
        'Waits for local UMR completion; recovery/flush retry branches mean this is not a fixed zero-cost event')
    claim('mlx5_revoke_fallback','mlx5_mr.c','mlx5_revoke_mr',
        ['mlx5r_umr_revoke_mr(mr)','cache_ent_find_and_store','return destroy_mkey(dev, mr)'],
        'Cacheable key attempts DMA-fencing UMR then cache storage; fallback destroys the key')
    md=claim('mlx5_dereg_order','mlx5_mr.c','__mlx5_ib_dereg_mr',
        ['/* Stop DMA */','rc = mlx5_revoke_mr(mr)','ib_umem_release(mr->umem)'],
        'Selected USER MR path revokes before memory release; revocation error returns before that release')
    assert md.index('rc = mlx5_revoke_mr(mr)')<md.index('ib_umem_release(mr->umem)')
    claim('gin_registration_granularity','gin_host_gdaki.cc','ncclGinGdakiRegMrSym',
        ['gdakiRegMr(&mr','IBV_ACCESS_REMOTE_WRITE','IBV_ACCESS_REMOTE_ATOMIC','cComm->allGather(cComm, &rkey'],
        'Selected GDAKI window registers an MR and exchanges its rkey with ranks, not per-expert authority')
    gd=claim('gin_dereg_order','gin_host_gdaki.cc','ncclGinGdakiDeregMrSym',
        ['NCCLCHECK(wrap_ibv_dereg_mr(mr))','gdaki_mhandle->rkeys_hd_mhandle->deallocate()'],
        'Selected wrapper checks deregistration before deallocating GPU rkey metadata; lower provider guarantee still matters')
    assert gd.index('wrap_ibv_dereg_mr')<gd.index('->deallocate()')
    ctx=texts['deepep_context.cpp'];needle='ncclCommWindowRegister(comm, raw_window_ptr, symmetric_memory->num_sym_bytes'
    assert needle in ctx and 'num_workspace_bytes + num_gpu_buffer_bytes' in ctx
    first=ctx[:ctx.index('num_workspace_bytes + num_gpu_buffer_bytes')].count('\n')+1
    last=ctx[:ctx.index(needle)].count('\n')+1
    excerpt='\n'.join(ctx.splitlines()[first-1:last])
    claims.append(dict(id='deepep_window_granularity',status='source_path',source='deepep_context.cpp',
        line=first,end_line=last,function_sha256=hashlib.sha256(excerpt.encode()).hexdigest(),
        commit=by['deepep_context.cpp']['commit'],url=by['deepep_context.cpp']['url'],
        description='Selected main symmetric window covers workspace plus GPU buffer; optional storage windows are separate; not per-expert revoke'))
    harness=json.loads(a.harness.read_text());assert harness['actual_kernel_or_hardware_executed'] is False
    assert harness['key_choice_checks']==256*255 and harness['dma_retirement_measured'] is False
    build=json.loads((a.build/'command.json').read_text());assert len(build['selected_original_functions'])==12
    summary=dict(schema_version='target-retirement-analysis/1',source_files=len(sources),claims=claims,harness=harness,
        selected_original_functions=12,platform_unchanged=True,new_ns3_networks=0,
        decision=dict(weak_key_change_not_valid_mlx5_success_baseline=True,
            mr_and_fast_mr_and_mw_must_remain_distinct=True,whole_mr_retirement_is_required_strong_baseline=True,
            per_expert_free_revoke_supported_by_selected_modern_path=False,
            hardware_cost_and_deployable_legal_concurrency_validated=False,paper_innovation_established=False))
    a.output.write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n');print(json.dumps(dict(claims=len(claims),source_files=len(sources),decision=summary['decision'])))


if __name__=='__main__':main()
