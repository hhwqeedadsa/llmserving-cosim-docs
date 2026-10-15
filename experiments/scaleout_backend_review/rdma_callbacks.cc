// Direct-link RDMA callback probe. This is not a switch/fabric/service benchmark.
#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/rdma-driver.h"
#include "ns3/rdma-hw.h"
#include <array>
#include <iostream>
#include <vector>

using namespace ns3;
struct Record {
  uint32_t mtu;
  uint64_t bytes;
  unsigned sent = 0, acked = 0;
  int64_t first_sent_ns = -1, acked_ns = -1;
};
static std::vector<Record> records;
static void Noop() {}
static void Sent(Ptr<RdmaQueuePair> qp) {
  auto& r = records.at(qp->m_tag);
  ++r.sent;
  if (r.first_sent_ns < 0) r.first_sent_ns = Simulator::Now().GetNanoSeconds();
}
static void Acked(Ptr<RdmaQueuePair> qp) {
  auto& r = records.at(qp->m_tag);
  ++r.acked;
  r.acked_ns = Simulator::Now().GetNanoSeconds();
}

int main() {
  NodeContainer nodes;
  nodes.Create(2);
  InternetStackHelper internet;
  internet.Install(nodes);
  QbbHelper qbb;
  qbb.SetDeviceAttribute("DataRate", StringValue("100Gbps"));
  qbb.SetChannelAttribute("Delay", StringValue("1us"));
  auto devices = qbb.Install(nodes.Get(0), nodes.Get(1));
  Ipv4AddressHelper addresses;
  addresses.SetBase("10.0.0.0", "255.255.255.0");
  auto ips = addresses.Assign(devices);
  std::array<Ptr<RdmaHw>, 2> hw;
  std::array<Ptr<RdmaDriver>, 2> drivers;
  for (unsigned i = 0; i < 2; ++i) {
    hw[i] = CreateObject<RdmaHw>();
    hw[i]->SetAttribute("CcMode", UintegerValue(1));
    hw[i]->SetAttribute("GPUsPerServer", UintegerValue(1));
    hw[i]->SetAttribute("L2AckInterval", UintegerValue(1));
    hw[i]->SetAttribute("L2ChunkSize", UintegerValue(4000));
    drivers[i] = CreateObject<RdmaDriver>();
    drivers[i]->SetNode(nodes.Get(i));
    drivers[i]->SetRdmaHw(hw[i]);
    nodes.Get(i)->AggregateObject(drivers[i]);
    drivers[i]->Init();
    Ipv4Address peer = ips.GetAddress(1-i);
    hw[i]->AddTableEntry(peer, devices.Get(i)->GetIfIndex(), false);
    drivers[i]->TraceConnectWithoutContext("SendComplete", MakeCallback(&Sent));
    drivers[i]->TraceConnectWithoutContext("QpComplete", MakeCallback(&Acked));
  }
  // Every case has a distinct source port; no QP or callback identity is reused.
  for (uint32_t mtu : {1000u, 9000u}) {
    for (uint64_t size : {uint64_t(mtu-1), uint64_t(mtu), uint64_t(mtu+1), uint64_t(3*mtu)}) {
      const unsigned index = records.size();
      records.push_back({mtu, size});
      Simulator::Schedule(MicroSeconds(10+index*100), [=]() {
        hw[0]->SetAttribute("Mtu", UintegerValue(mtu));
        drivers[0]->AddQueuePair(0, 1, index, size, 3, ips.GetAddress(0), ips.GetAddress(1),
                                10000+index, 100, 0, 2200,
                                MakeCallback(&Noop), MakeCallback(&Noop));
      });
    }
  }
  Simulator::Stop(MilliSeconds(1));
  Simulator::Run();
  bool qp_ok = true, sent_ok = true;
  std::cout << "{\"scope\":\"direct-link RDMA; no switch, ASTRA or UB\",\"cases\":[";
  for (unsigned i = 0; i < records.size(); ++i) {
    auto& r = records[i];
    if (i) std::cout << ',';
    std::cout << "{\"payload_bytes\":" << r.mtu << ",\"message_bytes\":" << r.bytes
              << ",\"send_callbacks\":" << r.sent << ",\"qp_callbacks\":" << r.acked
              << ",\"first_send_callback_ns\":" << r.first_sent_ns
              << ",\"qp_complete_ns\":" << r.acked_ns << '}';
    qp_ok &= r.acked == 1;
    sent_ok &= r.sent == 1;
  }
  std::cout << "],\"all_qps_completed_once\":" << (qp_ok ? "true" : "false")
            << ",\"all_send_callbacks_once\":" << (sent_ok ? "true" : "false") << "}\n";
  Simulator::Destroy();
  // A defective upstream send trace is a result, not a successful semantic check.
  return qp_ok ? 0 : 1;
}
