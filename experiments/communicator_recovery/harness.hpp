// Recording substitutes only: no NCCL, CUDA, GPU, provider or real threading.
#include <cstdint>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>
#include <atomic>
using ncclResult_t=int;
constexpr int ncclSuccess=0,ncclInternalError=3,ncclTimeout=8,NCCL_SHRINK_ABORT=1;
static std::vector<std::string> trace;
static std::string fail_at;
static int Call(const std::string& name){trace.push_back(name);return fail_at==name?ncclInternalError:ncclSuccess;}
static std::map<uint32_t*,std::string> pointer_names;
static void Store(uint32_t* p,uint32_t value){trace.push_back("store_"+pointer_names.at(p)+":"+std::to_string(value));*p=value;}
#define COMPILER_ATOMIC_STORE(p,v,order) Store(p,v)
#define INFO(...) ((void)0)
#define NCCL_DESTROY 0
#define NCCL_PROXY 0
#define NCCLCHECKGOTO(cmd,res,label) do {const auto recording_result=(cmd);if(recording_result!=ncclSuccess){res=recording_result;goto label;}}while(0)
#define CUDACHECKGOTO NCCLCHECKGOTO
#define NCCLCHECK(cmd) do {const auto recording_result=(cmd);if(recording_result!=ncclSuccess)return recording_result;}while(0)
#define NVTX3_RANGE(...)
#define NVTX3_RANGE_ADD_PAYLOAD(...)
struct Stream {std::string name;};
struct Shared {Stream hostStream{"host_sync"},deviceStream{"device_sync"};};
struct Thread {
  bool available=false;std::string name;
  bool joinable() const{return available;}
  void join(){trace.push_back(name);available=false;}
};
struct Proxy {Thread thread{false,"join_proxy"},threadUDS{false,"join_uds"};};
struct Config {bool shrinkShare=false,splitShare=false;};
struct ncclComm {
  uint32_t* abortFlag=nullptr;uint32_t* abortFlagDev=nullptr;
  uint32_t* childAbortFlag=nullptr;uint32_t* childAbortFlagDev=nullptr;
  Shared* sharedRes=nullptr;Proxy* proxyState=nullptr;
  int proxyRefCountOld=0,cudaDev=0,rank=0;
  int asyncResult=-1;bool revokedFlag=false,shareResources=false;
  Config config;
};
using ncclComm_t=ncclComm*;
using ncclConfig_t=Config;
struct ncclAsyncJob {};
struct ncclCommRevokeAsyncJob {ncclAsyncJob base;ncclComm_t comm;};
static int PtrCheck(ncclComm_t comm,const char*,const char*){trace.push_back("ptr_check");return comm?0:3;}
static int cudaSetDevice(int){return Call("set_device");}
static int ncclStrongStreamSynchronize(Stream* stream){return Call(stream->name);}
static int ncclCommPollEventCallbacks(ncclComm_t,bool){return Call("poll_events");}
static int ncclCommPollCallbacks(ncclComm_t,bool){return Call("poll_callbacks");}
static int ncclProxyStop(ncclComm_t){return Call("proxy_stop");}
static int ncclCommSetAsyncError(ncclComm_t comm,int result){trace.push_back("async_error:"+std::to_string(result));if(comm)comm->asyncResult=result;return 0;}
static int ncclGroupStartInternal(){return Call("group_start");}
static int ncclGroupEndInternal(){return Call("group_end");}
static int ncclGroupErrCheck(int result){trace.push_back("group_error:"+std::to_string(result));return 0;}
static int ncclCommInitChildComm(ncclComm_t,ncclComm_t* out,bool isShrink,int flags,int,int,
                                 int*,int,ncclConfig_t*,const char*) {
  if(!isShrink || (flags!=0 && flags!=NCCL_SHRINK_ABORT))throw std::logic_error("unexpected child mode");
  const auto r=Call("child_init");if(r==0)*out=reinterpret_cast<ncclComm_t>(uintptr_t(1));return r;
}
struct LocalComm {
  ncclComm c;Shared shared;Proxy proxy;uint32_t host=1,device=1,child_host=1,child_device=1;
  explicit LocalComm(bool child) {
    c.abortFlag=&host;c.abortFlagDev=&device;c.sharedRes=&shared;
    if(child){c.childAbortFlag=&child_host;c.childAbortFlagDev=&child_device;}
    pointer_names={{&host,"host"},{&device,"device"},{&child_host,"child_host"},{&child_device,"child_device"}};
  }
};

// CPU-only CUDA-like names allow the unmodified GDAKI control function to run.
namespace cuda {
enum memory_order {memory_order_relaxed,memory_order_acquire};
enum thread_scope {thread_scope_system};
static unsigned fences=0;
static void atomic_thread_fence(memory_order,thread_scope){++fences;}
}
#define NCCL_DEVICE_INLINE inline
#define NCCL_IF_CONSTEXPR constexpr
#define NVCC_PRAGMA_UNROLL_DISABLED
namespace nccl {namespace utility {
template<class T>T loadConst(T const* p){return *p;}
// ORIGINAL_HOST_TEST_ABORT
}}
struct RecordingStop:std::runtime_error {using std::runtime_error::runtime_error;};
static unsigned polls=0,blocking_calls=0;static int complete_after=-1;static uint64_t cycles=0;
static uint64_t clock64(){return cycles++;}
enum doca_gpu_dev_verbs_resource_sharing_mode {recording_mode};
using doca_gpu_dev_verbs_ticket_t=uint64_t;
struct doca_gpu_dev_verbs_qp {};
struct ncclGinGdakiGPUContext {doca_gpu_dev_verbs_qp* gdqp;};
struct ncclGinCtx {void* handle;unsigned contextId;};
struct ncclGinGdakiRequest {uint64_t sq_rsvd_index;unsigned peer;};
using ncclGinRequest_t=ncclGinGdakiRequest;
template<doca_gpu_dev_verbs_resource_sharing_mode>
static void doca_gpu_dev_verbs_wait(doca_gpu_dev_verbs_qp*,uint64_t) {
  ++blocking_calls;
  if(complete_after!=0)throw RecordingStop("recording_blocking_wait");
}
template<doca_gpu_dev_verbs_resource_sharing_mode>
static int doca_gpu_dev_verbs_poll_one_cq_at(doca_gpu_dev_verbs_qp*,uint64_t) {
  if(++polls>20000)throw RecordingStop("recording_poll_cap");
  return complete_after>=0 && polls>static_cast<unsigned>(complete_after)?0:1;
}
// ORIGINAL_SET_FLAGS
// ORIGINAL_REVOKE_ASYNC
// ORIGINAL_SHRINK
// ORIGINAL_WAIT_CORE
struct RecordingDevComm {uint32_t* abortFlag=nullptr;};
static void SelectAbort(RecordingDevComm* outDevComm,ncclComm_t comm,bool isInternal) {
  // ORIGINAL_EXTERNAL_ABORT_SELECTION
}
static bool Share(ncclComm_t comm,bool isShrink,int flags) {
  // ORIGINAL_RESOURCE_SHARING
  return comm->shareResources;
}
