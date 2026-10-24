// CPU recording substitutes. No CUDA, verbs, DMA, actual frees or NCCL library.
#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <mutex>
#include <string>
#include <vector>
enum ncclResult_t {ncclSuccess=0,ncclSystemError=1,ncclInternalError=2};
using cudaStream_t=int;using CUdeviceptr=uintptr_t;
enum cudaStreamCaptureMode {cudaStreamCaptureModeRelaxed=0};
constexpr int cudaStreamNonBlocking=1,NCCL_GIN_MAX_CONNECTIONS=4,NCCL_GIN_MAX_ACTIVE_BACKENDS=2,NCCL_WIN_CFT_COUNTED=1;
constexpr int NCCL_LE_ID_INVALID=-1,ncclDevrRegisterLsa=2;
using ncclCftLeId=int;
#define NCCLCHECK(call) do {auto r_=(call);if(r_!=ncclSuccess)return r_;} while(0)
#define CUDACHECK(call) NCCLCHECK(call)
#define NCCLCHECKGOTO(call,ret,label) do {auto r_=(call);if(r_!=ncclSuccess){ret=r_;goto label;}} while(0)
#define CUDACHECKGOTO(call,ret,label) NCCLCHECKGOTO(call,ret,label)
#define NCCLCHECKIGNORE(call,ret) do {auto r_=(call);if(r_!=ncclSuccess)ret=r_;} while(0)
#define CUCHECKIGNORE(call) ((void)(call))
#define WARN(...) ((void)0)
struct Plugin {ncclResult_t (*deregMrSym)(void*,void*);};
struct ncclGinBackendState {int ginCommCount=0;Plugin* ncclGin=nullptr;void* ginComms[4]{};};
struct ncclGinState {int numActiveBackends=0;ncclGinBackendState backends[2];};
struct Shared {ncclGinState ginState;};
struct Segment {void* ginHostWins[8]{};};
struct ncclDevrMemory {
  int refCount=0;Segment* ginSegmentInfos=nullptr;int numGinSegments=0,winFlags=0;
  size_t bigOffset=0,lsaMaxSize=0;int numSegments=0;int* memHandles=nullptr;
  ncclDevrMemory* next=nullptr;int* lsaNumSegments=nullptr;size_t* segmentSizes=nullptr;
};
struct ncclDevrWindow;
struct ncclWindow_vidmem {void* winHost=nullptr;void* ginMultiSegmentWins=nullptr;};
struct ncclDevrWindow {ncclDevrMemory* memory=nullptr;void* localRegHandle=nullptr;void* userPtr=nullptr;};
struct ncclDevrWindowSorted {uintptr_t userAddr=0;ncclDevrWindow* win=nullptr;};
struct ncclDevrTeam {ncclDevrTeam* next=nullptr;int mcLeId[2]{};};
struct ncclDevCommWindowTable {
  struct Entry {void* base;size_t size;ncclWindow_vidmem* window;} entries[32]{};
  ncclDevCommWindowTable* next=nullptr;
};
struct ncclDevrState {
  bool ginEnabled=true,rmaProxyEnabled=false;ncclDevrMemory* memHead=nullptr;
  struct Le {int baseId=NCCL_LE_ID_INVALID;} le[2];int cftSelf=0;
  ncclDevrTeam* teamHead=nullptr;int lsaSize=1,lsaSelf=0;int bigSpace=0,shadows=0;
  ncclDevCommWindowTable* windowTable=nullptr;ncclDevrWindowSorted* winSorted=nullptr;int winSortedCount=0;
};
struct ncclComm {Shared* sharedRes;ncclDevrState devrState;int cudaDev=0;};
static std::mutex ncclWindowMapMutex;static int ncclWindowMap=0;
struct Handle {int segment,slot;};
static std::string mode;static int fail_segment,fail_slot;
static std::vector<int> calls,retired,failed_results;
static std::vector<std::string> events;
static void* memory_identity;static void* window_identity;
static bool memory_freed,window_freed;
static int shadow_lookups;
static ncclResult_t mock_dereg(void*,void* p) {
  auto* h=static_cast<Handle*>(p);const int id=h->segment*8+h->slot;
  calls.push_back(id);events.push_back("mr_"+std::to_string(id));
  if(h->segment==fail_segment && h->slot==fail_slot && mode=="provider_error") {
    failed_results.push_back(id);return ncclSystemError;
  }
  retired.push_back(id);
  if(h->segment==fail_segment && h->slot==fail_slot && mode=="metadata_error") {
    failed_results.push_back(id);return ncclSystemError;
  }
  return ncclSuccess;
}
static ncclResult_t CommCheck(ncclComm*,const char*,const char*) {events.push_back("comm_check");return ncclSuccess;}
static ncclResult_t cudaThreadExchangeStreamCaptureMode(cudaStreamCaptureMode*) {events.push_back("capture_exchange");return ncclSuccess;}
static ncclResult_t cudaGetDevice(int* p) {*p=0;events.push_back("get_device");return ncclSuccess;}
static ncclResult_t cudaSetDevice(int) {events.push_back("set_device");return ncclSuccess;}
static ncclResult_t cudaStreamCreateWithFlags(cudaStream_t* p,int) {*p=1;events.push_back("stream_create");return ncclSuccess;}
static ncclResult_t cudaStreamSynchronize(cudaStream_t) {events.push_back("stream_sync");return mode=="stream_sync_error"?ncclSystemError:ncclSuccess;}
static ncclResult_t cudaStreamDestroy(cudaStream_t) {events.push_back("stream_destroy");return ncclSuccess;}
template<class T>static ncclResult_t ncclShadowPoolToHost(int*,T* p,T** out) {
  ++shadow_lookups;events.push_back("shadow_"+std::to_string(shadow_lookups));
  if((mode=="initial_shadow_error" && shadow_lookups==1) ||
     (mode=="table_shadow_error" && shadow_lookups==2))return ncclSystemError;
  *out=p;return ncclSuccess;
}
static ncclResult_t cudaMemsetAsync(void*,int,size_t,cudaStream_t) {
  events.push_back("table_memset");return mode=="table_memset_error"?ncclSystemError:ncclSuccess;
}
static ncclResult_t ncclShadowPoolFree(int*,void*,cudaStream_t) {
  events.push_back("window_shadow_free");return mode=="window_shadow_error"?ncclSystemError:ncclSuccess;
}
static ncclResult_t ncclCommDeregister(ncclComm*,void*) {
  events.push_back("local_deregister");return mode=="local_deregister_error"?ncclSystemError:ncclSuccess;
}
static bool ncclDevrWinRegEnabled(int flags,int mask) {return (flags&mask)!=0;}
static void symMemoryDeregisterRma(ncclComm*,ncclDevrMemory*) {events.push_back("rma");}
static void symUnbindTeamLe(ncclComm*,ncclDevrMemory*,int) {events.push_back("unbind_le");}
static void symUnbindTeamMemory(ncclComm*,ncclDevrTeam*,ncclDevrMemory*) {events.push_back("unbind_team");}
static void symMemoryUnmapLsaRank(ncclDevrState*,ncclDevrMemory*,int) {events.push_back("unmap");}
static void ncclSpaceFree(int*,size_t,size_t) {events.push_back("space_free");}
static ncclResult_t cuMemRelease(int) {events.push_back("release_allocation_handle");return ncclSuccess;}
template<class M,class T>static int listFindSortedLub(M member,T* items,int n,uintptr_t value) {
  int i=0;while(i<n && items[i].*member<=value)++i;return i;
}
template<class T>static void listRemove(T* items,int* n,int i) {
  for(int j=i+1;j<*n;++j)items[j-1]=items[j];
  --*n;events.push_back("sorted_remove");
}
static ncclResult_t ncclIntruAddressMapRemove(int*,ncclWindow_vidmem*) {events.push_back("map_remove");return ncclSuccess;}
static void RecordFree(void* p) {
  // Objects are stack-owned in this harness. No production free is performed.
  if(p==memory_identity){assert(!memory_freed);memory_freed=true;events.push_back("memory_free");}
  if(p==window_identity){assert(!window_freed);window_freed=true;events.push_back("window_free");}
}
#define free(p) RecordFree(p)
