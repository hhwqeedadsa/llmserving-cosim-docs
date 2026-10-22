#pragma once
#include "ns3/switch-node.h"

namespace moe_rdma {
// Fixed upstream GetOutDev leaves the third hash word uninitialized for CNP.
// Use its ACK hash branch on a LOCAL metadata copy only. The actual 0xff CNP
// bytes, queue-0 service, MMU, and output device path stay native. ACK priority
// must remain enabled: both ACK and CNP take queue 0 in this Fabric.
class CnpSwitch final: public ns3::SwitchNode {
 public:
  static ns3::TypeId GetTypeId() {
    static ns3::TypeId tid=ns3::TypeId("moe_rdma::CnpSwitch").SetParent<ns3::SwitchNode>().AddConstructor<CnpSwitch>();
    return tid;
  }
  bool SwitchReceiveFromDevice(ns3::Ptr<ns3::NetDevice> device,ns3::Ptr<ns3::Packet> packet,ns3::CustomHeader& h) override {
    if(h.l3Prot!=0xff)return ns3::SwitchNode::SwitchReceiveFromDevice(device,packet,h);
    auto projected=h;
    const auto fid=h.cnp.fid;
    projected.l3Prot=0xfc;projected.ack.sport=fid;projected.ack.dport=fid;
    return ns3::SwitchNode::SwitchReceiveFromDevice(device,packet,projected);
  }
};
}
