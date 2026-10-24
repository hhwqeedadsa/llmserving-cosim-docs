/* These call orders inspect original function boundaries, not supported app
 * concurrency or real kernel/hardware execution. Expected bytes are independent
 * constants; backend copy, timing, references and firmware are substitutes.
 */
int main(void){
    unsigned key_choices=0;
    for(unsigned last=0;last<256;++last)for(unsigned next=0;next<256;++next)if(next!=last){
        random_values[0]=last;random_values[1]=next;random_index=0;
        assert(rxe_get_next_key(last)==next && random_index==2);++key_choices;
    }
    assert(key_choices==65280);
    setup(IB_MR_TYPE_MEM_REG);u32 old=region.rkey;
    pending=lookup_mr(&pd,IB_ACCESS_REMOTE_WRITE,old,RXE_LOOKUP_REMOTE);
    assert(pending==&region && region.elem.ref_cnt.refs==2);
    assert(rxe_invalidate_mr(&qp,old)==0 && region.state==RXE_MR_STATE_FREE);
    assert(lookup_mr(&pd,IB_ACCESS_REMOTE_WRITE,old,RXE_LOOKUP_REMOTE)==NULL);
    u8 value=0x11;assert(rxe_mr_copy(pending,4096,&value,1,RXE_TO_MR_OBJ)==0);
    assert(region.data[0]==0x11);rxe_put(pending);pending=NULL;
    assert(region.elem.ref_cnt.refs==1);
    // USER is the ordinary user-registration type, not fast MEM_REG.
    setup(IB_MR_TYPE_USER);old=region.rkey;
    assert(rxe_invalidate_mr(&qp,old)==-EINVAL && region.state==RXE_MR_STATE_VALID);
    pending=lookup_mr(&pd,IB_ACCESS_REMOTE_WRITE,old,RXE_LOOKUP_REMOTE);
    assert(pending==&region);rxe_put(pending);pending=NULL;
    setup(IB_MR_TYPE_MEM_REG);region.num_mw.value=1;
    assert(rxe_invalidate_mr(&qp,region.rkey)==-EINVAL && region.state==RXE_MR_STATE_VALID);
    region.num_mw.value=0;assert(rxe_invalidate_mr(&qp,region.rkey+1)==-EINVAL);
    // The normal deregistration waits until an existing reference releases.
    setup(IB_MR_TYPE_USER);
    pending=lookup_mr(&pd,IB_ACCESS_REMOTE_WRITE,region.rkey,RXE_LOOKUP_REMOTE);
    assert(rxe_dereg_mr(&region.ibmr,NULL)==0);
    assert(write_during_wait && region.data[0]==0x42 && wait_calls==1 && wait_budget==200);
    assert(region.elem.ref_cnt.refs==0 && cleanup_calls==1 && cleanup_with_refs==0 && free_calls==1 && cleanup_errors==0);
    // Exceptional timeout: original dereg logs cleanup error, then returns 0.
    // We record cleanup/free requests, and do not perform any use-after-free.
    setup(IB_MR_TYPE_USER);force_timeout=true;
    pending=lookup_mr(&pd,IB_ACCESS_REMOTE_WRITE,region.rkey,RXE_LOOKUP_REMOTE);
    assert(rxe_dereg_mr(&region.ibmr,NULL)==0);
    assert(region.elem.ref_cnt.refs==1 && cleanup_with_refs==1 && cleanup_errors==1 && warnings==1 && free_calls==1);
    setup(IB_MR_TYPE_USER);region.num_mw.value=1;
    assert(rxe_dereg_mr(&region.ibmr,NULL)==-EINVAL && cleanup_calls==0 && free_calls==0);
    // Reusing a fast-MR key's low byte is not rejected by REG_MR itself.
    setup(IB_MR_TYPE_MEM_REG);old=region.rkey;
    struct rxe_send_wqe wqe={0};wqe.wr.wr.reg.mr=&region.ibmr;
    wqe.wr.wr.reg.access=RXE_ACCESS_REMOTE;wqe.wr.wr.reg.key=old+1;
    assert(rxe_invalidate_mr(&qp,old)==0 && rxe_reg_fast_mr(&qp,&wqe)==0);
    assert(lookup_mr(&pd,IB_ACCESS_REMOTE_WRITE,old,RXE_LOOKUP_REMOTE)==NULL);
    assert(rxe_invalidate_mr(&qp,old+1)==0);wqe.wr.wr.reg.key=old;
    assert(rxe_reg_fast_mr(&qp,&wqe)==0);
    pending=lookup_mr(&pd,IB_ACCESS_REMOTE_WRITE,old,RXE_LOOKUP_REMOTE);assert(pending==&region);rxe_put(pending);
    // Original mlx5 revoke builds a disabling WQE and calls the wait boundary.
    struct mlx5_core_dev core={0};struct mlx5_ib_pd mpd={.pdn=19};
    struct mlx5_ib_dev mdev={.mdev=&core,.umrc={.pd=&mpd}};
    struct mlx5_ib_mr mmr={.dev=&mdev,.mmkey={.key=0x12345678}};
    post_calls=0;post_result=0;assert(mlx5r_umr_revoke_mr(&mmr)==0 && post_calls==1);
    assert(posted_key==0x12345678 && !posted_ignore_free && posted_wqe.ctrl_seg.mkey_mask==6);
    assert(posted_wqe.ctrl_seg.flags==MLX5_UMR_INLINE && posted_wqe.mkey_seg.free==1);
    assert(posted_wqe.mkey_seg.pd==19 && posted_wqe.mkey_seg.qpn==0xffffff && posted_wqe.mkey_seg.mkey_7_0==0x78);
    post_result=-EIO;assert(mlx5r_umr_revoke_mr(&mmr)==-EIO && post_calls==2);
    core.state=MLX5_DEVICE_STATE_INTERNAL_ERROR;assert(mlx5r_umr_revoke_mr(&mmr)==0 && post_calls==2);
    printf("{\"schema_version\":\"provider-source-harness/1\",\"key_choice_checks\":%u,"
      "\"fast_invalidate_existing_reference_can_copy\":true,\"user_mr_fast_invalidate_rejected\":true,"
      "\"normal_rxe_dereg_waits_for_reference\":true,\"rxe_timeout_cleanup_error_not_propagated\":true,"
      "\"fast_mr_reused_key_accepted\":true,\"mlx5_disabling_wqe_and_wait_call_verified\":true,"
      "\"mlx5_wait_error_propagated\":true,\"mlx5_internal_error_skips_post\":true,"
      "\"actual_kernel_or_hardware_executed\":false,\"dma_retirement_measured\":false}\n",key_choices);
    return 0;
}
