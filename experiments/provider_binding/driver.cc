// CPU substitutes for scalar types/qualifiers only. No CUDA/provider execution.
#include <cstdint>
#include <iostream>
#include <utility>
#define __device__
#define __forceinline__ inline
#define NCCL_DEVICE_INLINE inline
enum ncclGinResourceSharingMode { NCCL_GIN_RESOURCE_SHARING_CTA, NCCL_GIN_RESOURCE_SHARING_GPU };
constexpr int NCCL_GIN_MAX_CONNECTIONS=4;
struct ncclDevComm {
  int ginConnectionCount=2;
  int ginNetDeviceTypes[4]={10,11,12,13};
  void* ginHandles[4]={};
  uint64_t* ginSignalShadows=nullptr;
  int ginSignalCount=0;
};
struct Gin {
  int nConnections=0,connectionId=-1,contextId=-1,_ginBackend=-1;
  void* _ginHandle=nullptr;
  uint64_t* _signalShadows=nullptr;
};
#include "original_functions.inc"
int main() {
  uint64_t signals[1]={};ncclDevComm comm;comm.ginSignalShadows=signals;
  char phase;int sm,channel,notify;unsigned ordinal=0;
  while(std::cin>>phase>>sm>>channel>>notify) {
    std::pair<int,ncclGinResourceSharingMode> assigned;
    if(phase=='d')assigned=get_qp_mode<2,5,2,true>(sm,channel,notify!=0);
    else if(phase=='c')assigned=get_qp_mode<2,5,2,false>(sm,channel,notify!=0);
    else return 2;
    Gin gin;ncclGinInitCommon(&gin,comm,assigned.first);
    std::cout<<ordinal++<<' '<<assigned.first<<' '<<gin.connectionId<<' '<<gin.contextId<<' '
             <<gin._ginBackend<<' '<<int(assigned.second)<<'\n';
  }
  return std::cin.eof()?0:3;
}
