// Recording substitutes, not NCCL/provider/GPU execution. Array constants only
// bound synthetic layouts; they are not asserted NCCL production constants.
#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>
enum ncclResult_t {ncclSuccess=0,ncclSystemError=1,ncclInvalidArgument=2};
using ncclGinWindow_t=void*;
constexpr int NCCL_GIN_MAX_CONNECTIONS=4,NCCL_GIN_MAX_ACTIVE_BACKENDS=2;
constexpr int NCCL_WIN_STRICT_ORDERING=1,NCCL_NET_MR_FLAG_FORCE_SO=8,NCCL_PTR_DMABUF=16;
#define NCCLCHECK(call) do {auto result=(call);if(result!=ncclSuccess)return result;} while(0)
#define WARN(...) ((void)0)
#define INFO(...) ((void)cComm)
struct ncclGinIbCollComm {int rank,slot;};
struct ibv_mr {unsigned lkey,rkey;int slot;};
struct Metadata {int slot,kind;ncclResult_t deallocate();~Metadata();};
struct gdaki_mem_handle {ibv_mr* mr;Metadata* gdaki_mhandle_hd_mhandle;Metadata* rkeys_hd_mhandle;};
struct Plugin {
  ncclResult_t (*regMrSym)(void*,void*,size_t,int,uint64_t,void**,void**);
  ncclResult_t (*deregMrSym)(void*,void*);
};
struct Property {int ptrSupport;};
struct ncclGinBackendState {int ginCommCount;Plugin* ncclGin;void* ginComms[4];Property ginProps[4];};
struct ncclGinState {int numActiveBackends;ncclGinBackendState backends[2];};
struct Shared {ncclGinState ginState;};
struct ncclComm {int rank;Shared* sharedRes;};
static std::string mode;
static int failure_slot,device_count;
static ncclGinIbCollComm coll[8];static ibv_mr mrs[8];
static gdaki_mem_handle* handles[8];static Metadata* metadata[8][2];
static bool created[8],retired[8],freed[8],deleted[8][2];
static std::vector<int> reg_calls,dereg_calls,mr_calls,meta_calls;
static char backing[64];
static ncclResult_t wrap_ibv_dereg_mr(ibv_mr* mr) {
  mr_calls.push_back(mr->slot);
  if(mode=="mr_error" && failure_slot==mr->slot)return ncclSystemError;
  retired[mr->slot]=true;return ncclSuccess;
}
ncclResult_t Metadata::deallocate() {
  meta_calls.push_back(slot*2+kind);
  return (failure_slot==slot && ((mode=="metadata_handle_error" && kind==0) ||
      (mode=="metadata_keys_error" && kind==1)))?ncclSystemError:ncclSuccess;
}
Metadata::~Metadata(){assert(!deleted[slot][kind]);deleted[slot][kind]=true;}
static void RecordFree(void* p) {
  int slot=-1;for(int i=0;i<8;++i)if(handles[i]==p)slot=i;
  assert(slot>=0 && !freed[slot]);freed[slot]=true;std::free(p);
}
static ncclResult_t MockReg(void* ptr,void* address,size_t size,int type,uint64_t flags,void** host,void** device) {
  const auto c=static_cast<ncclGinIbCollComm*>(ptr);const int slot=c->slot;
  reg_calls.push_back(slot);
  assert(address==backing && size==sizeof(backing) && type==17 && flags==NCCL_NET_MR_FLAG_FORCE_SO);
  if(slot==failure_slot && mode=="register_error")return ncclSystemError;
  if(slot==failure_slot && mode=="null_handle")return ncclSuccess;
  auto h=static_cast<gdaki_mem_handle*>(std::calloc(1,sizeof(gdaki_mem_handle)));assert(h);
  handles[slot]=h;created[slot]=true;mrs[slot]={unsigned(100+slot),unsigned(200+slot),slot};h->mr=&mrs[slot];
  h->gdaki_mhandle_hd_mhandle=metadata[slot][0]=new Metadata{slot,0};
  h->rkeys_hd_mhandle=metadata[slot][1]=new Metadata{slot,1};
  *host=h;*device=ptr;return ncclSuccess;
}
ncclResult_t ncclGinGdakiDeregMrSym(void*,void*);
static ncclResult_t MockDereg(void* ptr,void* handle) {
  dereg_calls.push_back(static_cast<ncclGinIbCollComm*>(ptr)->slot);
  return ncclGinGdakiDeregMrSym(ptr,handle);
}
#define free(p) RecordFree(p)
