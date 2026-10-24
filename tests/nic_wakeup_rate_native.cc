#include "json/json.hpp"
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/qbb-net-device.h"
#include "ns3/rdma-hw.h"
#include <fstream>
using namespace ns3;
using Json=nlohmann::json;
class RateProbe:public QbbNetDevice {
 public:
  Json ready=Json::array();
  bool Pending() const{return !m_nextSend.IsExpired();}
 protected:
  void DequeueAndTransmit() override {
    for(const auto& q:m_rdmaEQ->m_qpGrp->m_qps)
      if(q->GetBytesLeft() && !q->IsWinBound() && q->m_nextAvail<=Simulator::Now()){
        ready.push_back(Simulator::Now().GetNanoSeconds());return;
      }
    QbbNetDevice::DequeueAndTransmit();
  }
};
int main(int argc,char**argv){
  if(argc!=3)return 2;
  Json rows=Json::array();bool pass=true;
  for(bool increase:{true,false}){
    auto n=CreateObject<Node>(),p=CreateObject<Node>();auto d=CreateObject<RateProbe>();auto peer=CreateObject<QbbNetDevice>();
    n->AddDevice(d);p->AddDevice(peer);auto ch=CreateObject<QbbChannel>();d->Attach(ch);peer->Attach(ch);
    auto hw=CreateObject<RdmaHw>();hw->SetNode(n);hw->m_nic.resize(1);hw->m_nic[0].dev=d;
    auto group=CreateObject<RdmaQueuePairGroup>();hw->m_nic[0].qpGrp=group;d->m_rdmaEQ->m_qpGrp=group;
    auto q=CreateObject<RdmaQueuePair>(3,Ipv4Address("11.0.0.1"),Ipv4Address("11.0.0.33"),10000,10000);
    q->m_size=2000;q->snd_nxt=500;q->m_win=1000;q->m_var_win=true;q->m_max_rate=DataRate(100000000000ULL);
    q->m_rate=DataRate(increase?10000000000ULL:100000000000ULL);q->lastPktSize=1000;
    q->m_nextAvail=NanoSeconds(increase?1000:50000);group->AddQp(q);hw->m_rtTable[q->dip.Get()]={0};
    bool pending=false,bound=true;
    d->TriggerTransmit();
    Simulator::Schedule(NanoSeconds(2000),[hw,q,d,increase,&pending,&bound]{
      hw->ChangeRate(q,DataRate(increase?100000000000ULL:10000000000ULL));pending=d->Pending();bound=q->IsWinBound();
    });
    Simulator::Stop(NanoSeconds(100000));Simulator::Run();
    const bool ok=increase?pending && !bound && d->ready==Json::array({2000}):!pending && bound && d->ready.empty();
    pass&=ok;rows.push_back({{"increase",increase},{"pending_after_change",pending},{"window_bound_after_change",bound},
      {"ready_ns",d->ready},{"contract_satisfied",ok}});Simulator::Destroy();
  }
  std::ofstream(argv[2])<<Json({{"cases",rows},{"passed",pass},{"scope","actual native ChangeRate with synthetic variable-window QP states; zero packets"}}).dump(2)<<'\n';
  return std::string(argv[1])=="fixed"&&!pass?1:0;
}
