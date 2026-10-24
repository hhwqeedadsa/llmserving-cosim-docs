"""Extract exact source functions into explicit CPU recording substitutes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess

HERE=Path(__file__).resolve().parent
TARGETS={
    'SET_FLAGS':('src/init.cc','static ncclResult_t setCommAbortFlags(ncclComm_t comm, int value) {',0),
    'REVOKE_ASYNC':('src/init.cc','static ncclResult_t commRevokeAsync(struct ncclAsyncJob* job_) {',0),
    'SHRINK':('src/init.cc','ncclResult_t ncclCommShrink(ncclComm_t comm, int* excludeRanksList',0),
    'WAIT_CORE':('src/include/nccl_device/gin/gdaki/gin_gdaki.h',
                 'template <bool HasTimeout, enum doca_gpu_dev_verbs_resource_sharing_mode doca_sharing_mode>\nNCCL_DEVICE_INLINE static ncclResult_t waitImplCore',0),
    'HOST_TEST_ABORT':('src/include/nccl_device/utility.h','static NCCL_DEVICE_INLINE bool testAbort(',1),
}


def save(p,v):p.write_text(json.dumps(v,sort_keys=True,indent=2)+'\n')
def sha(raw):return hashlib.sha256(raw).hexdigest()


def function(text,marker,occurrence):
    starts=[m.start() for m in re.finditer(re.escape(marker),text)]
    start=starts[occurrence];begin=text.index('{',start);depth=0
    tokens=r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27|[{}]'
    for m in re.finditer(tokens,text[begin:],re.S):
        token=m.group()
        if token=='{':depth+=1
        elif token=='}':
            depth-=1
            if depth==0:return start,begin+m.end()
    raise ValueError('unterminated source function')


def selected(inputs):
    result={}
    for name,(path,marker,index) in TARGETS.items():
        text=(inputs/'sources/nccl'/path).read_text();start,end=function(text,marker,index)
        result[name]=('nccl',path,text,start,end)
    text=(inputs/'sources/nccl/src/dev_runtime.cc').read_text()
    marker='if (isInternal) outDevComm->abortFlag = comm->abortFlagDev;'
    start=text.index(marker);result['EXTERNAL_ABORT_SELECTION']=('nccl','src/dev_runtime.cc',text,start,start+len(marker))
    text=(inputs/'sources/nccl/src/init.cc').read_text();start=text.index('comm->shareResources =\n')
    result['RESOURCE_SHARING']=('nccl','src/init.cc',text,start,text.index(';',start)+1)
    return result


def evidence(inputs):
    rows=[]
    for key,(project,path,text,start,end) in selected(inputs).items():
        raw=text[start:end].encode()
        rows.append(dict(id=key,project=project,path=path,start_line=text[:start].count('\n')+1,
                         end_line=text[:end].count('\n')+1,sha256=sha(raw),text=raw.decode(),kind='executed_original'))
    spans=[
      ('revoke_contract','nccl','docs/userguide/source/api/comms.rst',171,182),
      ('shrink_contract','nccl','docs/userguide/source/api/comms.rst',109,125),
      ('deregister_precondition','nccl','docs/userguide/source/api/comms.rst',289,294),
      ('device_zero_and_abort','nccl','src/dev_runtime.cc',1497,1510),
      ('external_create','nccl','src/dev_runtime.cc',1781,1788),
      ('gin_flush','nccl','src/include/nccl_device/impl/gin__funcs.h',997,1003),
      ('gin_signal_wait','nccl','src/include/nccl_device/impl/gin__funcs.h',1368,1395),
      ('deepep_create','deepep','csrc/kernels/comm/context.cpp',122,164),
      ('deepep_abort_wrapper','deepep','csrc/kernels/comm/context.cpp',45,50),
      ('deepep_flush_wait','deepep','deep_ep/include/deep_ep/comm/handle.cuh',148,199),
      ('deepep_timeout_trap','deepep','deep_ep/include/deep_ep/comm/barrier.cuh',30,47),
      ('remaining_window_cleanup','nccl','src/dev_runtime.cc',235,247),
      ('gin_error_query','nccl','src/init.cc',3943,3960),
    ]
    for key,project,path,first,last in spans:
        lines=(inputs/'sources'/project/path).read_text().splitlines(keepends=True);text=''.join(lines[first-1:last])
        rows.append(dict(id=key,project=project,path=path,start_line=first,end_line=last,
                         sha256=sha(text.encode()),text=text,kind='static_source'))
    return rows


def build(inputs,output):
    output.mkdir();template=(HERE/'harness.hpp').read_text()
    for key,(_,_,text,start,end) in selected(inputs).items():
        placeholder='// ORIGINAL_'+key
        if template.count(placeholder)!=1:raise ValueError('placeholder coverage')
        template=template.replace(placeholder,text[start:end])
    source=template+'\n'+(HERE/'harness_main.cc').read_text();(output/'source-harness.cc').write_text(source)
    command=['g++','-std=c++17','-O1','-Wall','-Wextra','-Werror',str(output/'source-harness.cc'),'-o',str(output/'source-harness')]
    r=subprocess.run(command,capture_output=True,timeout=45)
    (output/'compile.stdout').write_bytes(r.stdout);(output/'compile.stderr').write_bytes(r.stderr)
    save(output/'compile.json',dict(command=command,cwd=str(Path.cwd()),exit_code=r.returncode))
    if r.returncode:raise RuntimeError('exact CPU source compile failed; evidence preserved')


def analyze(records):
    rows=[json.loads(line) for line in records.read_text().splitlines()]
    return dict(schema_version='communicator-recovery-summary/1',conditions=len(rows),kinds=dict(Counter(r['kind'] for r in rows)),
      source_scope='five exact functions and two exact statements under explicit CPU recording substitutes',
      worker_proxy_stop_errors_ignored=sum(r['kind']=='revoke_async' and r['fail']=='proxy_stop' and r['return']==0 for r in rows),
      shrink_abort_never_shares=all(not r['shared'] for r in rows if r['kind']=='sharing' and r['shrink'] and r['abort']),
      revoked_never_shares=all(not r['shared'] for r in rows if r['kind']=='sharing' and r['revoked']),
      external_abort_pointer_absent=all(not r['abort_pointer_present'] for r in rows if r['kind']=='gin_wait' and not r['internal']),
      wait_recording_stops=dict(Counter(r['exit'] for r in rows if r['kind']=='gin_wait' and r['exit']!='returned')),
      deregistration_requires_no_access=True,revoke_success_proves_mr_retirement=False,
      arbitrary_user_kernel_quiescence_proven=False,hardware_or_gpu_executed=False,
      supported_failure_recovery_for_deepep_proven=False,performance_or_innovation_proven=False)


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    for name in ('build','evidence'):
        s=sub.add_parser(name);s.add_argument('--inputs',type=Path,required=True);s.add_argument('--output',type=Path,required=True)
    s=sub.add_parser('analyze');s.add_argument('--records',type=Path,required=True);s.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.action=='build':build(a.inputs,a.output)
    elif a.action=='evidence':save(a.output,evidence(a.inputs))
    else:save(a.output,analyze(a.records))


if __name__=='__main__':main()
