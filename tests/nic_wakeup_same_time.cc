#include "json/json.hpp"
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/qbb-net-device.h"
#include <fstream>
#include <vector>
using namespace ns3;
using Json = nlohmann::json;
class SameTimeProbe : public QbbNetDevice {
 public:
  std::vector<int64_t> ready;
  void Pause() { m_paused[3] = true; }
  void NativeResume() { Resume(3); }
 protected:
  void DequeueAndTransmit() override {
    auto q = m_rdmaEQ->GetQp(0);
    if (!m_paused[3] && q->GetBytesLeft() && !q->IsWinBound() && q->m_nextAvail <= Simulator::Now()) {
      ready.push_back(Simulator::Now().GetNanoSeconds());
      return;
    }
    QbbNetDevice::DequeueAndTransmit();
  }
};
int main(int argc, char** argv) {
  if (argc != 2) return 2;
  Json cases = Json::array();
  bool passed = true;
  for (bool pause_first : {true, false}) {
    auto node = CreateObject<Node>(), peer = CreateObject<Node>();
    auto dev = CreateObject<SameTimeProbe>(); auto dst = CreateObject<QbbNetDevice>();
    node->AddDevice(dev); peer->AddDevice(dst);
    auto channel = CreateObject<QbbChannel>(); dev->Attach(channel); dst->Attach(channel);
    auto group = CreateObject<RdmaQueuePairGroup>(); dev->m_rdmaEQ->m_qpGrp = group;
    auto q = CreateObject<RdmaQueuePair>(3, Ipv4Address("11.0.0.1"), Ipv4Address("11.0.0.33"), 10000, 10000);
    q->m_size = 1000; q->m_nextAvail = NanoSeconds(50000); group->AddQp(q);
    if (pause_first) Simulator::Schedule(NanoSeconds(50000), &SameTimeProbe::Pause, dev);
    dev->TriggerTransmit();
    if (!pause_first) Simulator::Schedule(NanoSeconds(50000), &SameTimeProbe::Pause, dev);
    Simulator::Schedule(NanoSeconds(60000), &SameTimeProbe::NativeResume, dev);
    Simulator::Stop(NanoSeconds(70000)); Simulator::Run();
    const int64_t expected = pause_first ? 60000 : 50000;
    const bool ok = !dev->ready.empty() && dev->ready.front() == expected;
    passed &= ok;
    cases.push_back({{"pause_inserted_first", pause_first}, {"ready_ns", dev->ready},
      {"expected_first_ready_ns", expected}, {"contract_satisfied", ok}});
    Simulator::Destroy();
  }
  std::ofstream(argv[1]) << Json({{"passed", passed}, {"cases", cases},
    {"scope", "two native ns-3 FIFO event orders at same ns and native Resume; synthetic QP, zero packets"}}).dump(2) << '\n';
  return passed ? 0 : 1;
}
