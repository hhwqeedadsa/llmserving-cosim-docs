// Exercise actual pinned SwitchNode branches with an explicit saturated prestate.
// This is not a production incast or a measurement of natural loss frequency.
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/switch-node.h"
#include "ns3/flow-id-tag.h"
#include "ns3/custom-header.h"
#include <array>
#include <iostream>
#include <stdexcept>
#include <vector>

using namespace ns3;
static void Check(bool ok,const char* why) {if(!ok)throw std::runtime_error(why);}
struct Capture {
  std::vector<std::array<int64_t,5>> drops;
  unsigned legacy=0;
  void Drop(Ptr<const Packet> p,uint32_t in,int32_t out,uint32_t q,uint32_t reason) {
    drops.push_back({static_cast<int64_t>(p->GetUid()),in,out,q,reason});
  }
  void Legacy(Ptr<const Packet>,uint32_t) {++legacy;}
};
int main() {
  try {
    Time::SetResolution(Time::NS);
    auto sw=CreateObject<SwitchNode>();auto source=CreateObject<Node>();auto sink=CreateObject<Node>();
    QbbHelper helper;auto incoming=helper.Install(sw,source);auto outgoing=helper.Install(sw,sink);
    const auto in=incoming.Get(0)->GetIfIndex(),out=outgoing.Get(0)->GetIfIndex();
    auto input=DynamicCast<QbbNetDevice>(incoming.Get(0));
    auto output=DynamicCast<QbbNetDevice>(outgoing.Get(0));
    Capture capture;
    Check(sw->TraceConnectWithoutContext("SwitchDrop",MakeCallback(&Capture::Drop,&capture)),"missing drop trace");
    Check(input->TraceConnectWithoutContext("QbbDrop",MakeCallback(&Capture::Legacy,&capture)),"missing legacy trace");
    CustomHeader h(CustomHeader::L2_Header|CustomHeader::L3_Header|CustomHeader::L4_Header);
    h.l3Prot=0x11;h.sip=1;h.dip=2;h.udp.sport=10000;h.udp.dport=10000;h.udp.pg=3;h.udp.seq=0;
    auto packet=[&](){auto p=Create<Packet>(1000);p->AddPacketTag(FlowIdTag(in));return p;};
    Ipv4Address dst(h.dip);sw->AddTableEntry(dst,out);
    auto mmu=sw->m_mmu;mmu->ConfigBufferSize(1048576);
    mmu->total_hdrm=1024;mmu->total_rsrv=8192;mmu->shared_used_bytes=524288;
    mmu->headroom[in]=256;mmu->hdrm_bytes[in][3]=256;mmu->pfc_a_shift[in]=3;
    mmu->ingress_bytes[in][3]=mmu->reserve+524288;mmu->egress_bytes[out][3]=524544+mmu->reserve;
    const auto shared=mmu->shared_used_bytes,ingress=mmu->ingress_bytes[in][3],egress=mmu->egress_bytes[out][3];
    const auto headroom=mmu->hdrm_bytes[in][3];
    auto p=packet();sw->SwitchReceiveFromDevice(input,p,h);
    Check(capture.drops.size()==1 && capture.drops.back()==std::array<int64_t,5>{int64_t(p->GetUid()),in,out,3,SwitchNode::INGRESS_ADMISSION},
          "ingress refusal not observed exactly once");
    Check(capture.legacy==0 && mmu->shared_used_bytes==shared && mmu->ingress_bytes[in][3]==ingress &&
      mmu->egress_bytes[out][3]==egress && mmu->hdrm_bytes[in][3]==headroom && output->GetQueue()->GetNBytesTotal()==0,
      "refused packet changed MMU or queue");
    Check(mmu->CheckEgressAdmission(out,3,1000000),"fixed egress model changed; do not invent egress refusal");
    sw->ClearTable();p=packet();sw->SwitchReceiveFromDevice(input,p,h);
    Check(capture.drops.size()==2 && capture.drops.back()[2]==-1 && capture.drops.back()[4]==SwitchNode::NO_ROUTE &&
      capture.legacy==1,"no-route/legacy domains differ");
    sw->AddTableEntry(dst,out);output->TakeDown();p=packet();sw->SwitchReceiveFromDevice(input,p,h);
    Check(capture.drops.size()==3 && capture.drops.back()[2]==out && capture.drops.back()[4]==SwitchNode::LINK_DOWN &&
      capture.legacy==2,"link-down reason or legacy behavior differs");
    Check(sw->TraceDisconnectWithoutContext("SwitchDrop",MakeCallback(&Capture::Drop,&capture)),"cannot detach passive observer");
    sw->SwitchReceiveFromDevice(input,packet(),h);
    Check(capture.drops.size()==3 && capture.legacy==3 && mmu->shared_used_bytes==shared,
          "disabled observer changed legacy behavior");
    Simulator::Destroy();
    std::cout<<"passed real SwitchNode ingress/no-route/link-down branches, unchanged rejected MMU state and legacy notifications; explicit saturated prestate, no end-to-end loss claim\n";
    return 0;
  } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';Simulator::Destroy();return 1;}
}
