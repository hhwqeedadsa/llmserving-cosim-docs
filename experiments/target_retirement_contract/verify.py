"""Independent byte/excerpt and oracle checks for a provider contract report."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def need(ok,reason):
    if not ok:raise AssertionError(reason)


def verify(inputs,build,summary):
    rows=json.loads((inputs/'sources.json').read_text());sources={}
    for row in rows:
        raw=(inputs/'sources'/row['alias']).read_bytes();sources[row['alias']]=raw.decode()
        need(hashlib.sha256(raw).hexdigest()==row['sha256'],'source sha256')
        need(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['git_blob_sha'],'source blob')
    command=json.loads((build/'command.json').read_text());generated=(build/'selected.c').read_text()
    need(hashlib.sha256(generated.encode()).hexdigest()==command['generated_sha256'],'generated source')
    # Use the saved line intervals directly, not the production extraction parser.
    for row in command['selected_original_functions']:
        selected='\n'.join(sources[row['source']].splitlines()[row['line']-1:row['end_line']])
        need(hashlib.sha256(selected.encode()).hexdigest()==row['sha256'],'selected function hash')
        need(generated.count(selected)==1,'original function replaced or duplicated')
    need(summary['source_files']==len(rows)==12 and len(summary['claims'])==16,'source/claim coverage')
    claims={r['id']:r for r in summary['claims']}
    need(len(claims)==16,'duplicate claim identity')
    for row in claims.values():
        raw='\n'.join(sources[row['source']].splitlines()[row['line']-1:row['end_line']])
        need(hashlib.sha256(raw.encode()).hexdigest()==row['function_sha256'],'claim excerpt')
    need(claims['mlx5_strong_fence']['status']=='documented_driver_contract','driver contract scope')
    need('Fence all DMA on the MR' in sources['mlx5_umr.c'],'strong fence omitted')
    need('and any DMA in progress will be completed' in sources['mlx5_umr.c'],'DMA completion omitted')
    context=claims['deepep_window_granularity']
    excerpt='\n'.join(sources[context['source']].splitlines()[context['line']-1:context['end_line']])
    need('num_workspace_bytes + num_gpu_buffer_bytes' in excerpt and
         'ncclCommWindowRegister(comm, raw_window_ptr, symmetric_memory->num_sym_bytes' in excerpt,
         'main window extent or registration omitted')
    expected=0
    for historical in range(256):expected+=len(set(range(256))-{historical})
    need(summary['harness']['key_choice_checks']==expected==65280,'finite key oracle')
    checks=('fast_invalidate_existing_reference_can_copy','user_mr_fast_invalidate_rejected',
        'normal_rxe_dereg_waits_for_reference','rxe_timeout_cleanup_error_not_propagated',
        'fast_mr_reused_key_accepted','mlx5_disabling_wqe_and_wait_call_verified','mlx5_wait_error_propagated',
        'mlx5_internal_error_skips_post')
    need(all(summary['harness'][k] is True for k in checks),'recorded branch evidence')
    need(not summary['harness']['actual_kernel_or_hardware_executed'] and not summary['harness']['dma_retirement_measured'],'hardware overclaim')
    need(summary['decision']['weak_key_change_not_valid_mlx5_success_baseline'],'weakened strong baseline')
    need(not summary['decision']['per_expert_free_revoke_supported_by_selected_modern_path'],'invented expert authority')
    need(not summary['decision']['paper_innovation_established'],'paper overclaim')
    return dict(schema_version='target-retirement-verification/1',source_files=12,exact_original_functions=12,
        claim_excerpts=16,key_oracle_pairs=expected,platform_or_hardware_executed=False)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','build','summary','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();summary=json.loads(a.summary.read_text());report=verify(a.inputs,a.build,summary)
    rejected=[]
    for kind in ('weaken_fence','claim_hardware','per_expert_authority','key_count'):
        wrong=deepcopy(summary)
        if kind=='weaken_fence':wrong['decision']['weak_key_change_not_valid_mlx5_success_baseline']=False
        elif kind=='claim_hardware':wrong['harness']['dma_retirement_measured']=True
        elif kind=='per_expert_authority':wrong['decision']['per_expert_free_revoke_supported_by_selected_modern_path']=True
        else:wrong['harness']['key_choice_checks']+=1
        try:verify(a.inputs,a.build,wrong)
        except AssertionError:rejected.append(kind)
        else:raise AssertionError('tampering accepted: '+kind)
    report['tampering_rejected']=rejected
    a.output.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':main()
