// Native state-transition probe, NOT a network performance or recovery test.
#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/switch-node.h"
#include "json/json.hpp"
#include <fstream>
#include <iostream>
#include <vector>
using namespace ns3;
using Json=nlohmann::json;
struct Record {Json qbb=Json::array(),phy=Json::array(),tx=Json::array();};
static void QueueDrop(Record* r,Ptr<const Packet> p,uint32_t q) {
  r->qbb.push_back({{"uid",p->GetUid()},{"bytes",p->GetSize()},{"queue",q}});
}
static void PhyDrop(Record* r,Ptr<const Packet> p) {r->phy.push_back({{"uid",p->GetUid()},{"bytes",p->GetSize()}});}
static void Tx(Record* r,Ptr<const Packet> p) {r->tx.push_back({{"uid",p->GetUid()},{"bytes",p->GetSize()}});}
static void Need(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
int main(int argc,char**argv) {
  try {
    Need(argc==2,"require new output path");Time::SetResolution(Time::NS);
    NodeContainer nodes;nodes.Add(CreateObject<Node>());nodes.Add(CreateObject<Node>());auto sw=CreateObject<SwitchNode>();nodes.Add(sw);
    InternetStackHelper stack;stack.Install(nodes);QbbHelper helper;
    helper.SetDeviceAttribute("DataRate",DataRateValue(DataRate(100000000000ULL)));
    helper.SetChannelAttribute("Delay",TimeValue(NanoSeconds(1000)));
    helper.Install(nodes.Get(0),sw);auto second=helper.Install(sw,nodes.Get(1));
    auto dev=DynamicCast<QbbNetDevice>(second.Get(0));Need(dev->GetIfIndex()==2,"unexpected switch port");
    auto mmu=sw->m_mmu;mmu->ConfigBufferSize(8388608);
    for(unsigned port=1;port<=2;++port){mmu->ConfigHdrm(port,37500);mmu->pfc_a_shift[port]=3;}
    mmu->ConfigNPort(2);Record events;
    Need(dev->TraceConnectWithoutContext("QbbDrop",MakeBoundCallback(&QueueDrop,&events)),"QbbDrop trace missing");
    Need(dev->TraceConnectWithoutContext("PhyRxDrop",MakeBoundCallback(&PhyDrop,&events)),"PhyRxDrop trace missing");
    Need(dev->TraceConnectWithoutContext("PhyTxBegin",MakeBoundCallback(&Tx,&events)),"PhyTxBegin trace missing");
    Json seeds=Json::array();
    auto add=[&](unsigned q,unsigned bytes) {
      auto packet=Create<Packet>(bytes);
      if(q) {
        Need(mmu->CheckIngressAdmission(1,q,bytes) && mmu->CheckEgressAdmission(2,q,bytes),"seed not admitted");
        mmu->UpdateIngressAdmission(1,q,bytes);mmu->UpdateEgressAdmission(2,q,bytes);
      }
      Need(dev->GetQueue()->Enqueue(packet,q),"seed enqueue failed");
      seeds.push_back({{"uid",packet->GetUid()},{"queue",q},{"bytes",bytes}});
    };
    for(unsigned i=0;i<8;++i)add(3,1000);
    for(unsigned i=0;i<6;++i)add(2,1000);
    add(0,96);
    auto state=[&]() {
      Json ingress=Json::array(),egress=Json::array(),hdrm=Json::array();
      for(unsigned q=0;q<8;++q){ingress.push_back(mmu->ingress_bytes[1][q]);egress.push_back(mmu->egress_bytes[2][q]);hdrm.push_back(mmu->hdrm_bytes[1][q]);}
      return Json{{"queued_bytes",dev->GetQueue()->GetNBytesTotal()},{"ingress",ingress},{"egress",egress},
        {"headroom_used",hdrm},{"shared_used_bytes",mmu->shared_used_bytes},{"is_link_up",dev->IsLinkUp()}};
    };
    Json result={{"schema_version","native-link-down-contract/1"},{"seeds",seeds},{"reserve_bytes",mmu->reserve},
      {"before",state()},{"simulator_run_called",false},{"network_flows_executed",0},{"manually_seeded_state",true}};
    dev->TakeDown();result["after_take_down"]=state();result["queue_drop_events"]=events.qbb;
    auto late=Create<Packet>(64);result["late_receive_uid"]=late->GetUid();dev->Receive(late);
    result["after_down_receive"]=state();result["all_qbb_drop_events"]=events.qbb;
    // Explicit consistency control only. It uses the seed journal, which is
    // evaluation truth; this is NOT a deployable teardown implementation.
    for(const auto& seed:seeds)if(seed.at("queue")!=0) {
      mmu->RemoveFromIngressAdmission(1,seed.at("queue"),seed.at("bytes"));
      mmu->RemoveFromEgressAdmission(2,seed.at("queue"),seed.at("bytes"));
    }
    result["after_explicit_release"]=state();result["phy_rx_drop_events"]=events.phy;result["phy_tx_events"]=events.tx;
    result["scope"]="native manually seeded queue/MMU transition; no Simulator::Run, routed traffic, adapter fix or hardware claim";
    std::ifstream existing(argv[1]);Need(!existing.good(),"output exists");std::ofstream output(argv[1]);Need(bool(output),"cannot create output");output<<result.dump(2)<<'\n';output.close();
    Simulator::Destroy();std::cout<<"native state transition saved\n";return 0;
  }catch(const std::exception& error){std::cerr<<error.what()<<'\n';Simulator::Destroy();return 1;}
}
