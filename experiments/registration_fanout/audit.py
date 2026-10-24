"""Prepare pinned cached sources, compile exact functions, and summarize CPU records."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'experiments/target_retirement_contract'))
from extract import function


def sha(raw):return hashlib.sha256(raw).hexdigest()
def save(path,value):path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')


def prepare(out):
    out.mkdir();(out/'sources').mkdir();rows=[]
    for manifest,base,paths in [
        ('docs/review_artifacts/nccl_gin_20261022/sources.json','data/research/nccl_gin_20261022/sources',
         ['src/gin/gin_host.cc','src/transport/net_ib/gdaki/gin_host_gdaki.cc']),
        ('docs/review_artifacts/deepep_modern_20261022/sources.json','data/research/deepep_modern_20261022/sources',
         ['csrc/kernels/comm/context.cpp'])]:
        original=json.loads((ROOT/manifest).read_text())
        for path in paths:
            row=next(r for r in original['sources'] if r['path']==path);raw=(ROOT/base/path).read_bytes()
            blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
            assert sha(raw)==row['sha256'] and blob==row['git_blob_sha']
            alias=Path(path).name;(out/'sources'/alias).write_bytes(raw)
            rows.append(dict(alias=alias,commit=row['commit'],path=path,url=row['public_url'],sha256=sha(raw),git_blob_sha=blob,bytes=len(raw)))
    save(out/'sources.json',rows)
    for project in ('nccl_gin_20261022','deepep_modern_20261022'):
        folder=out/'provenance'/project;folder.mkdir(parents=True)
        for name in ('tree.json','commit.json','ref.json'):
            src=ROOT/'data/research'/project/name
            if src.exists():shutil.copyfile(src,folder/name)
        shutil.copyfile(ROOT/'docs/review_artifacts'/project/'sources.json',folder/'source-manifest.json')
    print(json.dumps(dict(source_files=len(rows),new_downloads=0)))


def build(inputs,out):
    out.mkdir();local=Path(__file__).resolve().parent;rows=[];chunks=[(local/'harness.hpp').read_text()]
    sources=json.loads((inputs/'sources.json').read_text())
    for row in sources:assert sha((inputs/'sources'/row['alias']).read_bytes())==row['sha256']
    for alias,name in [('gin_host.cc','ncclGinRegister'),('gin_host.cc','ncclGinDeregister'),
            ('gin_host_gdaki.cc','ncclGinGdakiDeregMrSym')]:
        selected=function((inputs/'sources'/alias).read_text(),name)
        rows.append(dict(source=alias,name=name,line=selected['line'],end_line=selected['end_line'],sha256=sha(selected['text'].encode())))
        chunks.append(selected['text'])
    chunks.append((local/'harness_main.cc').read_text());generated=out/'selected.cc';generated.write_text('\n\n'.join(chunks))
    command=['g++','-std=c++17','-O1','-Wall','-Wextra','-Werror','-Wno-unused-parameter',str(generated),'-o',str(out/'source-harness')]
    save(out/'build.json',dict(command=command,cwd=str(Path.cwd()),functions=rows,generated_sha256=sha(generated.read_bytes()),
        substitutes=['provider callbacks/MR outcome','host/GPU metadata deletion','backend and device topology','array bounds 4 connections x 2 backends'],
        actual_provider_executed=False))
    subprocess.run(command,check=True)


def analyze(records,out):
    rows=[json.loads(line) for line in records.read_text().splitlines()]
    partial=[i for i,r in enumerate(rows) if r['result'] and r['retired'] and set(r['retired'])!=set(r['created'])]
    all_retired_error=[i for i,r in enumerate(rows) if r['result'] and r['created'] and set(r['created'])==set(r['retired'])]
    registered_prefix=[i for i,r in enumerate(rows) if r['register_result'] and r['created']]
    alias=[i for i,r in enumerate(rows) if len(r['created'])>len({tuple(x[1:]) for x in r['alias_device_mapping'] if x[0] in r['created']})]
    assert partial and all_retired_error and registered_prefix and alias
    result=dict(schema_version='registration-fanout-analysis/1',cases=len(rows),
        source_functions=3,records_sha256=sha(records.read_bytes()),partial_retirement_errors=len(partial),
        all_created_mrs_retired_but_error=len(all_retired_error),registration_error_with_successful_prefix=len(registered_prefix),
        cases_with_multiple_created_aliases_per_device=len(alias),
        witness_rows=dict(partial_retirement=partial[0],all_retired_metadata_error=all_retired_error[0],
            register_prefix=registered_prefix[0],same_device_alias=alias[0]),
        actual_provider_executed=False,new_ns3_networks=0,paper_innovation_established=False,
        scope='synthetic recording calls; no legal application concurrency, real MR retirement or failure rates measured')
    save(out,result);print(json.dumps(result))


def evidence(inputs,out):
    claims=[]
    for alias,name,required in [
        ('gin_host.cc','ncclGinConnectOnce',['localGinDevs[commIdx % nLocalGinDevs]']),
        ('gin_host.cc','ncclGinRegister',['backendIdx * NCCL_GIN_MAX_CONNECTIONS + commIdx','address, size, memType, mrFlags']),
        ('gin_host.cc','ncclGinDeregister',['deregMrSym(backend->ginComms[commIdx], ginHostWins[slot])']),
        ('gin_host_gdaki.cc','ncclGinGdakiRegMrSym',['gdakiRegMr(&mr, cComm->ib.pd, data, size','allGather(cComm, &rkey']),
        ('gin_host_gdaki.cc','ncclGinGdakiDeregMrSym',['NCCLCHECK(wrap_ibv_dereg_mr(mr))','rkeys_hd_mhandle->deallocate()'])]:
        raw=(inputs/'sources'/alias).read_text();entry=function(raw,name)
        assert all(x in entry['text'] for x in required)
        claims.append(dict(source=alias,name=name,line=entry['line'],end_line=entry['end_line'],sha256=sha(entry['text'].encode()),required=required))
    text=(inputs/'sources/gin_host_gdaki.cc').read_text()
    start=text.index('  ncclResult_t deallocate()');last=text.index('  ncclResult_t copy_h_to_d()',start)
    first_line=text[:start].count('\n')+1;last_line=text[:last].count('\n')
    selected='\n'.join(text.splitlines()[first_line-1:last_line]);assert 'NCCLCHECK(ncclCuMemFree' in selected
    claims.append(dict(source='gin_host_gdaki.cc',name='metadata_deallocate',line=first_line,end_line=last_line,
        sha256=sha(selected.encode()),required=['NCCLCHECK(ncclCuMemFree']))
    text=(inputs/'sources/context.cpp').read_text()
    first_line=text[:text.index('num_workspace_bytes + num_gpu_buffer_bytes')].count('\n')+1
    last_line=text[:text.index('ncclCommWindowRegister(comm, raw_window_ptr')].count('\n')+1
    selected='\n'.join(text.splitlines()[first_line-1:last_line])
    claims.append(dict(source='context.cpp',name='main_shared_window',line=first_line,end_line=last_line,
        sha256=sha(selected.encode()),required=['num_workspace_bytes + num_gpu_buffer_bytes','ncclCommWindowRegister']))
    save(out,claims);print(json.dumps(dict(source_excerpts=len(claims))))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('prepare','build','analyze','evidence'))
    p.add_argument('--inputs',type=Path);p.add_argument('--records',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.action=='prepare':prepare(a.output)
    elif a.action=='build':build(a.inputs,a.output)
    elif a.action=='analyze':analyze(a.records,a.output)
    else:evidence(a.inputs,a.output)


if __name__=='__main__':main()
