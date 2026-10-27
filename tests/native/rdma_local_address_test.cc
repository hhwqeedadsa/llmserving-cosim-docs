// Actual native objects; no Simulator::Run, packets, GPU or synthetic headers.
#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/rdma-driver.h"
#include <algorithm>
#include <iostream>
#include <stdexcept>

using namespace ns3;
static unsigned checks=0;
static void Check(bool condition,const char* why) {
  ++checks;if(!condition)throw std::runtime_error(why);
}
static void Noop() {}

int main() {
  try {
    Time::SetResolution(Time::NS);
    NodeContainer nodes;nodes.Create(3);
    InternetStackHelper internet;internet.Install(nodes);
    QbbHelper helper;
    helper.SetDeviceAttribute("DataRate",DataRateValue(DataRate("100Gbps")));
    auto first=helper.Install(nodes.Get(0),nodes.Get(1));
    helper.SetDeviceAttribute("DataRate",DataRateValue(DataRate("50Gbps")));
    auto second=helper.Install(nodes.Get(0),nodes.Get(2));
    Ipv4AddressHelper addresses;
    addresses.SetBase("10.1.1.0","255.255.255.0");addresses.Assign(first);
    addresses.SetBase("10.1.2.0","255.255.255.0");addresses.Assign(second);
    auto ip=nodes.Get(0)->GetObject<Ipv4>();
    const Ipv4Address a("12.0.0.1"),b("12.0.0.2"),late("12.0.0.3"),duplicate("12.0.0.4"),remote("12.0.8.1");
    const auto one=ip->GetInterfaceForDevice(first.Get(0)),two=ip->GetInterfaceForDevice(second.Get(0));
    ip->AddAddress(one,Ipv4InterfaceAddress(a,Ipv4Mask("255.0.0.0")));
    ip->AddAddress(two,Ipv4InterfaceAddress(b,Ipv4Mask("255.0.0.0")));
    ip->AddAddress(two,Ipv4InterfaceAddress(late,Ipv4Mask("255.0.0.0")));
    ip->AddAddress(one,Ipv4InterfaceAddress(duplicate,Ipv4Mask("255.0.0.0")));
    ip->AddAddress(two,Ipv4InterfaceAddress(duplicate,Ipv4Mask("255.0.0.0")));
    auto hw=CreateObject<RdmaHw>();hw->SetAttribute("CcMode",UintegerValue(1));
    auto driver=CreateObject<RdmaDriver>();driver->SetNode(nodes.Get(0));driver->SetRdmaHw(hw);
    nodes.Get(0)->AggregateObject(driver);driver->Init();
    Check(!hw->BindLocalAddress(a,0),"loopback accepted");
    Check(!hw->BindLocalAddress(a,99),"nonexistent NIC accepted");
    Check(!hw->BindLocalAddress(a,2),"wrong physical owner accepted");
    Check(!hw->BindLocalAddress(Ipv4Address("12.0.9.9"),1),"unassigned address accepted");
    Check(!hw->BindLocalAddress(duplicate,1),"ambiguous local address accepted");
    Check(hw->BindLocalAddress(a,1),"first physical binding failed");
    Check(hw->BindLocalAddress(b,2),"second physical binding failed");
    Check(hw->BindLocalAddress(a,1),"identical pre-QP registration is not idempotent");
    auto qa=CreateObject<RdmaQueuePair>(3,a,remote,21000,21000);
    auto qb=CreateObject<RdmaQueuePair>(3,b,remote,21001,21001);
    auto legacy=CreateObject<RdmaQueuePair>(3,late,remote,21002,21002);
    hw->m_rtTable[remote.Get()]={2};
    // Keep the zero-byte scheduler entry inspectable: NewQp normally prunes
    // already-finished QPs synchronously. No packet or Simulator::Run occurs.
    hw->m_nic[1].dev->TakeDown();
    Check(hw->GetNicIdxOfQp(qa)==1,"first source followed the destination route instead of its binding");
    hw->m_rtTable[remote.Get()]={1};
    Check(hw->GetNicIdxOfQp(qb)==2,"second source followed the destination route instead of its binding");
    Check(hw->GetNicIdxOfQp(legacy)==1,"unregistered source lost original route behavior");
    auto ra=CreateObject<RdmaRxQueuePair>(),rb=CreateObject<RdmaRxQueuePair>();
    ra->sip=a.Get();ra->dip=remote.Get();rb->sip=b.Get();rb->dip=remote.Get();
    Check(hw->GetNicIdxOfRxQp(ra)==1 && hw->GetNicIdxOfRxQp(rb)==2,"reverse feedback ignores local address");
    hw->ClearTable();
    Check(hw->GetNicIdxOfQp(qa)==1 && hw->GetNicIdxOfQp(qb)==2,"route clearing migrated bound senders");
    Check(hw->GetNicIdxOfRxQp(ra)==1 && hw->GetNicIdxOfRxQp(rb)==2,"route clearing migrated bound receivers");
    hw->m_rtTable[remote.Get()]={2};
    hw->AddQueuePair(0,1,7,0,3,a,remote,21003,21003,0,10000,MakeCallback(&Noop),MakeCallback(&Noop));
    auto qp=hw->GetQp(remote.Get(),21003,3);
    Check(bool(qp) && hw->GetNicIdxOfQp(qp)==1,"actual QP creation ignored source binding");
    const auto& group=hw->m_nic[1].qpGrp->m_qps;
    Check(std::find(group.begin(),group.end(),qp)!=group.end(),"actual QP inserted into wrong scheduler");
    Check(hw->m_nic[2].qpGrp->m_qps.empty(),"QP also inserted into the other scheduler");
    Check(qp->m_rate.GetBitRate()==100000000000ull && qp->m_max_rate.GetBitRate()==100000000000ull,
          "actual QP initialized from the wrong physical rate");
    Check(!hw->BindLocalAddress(late,2),"registration after QP creation accepted");
    hw->DeleteQueuePair(qp);
    Check(!hw->BindLocalAddress(late,2),"deleting QP reopened physical registration");
    Simulator::Destroy();
    std::cout<<"{\"passed\":true,\"native_api_checks\":"<<checks<<",\"network_executed\":false}\n";
    return 0;
  } catch(const std::exception& error) {
    std::cerr<<error.what()<<'\n';Simulator::Destroy();return 1;
  }
}
