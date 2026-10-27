"""Match the specific returned-value issue to cached, pinned MoE call sites."""
import argparse
import ast
import hashlib
from pathlib import Path
import shutil

from experiments.rc_completion_reachability.analysis.sources import load, save, sha, git_hash


def execute(repo,stage):
    base=repo/'data/research/deepep_modern_20261022';tree=load(base/'tree.json')
    assert not tree['truncated']
    paths=('deep_ep/include/deep_ep/comm/handle.cuh',
           'deep_ep/include/deep_ep/impls/ep/hybrid_dispatch.cuh')
    descriptions={}
    for path in (base/'file-sources.json',base/'file-sources-completion.json'):
        if path.exists():
            for row in load(path)['sources']:descriptions[row['path']]=row
    entries={r['path']:r for r in tree['tree'] if r['type']=='blob'}
    target=stage/'observed/moe-relevance';target.mkdir();rows=[]
    for name in paths:
        path=base/'sources'/name;raw=path.read_bytes();row=descriptions[name]
        assert row['commit']=='f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
        assert len(raw)==row['bytes'] and sha(path)==row['sha256']
        assert git_hash('blob',raw)==row['git_blob_sha']==entries[name]['sha']
        shutil.copyfile(path,target/Path(name).name)
        rows.append(dict(path=name,commit=row['commit'],sha256=row['sha256'],
             git_blob=row['git_blob_sha'],source_identity='existing cached pinned-ref/tree-entry and blob; no new remote revision verification'))
    source=repo/'python/moe_ub_cosim/modern_native.py';shutil.copyfile(source,target/source.name)
    tree_ast=ast.parse(source.read_text());node,=[n for n in ast.walk(tree_ast) if isinstance(n,ast.FunctionDef) and n.name=='_advance_step']
    keys=set()
    for item in ast.walk(node):
        if isinstance(item,ast.Subscript) and isinstance(item.value,ast.Name) and item.value.id=='row' and isinstance(item.slice,ast.Constant):keys.add(item.slice.value)
        if isinstance(item,ast.Call) and isinstance(item.func,ast.Attribute) and isinstance(item.func.value,ast.Name) and item.func.value.id=='row' and item.func.attr=='get':
            assert isinstance(item.args[0],ast.Constant);keys.add(item.args[0].value)
    assert keys=={'operation','event','time_ns'},keys
    checks={
        'tail_signal_void_api':('handle.cuh','void red_add_rel(dtype_t* sym_ptr'),
        'remote_tail_uses_gin_signal':('handle.cuh','ncclGin_VASignalAdd(nccl_window'),
        'actual_hybrid_tail_call':('hybrid_dispatch.cuh','gin.red_add_rel<ncclTeamTagRail>(ptr, signaled_tail - old_signaled_tail, lane_idx);'),
        'returned_slot_is_local_scaleup_atomic':('hybrid_dispatch.cuh','stored_dst_slot_idx = atomicAdd(workspace_layout.get_scaleup_atomic_sender_counter()'),
        'tail_consumer_reads_target_memory':('hybrid_dispatch.cuh','ptx::ld_acquire_sys<int64_t>(')}
    excerpts=[]
    for name,(file,anchor) in checks.items():
        lines=(target/file).read_text().splitlines();hits=[i for i,line in enumerate(lines) if anchor in line];assert len(hits)==1,(name,hits)
        i=hits[0];excerpts.append(dict(name=name,file=file,line=i+1,text='\n'.join(lines[max(0,i-2):i+4])))
    save(target/'evidence.json',dict(passed=True,source_files=rows,platform_source_sha256=sha(source),
        controller_callback_row_fields=sorted(keys),excerpts=excerpts,
        direct_MoE_impact_from_missing_atomic_old_value_proven=False,
        scope='specific Hybrid tail notification and local slot call sites; not all MoE kernels/providers',
        warning='void API still needs correct atomic execution/ordering and transport completion; never delete Atomic ACK or infer wire format from this API'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();execute(a.repository.resolve(),a.stage.resolve())
