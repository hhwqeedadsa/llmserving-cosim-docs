#pragma once
#include "ns3/qbb-net-device.h"
#include "ns3/qbb-channel.h"
#include "ns3/mac48-address.h"
#include "ns3/data-rate.h"
#include "ns3/nstime.h"
#include "ns3/net-device-container.h"
#include <stdexcept>

namespace moe_rdma {
// Opt-in switch port. The pinned TakeDown/Receive/TransmitComplete implementations
// remain in charge of queue drops, arrival acceptance and any current packet.
class RecoverableQbb final: public ns3::QbbNetDevice {
 public:
  static ns3::TypeId GetTypeId() {
    static ns3::TypeId tid=ns3::TypeId("moe_rdma::RecoverableQbb")
      .SetParent<ns3::QbbNetDevice>().AddConstructor<RecoverableQbb>();
    return tid;
  }
  void RestoreSwitchPort() {
    if(!m_node || m_node->GetNodeType()==0 || m_linkUp)
      throw std::runtime_error("restore requires a down switch port");
    // Never reset BUSY/currentPkt: its original completion can still be pending.
    NotifyLinkUp();
  }
};

// QbbHelper has a private fixed device factory. Reproduce its serial installation
// for only the two explicitly recoverable switch ports; no re-attachment on up.
inline ns3::NetDeviceContainer InstallRecoverableLink(ns3::Ptr<ns3::Node> a,ns3::Ptr<ns3::Node> b,
                                                     uint64_t rate,int64_t delay) {
  using namespace ns3;
  auto channel=CreateObject<QbbChannel>();channel->SetAttribute("Delay",TimeValue(NanoSeconds(delay)));
  NetDeviceContainer devices;
  for(auto node:{a,b}) {
    auto dev=CreateObject<RecoverableQbb>();
    dev->SetAttribute("DataRate",DataRateValue(DataRate(rate)));
    dev->SetAddress(Mac48Address::Allocate());node->AddDevice(dev);
    dev->SetQueue(CreateObject<BEgressQueue>());
    dev->Attach(channel);devices.Add(dev);
  }
  return devices;
}
}
