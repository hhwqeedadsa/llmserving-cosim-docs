#pragma once
#include "ns3/switch-node.h"
#include "repair_wire.hh"

namespace moe_rdma {
// Fixed upstream GetOutDev leaves the third hash word uninitialized for CNP.
// Use its ACK hash branch on a LOCAL metadata copy only. The actual 0xff CNP
// bytes, queue-0 service, MMU, and output device path stay native. ACK priority
// must remain enabled: both ACK and CNP take queue 0 in this Fabric.
class CnpSwitch final: public ns3::SwitchNode {
 public:
  using RepairObserver=std::function<void(unsigned,unsigned,const RepairHeader&,ns3::Ptr<const ns3::Packet>,unsigned)>;
  void EnableRepair(std::map<uint32_t,RepairPath> plans,RepairObserver observer) {
    repair_enabled_=true;plans_=std::move(plans);observer_=std::move(observer);
  }
  void InstallOutputAliases() {
    if(!repair_enabled_)return;
    for(unsigned port=1;port<GetNDevices();++port) {
      ns3::Ipv4Address address(Alias(port));AddTableEntry(address,port);
    }
  }
  void ClearActiveRepairs(){active_.clear();}
  bool ActivateRepair(uint32_t ip) {
    if(!plans_.count(ip))return false;
    active_.insert(ip);return true;
  }
  static ns3::TypeId GetTypeId() {
    static ns3::TypeId tid=ns3::TypeId("moe_rdma::CnpSwitch").SetParent<ns3::SwitchNode>().AddConstructor<CnpSwitch>();
    return tid;
  }
  bool SwitchReceiveFromDevice(ns3::Ptr<ns3::NetDevice> device,ns3::Ptr<ns3::Packet> packet,ns3::CustomHeader& h) override {
    if(repair_enabled_ && h.l3Prot==kRepairProtocol) {
      RepairHeader route;ns3::Ipv4Header outer;const auto overhead=RemoveRepair(packet,route,outer);
      MergeRepairEcn(packet,outer);auto inner=RawHeader(packet);
      if(route.next==route.path.steps.size()) {
        RepairRequire(GetId()==route.path.merge,"repair arrived at wrong merge");
        observer_(3,0,route,packet,overhead);
        return NativeReceive(device,packet,inner);
      }
      return ForwardRepair(device,packet,inner,route,2);
    }
    if(repair_enabled_ && active_.count(h.dip) && (h.l3Prot==0x11 || h.l3Prot==0xfc || h.l3Prot==0xfd || h.l3Prot==0xff)) {
      RepairHeader route;route.path=plans_.at(h.dip);
      return ForwardRepair(device,packet,h,route,1);
    }
    return NativeReceive(device,packet,h);
  }
 private:
  static uint32_t Alias(unsigned port){return 0x0e000000u+port;}
  bool ForwardRepair(ns3::Ptr<ns3::NetDevice> device,ns3::Ptr<ns3::Packet> packet,
                     ns3::CustomHeader h,RepairHeader route,unsigned action) {
    RepairRequire(route.next<route.path.steps.size(),"repair cursor exhausted");
    const auto step=route.path.steps.at(route.next);
    RepairRequire(step.node==GetId() && step.port<GetNDevices(),"repair step not local physical port");
    ++route.next;const auto overhead=AddRepair(packet,route,h);
    observer_(action,step.port,route,packet,overhead);
    // Only the table lookup is projected to a singleton physical output.
    // Actual bytes carry the original endpoints and explicit path; the native
    // priority, MMU, drop, ECN and Qbb service consume the encapsulated size.
    h.dip=Alias(step.port);return NativeReceive(device,packet,h);
  }
  bool NativeReceive(ns3::Ptr<ns3::NetDevice> device,ns3::Ptr<ns3::Packet> packet,ns3::CustomHeader& h) {
    if(h.l3Prot!=0xff)return ns3::SwitchNode::SwitchReceiveFromDevice(device,packet,h);
    auto projected=h;
    const auto fid=h.cnp.fid;
    projected.l3Prot=0xfc;projected.ack.sport=fid;projected.ack.dport=fid;
    return ns3::SwitchNode::SwitchReceiveFromDevice(device,packet,projected);
  }
  bool repair_enabled_=false;
  std::map<uint32_t,RepairPath> plans_;
  std::set<uint32_t> active_;
  RepairObserver observer_;
};
}
