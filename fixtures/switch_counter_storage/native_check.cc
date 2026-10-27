#include "ns3/core-module.h"
#include "ns3/switch-node.h"
#include <string>
#include <stdexcept>

int main(int argc,char** argv) {
  if(argc!=2)return 2;
  auto sw=ns3::CreateObject<ns3::SwitchNode>();
  if(sw->ByteCounterSlots()!=0 || sw->ByteCounterStorageBytes()!=0)return 3;
  const std::string mode=argv[1];
  if(mode=="zero")sw->InitializeByteCounters(0);
  else if(mode=="too_many")sw->InitializeByteCounters(1026);
  else {
    sw->InitializeByteCounters(mode=="legacy" ? 1025 : 3);
    if(mode=="twice")sw->InitializeByteCounters(3);
    else if(mode=="bad_in")sw->ReadByteCounter(3,0,0);
    else if(mode=="bad_out")sw->ReadByteCounter(0,3,0);
    else if(mode=="bad_priority")sw->ReadByteCounter(0,0,8);
    else if(mode=="legacy" || mode=="port") {
      const uint32_t slots=mode=="legacy" ? 1025 : 3;
      if(sw->ByteCounterSlots()!=slots || sw->ByteCounterStorageBytes()!=uint64_t(slots)*slots*8*4)return 4;
      if(sw->ReadByteCounter(slots-1,slots-1,7)!=0)return 5;
      return 0;
    }
  }
  return 6;
}
