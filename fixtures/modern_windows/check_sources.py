"""Check fixed source identity and host ABI of the original workspace structs.

CUDA includes/qualifiers are replaced ONLY for a host data-layout compilation.
The original BarrierSignals/EPSignals field and method declarations are intact.
This is not compilation or execution of DeepEP, NCCL, CUDA or a network backend.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import resource
import shutil
import subprocess

from moe_ub_cosim.modern_deepep_windows import workspace_layout

ROOT=Path(__file__).resolve().parents[2]
DEEP='f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
NCCL='12df1a11afad322be5a204a2db890161cbf8131d'


def sha(raw):return hashlib.sha256(raw).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    a=parser.parse_args();out=a.output.resolve()
    assert not out.exists() and shutil.disk_usage(out.parent).free>=8*2**30+8*2**20
    resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU,(60,60))
    out.mkdir();records=[];texts={}
    packages={
        'deepep_windows_20261022':['deep_ep/include/deep_ep/layout/common/barrier.cuh'],
        'deepep_layout_20261022':['deep_ep/include/deep_ep/common/compiled.cuh',
            'deep_ep/include/deep_ep/layout/ep/workspace.cuh'],
        'deepep_modern_20261022':['csrc/buffers/ep.hpp','csrc/kernels/comm/context.cpp',
            'deep_ep/include/deep_ep/comm/handle.cuh','deep_ep/include/deep_ep/comm/barrier.cuh',
            'deep_ep/include/deep_ep/impls/ep/dispatch.cuh','deep_ep/include/deep_ep/impls/ep/combine.cuh'],
        'nccl_gin_20261022':['src/include/nccl_device/impl/gin__funcs.h',
            'src/include/nccl_device/gin/gdaki/gin_gdaki.h','src/transport/net_ib/gdaki/gin_host_gdaki.cc']}
    for package,names in packages.items():
        folder=ROOT/'data/research'/package
        tree=json.loads((folder/'tree.json').read_text());commit=NCCL if package.startswith('nccl') else DEEP
        if package.startswith('nccl'):
            identity=json.loads((folder/'identity.json').read_text())
            for item in identity['requests']:
                assert sha((folder/item['file']).read_bytes())==item['sha256']
            git_commit=json.loads((folder/'commit.json').read_text())
            assert git_commit['sha']==commit and tree['sha']==git_commit['tree']['sha']==identity['tree']
        else:
            identity=json.loads((folder/'tree-source.json').read_text())
            assert identity['commit']==commit and tree['sha']==commit
            assert sha((folder/'tree.json').read_bytes())==identity['requests'][1]['sha256']
        assert not tree['truncated']
        blobs={r['path']:r for r in tree['tree'] if r['type']=='blob'}
        pattern='files-*.json' if package.startswith('nccl') else 'file-sources*.json'
        manifests=[json.loads(p.read_text()) for p in folder.glob(pattern)]
        files={r['path']:r for m in manifests for r in m['sources']}
        for name in names:
            record=files[name];p=folder/'sources'/name;raw=p.read_bytes();blob=blobs[name]
            assert record['status']=='read' and record['commit']==commit and sha(raw)==record['sha256']
            assert len(raw)==blob['size'] and hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==blob['sha']
            records.append(dict(path=name,local_cache=str(p.relative_to(ROOT)),commit=commit,
                bytes=len(raw),sha256=sha(raw),git_blob_sha=blob['sha'],public_url=record['public_url']))
            texts[name]=raw.decode()
    def host_header(text):
        return '\n'.join(line for line in text.splitlines()
                         if not line.startswith('#include') and line!='#pragma once')+'\n'
    compiled=texts['deep_ep/include/deep_ep/common/compiled.cuh']
    constants=[]
    for name in ('kNumRDMAAlignmentBytes','kNumMaxRanks','kNumMaxSignalBytes'):
        matches=re.findall(r'^constexpr int '+name+r' = .*?;',compiled,re.M)
        assert len(matches)==1;constants+=matches
    source='#include <cassert>\n#include <cstddef>\n#include <cstdint>\n#include <iostream>\n'
    source+='#define __forceinline__ inline\n#define __device__\n#define __host__\n'
    source+='#define EP_STATIC_ASSERT static_assert\n#define EP_UNIFIED_ASSERT assert\n'
    source+='namespace deep_ep {\n'+'\n'.join(constants)+'\n}\n'
    source+=host_header(texts['deep_ep/include/deep_ep/layout/common/barrier.cuh'])
    source+=host_header(texts['deep_ep/include/deep_ep/layout/ep/workspace.cuh'])
    source+='int main(){using namespace deep_ep::layout;\n'
    source+='std::cout << "struct " << sizeof(EPSignals) << " " << alignof(EPSignals) << "\\n";\n'
    fields=workspace_layout()['members']
    for name in fields:
        source+='std::cout << "'+name+' " << offsetof(EPSignals,'+name+') << " " << sizeof(((EPSignals*)nullptr)->'+name+') << "\\n";\n'
    source+='}\n';(out/'layout.cc').write_text(source)
    commands=[]
    for name,argv in [('compile',['g++','-std=c++17','-Wall','-Wextra','-Werror',str(out/'layout.cc'),'-o',str(out/'layout')]),
                      ('execute',[str(out/'layout')])]:
        result=subprocess.run(argv,cwd=ROOT,capture_output=True,text=True,timeout=30)
        (out/(name+'.stdout')).write_text(result.stdout);(out/(name+'.stderr')).write_text(result.stderr)
        commands.append(dict(argv=argv,cwd=str(ROOT),exitcode=result.returncode))
        assert result.returncode==0,result.stderr
    lines=(out/'execute.stdout').read_text().splitlines()
    _,size,alignment=lines[0].split();expected=workspace_layout()
    assert int(size)==expected['struct_bytes'] and int(alignment)==64
    for line in lines[1:]:
        name,offset,size=line.split()
        assert dict(byte_offset=int(offset),bytes=int(size))==fields[name]
    excerpts=[]
    anchors=[
        ('deep_ep/include/deep_ep/layout/ep/workspace.cuh', 'return signals.scaleup_rank_expert_count[not kIsSendBuffer];', 1),
        ('deep_ep/include/deep_ep/layout/ep/workspace.cuh', 'return get_scaleup_rank_expert_count_ptr<kIsSendBuffer>() + num_scaleup_ranks;', 1),
        ('csrc/kernels/comm/context.cpp', 'NCCL_CHECK(ncclCommWindowRegister(comm, raw_window_ptr,', 4),
        ('csrc/kernels/comm/context.cpp', 'reqs.ginSignalCount = num_ranks + 2 * 2;', 1),
        ('deep_ep/include/deep_ep/comm/handle.cuh', '// NOTES: signals are rolling counters,', 6),
        ('deep_ep/include/deep_ep/impls/ep/dispatch.cuh', 'workspace_layout.get_scaleup_rank_expert_count_ptr<false>()[i] = 0;', 3),
        ('src/include/nccl_device/impl/gin__funcs.h', 'gin->_signalShadows = comm.ginSignalShadows + contextIndex * comm.ginSignalCount;', 1),
        ('src/transport/net_ib/gdaki/gin_host_gdaki.cc', 'signals_table->allocate(num_signals * ncontexts, nranks)', 1),
        ('src/include/nccl_device/gin/gdaki/gin_gdaki.h', 'return {loadConst(&gdaki->signals_table.buffer) + signalId, 0};', 1)]
    for name,needle,count in anchors:
        lines=texts[name].splitlines();positions=[i for i,line in enumerate(lines) if needle in line]
        assert len(positions)==1,(name,needle,positions)
        i=positions[0];excerpts.append(dict(path=name,first_line=i+1,excerpt='\n'.join(lines[i:i+count])))
    collection=json.loads((ROOT/'data/research/deepep_windows_20261022/file-sources.json').read_text())
    verification=dict(passed=True,sources=records,source_files=len(records),
        original_host_struct_fields_verified=len(fields),workspace=expected,commands=commands,
        excerpts=excerpts,
        host_only=True,cuda_compiled=False,network_executed=False,hardware_calibrated=False,
        source_collection_new_responses=len(collection['sources']),
        source_collection_new_response_bytes=sum(r['response_bytes'] for r in collection['sources']),
        source_collection_record='data/research/deepep_windows_20261022/file-sources.json')
    (out/'source_basis.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(passed=True,source_files=len(records),host_members=len(fields),
        workspace_struct_bytes=expected['struct_bytes'],workspace_allocation_bytes=expected['allocation_bytes'])))


if __name__=='__main__':main()
