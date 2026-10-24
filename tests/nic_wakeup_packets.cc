#include "json/json.hpp"
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/custom-header.h"
#include "ns3/qbb-net-device.h"
#include "ns3/rdma-hw.h"
#include <fstream>
#include <stdexcept>

using namespace ns3;
using Json = nlohmann::json;
static Json frames = Json::array(), selections = Json::array(), completed = Json::array();

void Frame(unsigned node, unsigned event, Ptr<const Packet> packet) {
  if (frames.size() >= 1000) throw std::runtime_error("frame cap");
  CustomHeader h(CustomHeader::L2_Header|CustomHeader::L3_Header|CustomHeader::L4_Header);
  h.getInt = 1; packet->PeekHeader(h);
  frames.push_back({{"time_ns", Simulator::Now().GetNanoSeconds()}, {"node", node}, {"event", event},
    {"uid", packet->GetUid()}, {"bytes", packet->GetSize()}, {"protocol", static_cast<unsigned>(h.l3Prot)},
    {"sequence", h.l3Prot == 17 ? h.udp.seq : h.ack.seq},
    {"port", h.l3Prot == 17 ? h.udp.sport : h.ack.dport},
    {"payload_bytes", h.l3Prot == 17 ? packet->GetSize()-h.GetSerializedSize() : 0}});
}

// Passive state recording around actual native selection; no extra events.
class LoggedNic : public QbbNetDevice {
 protected:
  void DequeueAndTransmit() override {
    if (selections.size() >= 1000) throw std::runtime_error("selection cap");
    Json state = {{"time_ns", Simulator::Now().GetNanoSeconds()}, {"busy", m_txMachineState != READY}, {"qps", Json::array()}};
    for (const auto& q : m_rdmaEQ->m_qpGrp->m_qps) {
      state["qps"].push_back({{"port", q->sport}, {"published", q->m_size}, {"snd_nxt", q->snd_nxt},
        {"snd_una", q->snd_una}, {"rate_bps", q->m_rate.GetBitRate()}, {"next_avail_ns", q->m_nextAvail.GetNanoSeconds()},
        {"window_bound", q->IsWinBound()}, {"paused", m_paused[q->m_pg]}});
    }
    QbbNetDevice::DequeueAndTransmit();
    state["wake_due_ns"] = m_nextSend.IsExpired() ? -1 : static_cast<int64_t>(m_nextSend.GetTs());
    selections.push_back(state);
  }
};

void Noop() {}
void Finish(Ptr<RdmaQueuePair> q) {
  completed.push_back({{"port", q->sport}, {"time_ns", Simulator::Now().GetNanoSeconds()},
    {"snd_una", q->snd_una}, {"bytes", q->m_size}});
}

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  auto source = CreateObject<Node>(), target = CreateObject<Node>();
  auto src = CreateObject<LoggedNic>(); auto dst = CreateObject<QbbNetDevice>();
  source->AddDevice(src); target->AddDevice(dst);
  src->SetDataRate(DataRate(100000000000ULL)); dst->SetDataRate(DataRate(100000000000ULL));
  auto channel = CreateObject<QbbChannel>(); channel->SetAttribute("Delay", TimeValue(NanoSeconds(10000)));
  src->Attach(channel); dst->Attach(channel);
  auto sh = CreateObject<RdmaHw>(), dh = CreateObject<RdmaHw>();
  auto sip = Ipv4Address("11.0.0.1"), dip = Ipv4Address("11.0.0.33");
  for (auto hw : {sh, dh}) {
    hw->SetAttribute("Mtu", UintegerValue(1000)); hw->SetAttribute("CcMode", UintegerValue(1));
    hw->SetAttribute("L2ChunkSize", UintegerValue(4000)); hw->SetAttribute("L2AckInterval", UintegerValue(1));
    hw->SetAttribute("L2BackToZero", BooleanValue(false)); hw->SetAttribute("RateBound", BooleanValue(true));
    hw->SetAttribute("TotalPauseTimes", UintegerValue(0)); hw->m_nic.resize(1);
    hw->m_nic[0].qpGrp = CreateObject<RdmaQueuePairGroup>();
  }
  sh->SetNode(source); dh->SetNode(target); sh->m_nic[0].dev = src; dh->m_nic[0].dev = dst;
  sh->m_rtTable[dip.Get()] = {0}; dh->m_rtTable[sip.Get()] = {0};
  sh->Setup(MakeCallback(&Finish)); dh->Setup(MakeCallback(&Finish));
  src->TraceConnectWithoutContext("PhyTxBegin", MakeBoundCallback(&Frame, 0u, 0u));
  dst->TraceConnectWithoutContext("PhyTxBegin", MakeBoundCallback(&Frame, 1u, 0u));
  src->TraceConnectWithoutContext("MacRx", MakeBoundCallback(&Frame, 0u, 1u));
  dst->TraceConnectWithoutContext("MacRx", MakeBoundCallback(&Frame, 1u, 1u));
  sh->AddQueuePair(0, 1, 1, 1000, 3, sip, dip, 20000, 20000, 0, 20000, MakeCallback(&Noop), MakeCallback(&Noop));
  sh->AddQueuePair(0, 1, 2, 16000, 3, sip, dip, 20001, 20001, 0, 20000, MakeCallback(&Noop), MakeCallback(&Noop));
  // The second QP is queued behind the first actual physical transmission.
  // Its declared initial rate is set before its first packet, not via a
  // ChangeRate call on an uninitialized lastPktSize.
  auto paced = sh->GetQp(dip.Get(), 20001, 3); paced->m_rate = DataRate(10000000000ULL);
  paced->mlx.m_targetRate = paced->m_rate;
  Simulator::Stop(NanoSeconds(100000)); Simulator::Run();
  Json out = {{"frames", frames}, {"selections", selections}, {"completed", completed},
    {"channel_delay_ns", 10000}, {"physical_rate_bps", 100000000000ULL},
    {"initial_rates_bps", {100000000000ULL, 10000000000ULL}},
    {"scope", "actual native two-QP packet/ACK test with declared initial rate; no Fabric, inference, faults or hardware calibration"}};
  std::ofstream(argv[1]) << out.dump(2) << '\n'; Simulator::Destroy();
  return completed.size() == 2 ? 0 : 1;
}
