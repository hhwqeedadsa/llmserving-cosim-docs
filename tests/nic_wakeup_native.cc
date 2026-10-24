#include "json/json.hpp"
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/qbb-net-device.h"
#include <fstream>
#include <string>
#include <vector>

using namespace ns3;
using Json = nlohmann::json;

// Exercise the real native queue selection and timer callback. The override
// stops at a ready QP, before packet creation: these are CPU state tests.
class WakeProbe : public QbbNetDevice {
 public:
  std::vector<int64_t> calls, ready;
  Json scheduled = Json::array();
  void Pause(unsigned pg, bool value) { m_paused[pg] = value; }
 protected:
  void DequeueAndTransmit() override {
    const auto now = Simulator::Now().GetNanoSeconds(); calls.push_back(now);
    bool available = false;
    for (const auto& qp : m_rdmaEQ->m_qpGrp->m_qps) {
      available |= !m_paused[qp->m_pg] && qp->GetBytesLeft() && !qp->IsWinBound() && qp->m_nextAvail <= Simulator::Now();
    }
    if (available) { ready.push_back(now); return; }
    QbbNetDevice::DequeueAndTransmit();
    scheduled.push_back({{"time_ns", now}, {"pending", !m_nextSend.IsExpired()},
      {"due_ns", m_nextSend.IsExpired() ? -1 : static_cast<int64_t>(m_nextSend.GetTs())}});
  }
};

Ptr<RdmaQueuePair> Qp(unsigned pg, int64_t due) {
  auto q = CreateObject<RdmaQueuePair>(pg, Ipv4Address("11.0.0.1"), Ipv4Address("11.0.0.33"), 10000, 10000);
  q->m_size = 1000; q->m_nextAvail = NanoSeconds(due); return q;
}

Json Case(const std::string& name) {
  auto node = CreateObject<Node>(); auto peer = CreateObject<Node>();
  auto dev = CreateObject<WakeProbe>(); auto other = CreateObject<QbbNetDevice>();
  node->AddDevice(dev); peer->AddDevice(other);
  auto channel = CreateObject<QbbChannel>(); dev->Attach(channel); other->Attach(channel);
  dev->m_rdmaEQ->m_qpGrp = CreateObject<RdmaQueuePairGroup>();
  auto active = Qp(3, 50000); dev->m_rdmaEQ->m_qpGrp->AddQp(active);
  int64_t expected = 50000;
  if (name == "empty-unacked" || name == "all-blocked" || name == "resume-with-stale") {
    auto stale = Qp(2, 0); stale->snd_nxt = 1000; dev->m_rdmaEQ->m_qpGrp->AddQp(stale);
  }
  if (name == "window-blocked") {
    auto blocked = Qp(2, 0); blocked->m_size = 2000; blocked->snd_nxt = 1000;
    blocked->SetWin(1000); dev->m_rdmaEQ->m_qpGrp->AddQp(blocked);
  }
  if (name == "paused") {
    dev->m_rdmaEQ->m_qpGrp->AddQp(Qp(2, 0)); dev->Pause(2, true);
  }
  if (name == "all-blocked") { active->snd_nxt = 1000; expected = -1; }
  if (name == "blocked-future") {
    active->m_size = 2000; active->snd_nxt = 1000; active->SetWin(1000); expected = -1;
  }
  if (name == "earlier-append") {
    expected = 10000;
    Simulator::Schedule(NanoSeconds(1000), [dev] {
      dev->m_rdmaEQ->m_qpGrp->AddQp(Qp(3, 10000)); dev->TriggerTransmit();
    });
  }
  if (name == "rate-earlier" || name == "rate-later") {
    expected = name == "rate-earlier" ? 10000 : 80000;
    Simulator::Schedule(NanoSeconds(1000), [dev, active, expected] {
      active->m_nextAvail = NanoSeconds(expected); dev->UpdateNextAvail(active->m_nextAvail);
    });
  }
  if (name == "resume-with-stale") {
    dev->Pause(3, true);
    Simulator::Schedule(NanoSeconds(1000), [dev] { dev->Pause(3, false); dev->TriggerTransmit(); });
  }
  if (name == "window-release") {
    active->m_size = 2000; active->snd_nxt = 1000; active->SetWin(1000);
    Simulator::Schedule(NanoSeconds(1000), [dev, active] { active->Acknowledge(1000); dev->TriggerTransmit(); });
  }
  dev->TriggerTransmit(); Simulator::Stop(NanoSeconds(100000)); Simulator::Run();
  const bool pass = expected < 0 ? dev->ready.empty() : !dev->ready.empty() && dev->ready.front() == expected;
  Json result = {{"name", name}, {"expected_first_ready_ns", expected}, {"calls_ns", dev->calls},
    {"ready_ns", dev->ready}, {"scheduled", dev->scheduled}, {"contract_satisfied", pass},
    {"data_packets", 0}, {"scope", "synthetic QP state through actual native queue/timer; stops before packet creation"}};
  Simulator::Destroy(); return result;
}

int main(int argc, char** argv) {
  if (argc != 3) return 2;
  Json out; out["cases"] = Json::array(); bool pass = true;
  for (const auto* name : {"single", "empty-unacked", "window-blocked", "paused", "all-blocked",
       "blocked-future", "earlier-append", "rate-earlier", "rate-later", "resume-with-stale", "window-release"}) {
    auto row = Case(name); pass &= row.at("contract_satisfied").get<bool>(); out["cases"].push_back(row);
  }
  out["all_contracts_satisfied"] = pass; out["native_library_profile"] = argv[1];
  out["scope"] = "11 native CPU scheduler-state cases; zero data/control packets, no Fabric or inference";
  std::ofstream(argv[2]) << out.dump(2) << '\n';
  return std::string(argv[1]) == "fixed" && !pass ? 1 : 0;
}
