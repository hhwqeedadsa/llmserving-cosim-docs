// CPU-only path/real-byte serialization checks. No Simulator::Run or network.
#include "repair_wire.hh"
#include "ns3/udp-header.h"
#include "ns3/seq-ts-header.h"
#include "ns3/qbb-header.h"
#include "ns3/cn-header.h"
#include <fstream>
#include <iostream>

using namespace moe_rdma;
using namespace ns3;
using Json=nlohmann::json;
static void Check(bool value,const char* why){if(!value)throw std::runtime_error(why);}
template<class F>void Reject(F f) {bool rejected=false;try{f();}catch(const std::exception&){rejected=true;}Check(rejected,"illegal repair accepted");}
static std::vector<uint8_t> Bytes(Ptr<const Packet> p){std::vector<uint8_t>b(p->GetSize());p->CopyData(b.data(),b.size());return b;}
int main(int argc,char** argv) {
 try {
  Check(argc==2,"pass physical topology JSON");Json topology;std::ifstream(argv[1])>>topology;
  const auto plans=BuildRepairPlans(topology,128,132);
  Check(plans.size()==1 && plans.at(132).size()==16,"Clos repair coverage mismatch");
  const auto path=plans.at(132).at(0x0b000001u+32u);
  Check(path.steps.size()==2 && path.steps[0].node==132 && path.steps[0].port==2 &&
    path.steps[1].node==129 && path.steps[1].port==18 && path.merge==133,"wrong Clos prefix ports");
  for(auto prot:{0x11,0xfc,0xfd,0xff}) {
    std::vector<uint8_t> payload(1000,0x5a);auto packet=Create<Packet>(payload.data(),payload.size());
    if(prot==0x11){SeqTsHeader s;s.SetSeq(1000);s.SetPG(3);packet->AddHeader(s);
      UdpHeader udp;udp.SetSourcePort(12000);udp.SetDestinationPort(12000);packet->AddHeader(udp);}
    else if(prot==0xfc || prot==0xfd){qbbHeader ack;ack.SetSeq(2000);ack.SetPG(3);ack.SetSport(12000);ack.SetDport(12000);packet->AddHeader(ack);}
    else {CnHeader cnp(12000,3,3,0,0);packet->AddHeader(cnp);}
    Ipv4Header ip;ip.SetProtocol(prot);ip.SetSource(Ipv4Address(0x0b000821));ip.SetDestination(Ipv4Address(0x0b000021));
    ip.SetTtl(64);ip.SetPayloadSize(packet->GetSize());packet->AddHeader(ip);
    PppHeader ppp;ppp.SetProtocol(0x21);packet->AddHeader(ppp);
    const auto original=Bytes(packet);const auto h=RawHeader(packet);const auto uid=packet->GetUid();
    RepairHeader route;route.path=path;route.next=1;
    const auto overhead=AddRepair(packet,route,h);Check(overhead==38,"wire overhead must include native2-byte PPP");
    unsigned observed=0;auto logical=LogicalHeader(packet,&observed);
    Check(observed==overhead && logical.l3Prot==unsigned(prot) && logical.dip==h.dip && packet->GetUid()==uid,"logical projection/lineage changed");
    Check(RawHeader(packet).l3Prot==kRepairProtocol,"repair not serialized on wire");
    RepairHeader decoded;Ipv4Header outer;Check(RemoveRepair(packet,decoded,outer)==overhead,"removed wrong bytes");
    Check(decoded.next==1 && decoded.path.steps.size()==2 && decoded.path.merge==133 && Bytes(packet)==original,"real bytes not round-tripped");
    AddRepair(packet,route,h);PppHeader op;Ipv4Header oi;packet->RemoveHeader(op);packet->RemoveHeader(oi);
    oi.SetEcn(Ipv4Header::ECN_CE);packet->AddHeader(oi);packet->AddHeader(op);
    Check(LogicalHeader(packet).GetIpv4EcnBits()==3,"observer lost outer CE");
    RemoveRepair(packet,decoded,outer);MergeRepairEcn(packet,outer);Check(RawHeader(packet).GetIpv4EcnBits()==3,"merge lost CE");
  }
  RepairHeader invalid;invalid.path=path;invalid.next=3;Reject([&]{invalid.Validate();});
  invalid.next=0;invalid.path.steps[1].node=132;Reject([&]{invalid.Validate();});
  invalid.path=path;invalid.path.steps[0].port=0;Reject([&]{invalid.Validate();});
  invalid.path=path;invalid.path.merge=132;Reject([&]{invalid.Validate();});
  invalid.path.steps.clear();Reject([&]{invalid.Validate();});
  auto small=Create<Packet>(7);Reject([&]{small->PeekHeader(invalid);});
  auto corrupt=Create<Packet>(32);Reject([&]{corrupt->PeekHeader(invalid);});
  auto multi=topology;multi["links"].push_back({{"src",0},{"dst",129},{"src_port",2},{"dst_port",1024}});
  Reject([&]{BuildRepairPlans(multi,128,132);});
  Json rows=Json::array();for(const auto& [source,destinations]:plans)for(const auto& [ip,route]:destinations) {
    Json steps=Json::array();for(auto s:route.steps)steps.push_back({s.node,s.port});
    rows.push_back({{"source",source},{"destination_ip",ip},{"steps",steps},{"merge",route.merge}});
  }
  std::cout<<Json{{"passed",true},{"network_executed",false},{"prefixes",rows},
    {"protocol_roundtrips",4},{"illegal_cases_rejected",8},{"prefix_overhead_bytes",38}}.dump(2)<<'\n';return 0;
 }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
