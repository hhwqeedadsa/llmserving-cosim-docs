#pragma once
#include "repair_plan.hh"
#include "ns3/custom-header.h"
#include "ns3/ipv4-header.h"
#include "ns3/ppp-header.h"
#include "ns3/packet.h"

namespace moe_rdma {
inline void RepairRequire(bool ok,const char* why) {if(!ok)throw std::runtime_error(why);}
constexpr uint8_t kRepairProtocol=250; // Project-local framing, not an IANA allocation.
class RepairHeader final:public ns3::Header {
 public:
  RepairPath path;uint8_t next=0;
  static ns3::TypeId GetTypeId() {
    static ns3::TypeId t=ns3::TypeId("moe_rdma::RepairHeader").SetParent<ns3::Header>().AddConstructor<RepairHeader>();return t;
  }
  ns3::TypeId GetInstanceTypeId()const override{return GetTypeId();}
  uint32_t GetSerializedSize()const override{return 8+4*path.steps.size();}
  void Print(std::ostream& out)const override {out<<"MRP1 next="<<unsigned(next)<<" merge="<<path.merge;}
  void Validate()const {
    RepairRequire(!path.steps.empty() && path.steps.size()<=16 && next<=path.steps.size(),"invalid repair count/cursor");
    std::set<unsigned> seen;
    for(auto s:path.steps)RepairRequire(s.port>0 && s.port<=1024 && seen.insert(s.node).second,"invalid/cyclic repair step");
    RepairRequire(!seen.count(path.merge),"repair merge repeats prefix");
  }
  void Serialize(ns3::Buffer::Iterator i)const override {
    Validate();i.WriteHtonU32(0x4d525031);i.WriteU8(path.steps.size());i.WriteU8(next);i.WriteHtonU16(path.merge);
    for(auto s:path.steps){i.WriteHtonU16(s.node);i.WriteHtonU16(s.port);}
  }
  uint32_t Deserialize(ns3::Buffer::Iterator i)override {
    RepairRequire(i.GetRemainingSize()>=8,"short repair header");
    RepairRequire(i.ReadNtohU32()==0x4d525031,"invalid repair wire version");
    const unsigned count=i.ReadU8();next=i.ReadU8();path.merge=i.ReadNtohU16();
    RepairRequire(count>0 && count<=16 && i.GetRemainingSize()>=4*count,"invalid/truncated repair steps");
    path.steps.clear();for(unsigned j=0;j<count;++j)path.steps.push_back({i.ReadNtohU16(),i.ReadNtohU16()});
    Validate();return GetSerializedSize();
  }
};
inline ns3::CustomHeader RawHeader(ns3::Ptr<const ns3::Packet> p) {
  ns3::CustomHeader h(ns3::CustomHeader::L2_Header|ns3::CustomHeader::L3_Header|ns3::CustomHeader::L4_Header);
  h.getInt=1;p->PeekHeader(h);return h;
}
inline unsigned RemoveRepair(ns3::Ptr<ns3::Packet> p,RepairHeader& route,ns3::Ipv4Header& outer) {
  ns3::PppHeader ppp;const auto before=p->GetSize();p->RemoveHeader(ppp);p->RemoveHeader(outer);
  RepairRequire(ppp.GetProtocol()==0x21 && outer.GetProtocol()==kRepairProtocol,"wrong repair encapsulation");
  p->RemoveHeader(route);
  const auto inner=RawHeader(p);
  RepairRequire(inner.l3Prot!=kRepairProtocol && inner.l3Prot!=0xfe,"nested repair or encapsulated PFC");
  RepairRequire(inner.sip==outer.GetSource().Get() && inner.dip==outer.GetDestination().Get(),"repair endpoint mismatch");
  return before-p->GetSize();
}
inline unsigned AddRepair(ns3::Ptr<ns3::Packet> p,const RepairHeader& route,const ns3::CustomHeader& inner) {
  const auto before=p->GetSize();p->AddHeader(route);
  RepairRequire(p->GetSize()<=65515,"repair frame exceeds IPv4 size");
  ns3::Ipv4Header outer;outer.SetProtocol(kRepairProtocol);outer.SetTtl(64);
  outer.SetSource(ns3::Ipv4Address(inner.sip));outer.SetDestination(ns3::Ipv4Address(inner.dip));
  outer.SetTos(inner.m_tos);outer.SetPayloadSize(p->GetSize());p->AddHeader(outer);
  ns3::PppHeader ppp;ppp.SetProtocol(0x21);p->AddHeader(ppp);return p->GetSize()-before;
}
inline ns3::CustomHeader LogicalHeader(ns3::Ptr<const ns3::Packet> p,unsigned* overhead=nullptr) {
  auto h=RawHeader(p);if(overhead)*overhead=0;
  if(h.l3Prot!=kRepairProtocol)return h;
  auto copy=p->Copy();RepairHeader r;ns3::Ipv4Header outer;
  const auto bytes=RemoveRepair(copy,r,outer);h=RawHeader(copy);
  if(outer.GetEcn()==ns3::Ipv4Header::ECN_CE)h.m_tos=(h.m_tos&~3u)|3u;
  if(overhead)*overhead=bytes;
  return h;
}
inline void MergeRepairEcn(ns3::Ptr<ns3::Packet> p,const ns3::Ipv4Header& outer) {
  if(outer.GetEcn()!=ns3::Ipv4Header::ECN_CE)return;
  ns3::PppHeader ppp;ns3::Ipv4Header inner;p->RemoveHeader(ppp);p->RemoveHeader(inner);
  inner.SetEcn(ns3::Ipv4Header::ECN_CE);p->AddHeader(inner);p->AddHeader(ppp);
}
}
