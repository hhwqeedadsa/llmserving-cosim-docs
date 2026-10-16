#pragma once
#include "json/json.hpp"
#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/rdma-driver.h"
#include <functional>
#include <array>
#include <map>
#include <set>

namespace moe_rdma {
using Json = nlohmann::json;
void Require(bool ok, const std::string& why);
Json ReadJson(const std::string& path);

// An independent native RDMA network. No ASTRA execution or UB object lives here.
class Fabric {
 public:
  using Done = std::function<void()>;
  Fabric(const Json& topology, const Json& config);
  void Send(uint64_t id, uint32_t src, uint32_t dst, uint64_t bytes, unsigned plane, Done acked,
            Done received = {});
  bool Drained() const;
  Json Report();
  unsigned Planes() const { return planes_; }
  uint64_t MaxMessages() const { return config_.contains("limits") ? config_.at("limits").at("max_messages").get<uint64_t>() : 8192; }
  int64_t MaxSimNs() const { return config_.contains("limits") ? config_.at("limits").at("max_sim_ns").get<int64_t>() : 5000000; }
 private:
  struct Neighbor { unsigned peer, port; };
  struct Flow {
    Json record;
    Done done;
    Done received;
    bool data_ready = false, acked = false;
    uint32_t src, dst;
    unsigned plane;
    uint64_t bytes;
    uint64_t received_bytes=0;
  };
  static void Noop();
  static void TxTrace(Fabric*, unsigned, unsigned, ns3::Ptr<const ns3::Packet>);
  static void RxTrace(Fabric*, unsigned, unsigned, ns3::Ptr<const ns3::Packet>);
  static void QueueTrace(Fabric*, unsigned, unsigned, ns3::Ptr<const ns3::Packet>, uint32_t);
  static void PfcTrace(Fabric*, unsigned, unsigned, uint32_t);
  void Acked(ns3::Ptr<ns3::RdmaQueuePair> qp);
  void CheckReceived(uint64_t id);
  void SendLocal(uint64_t id);
  ns3::Ipv4Address Address(unsigned gpu, unsigned plane) const;
  Json topology_, config_;
  ns3::NodeContainer nodes_;
  std::vector<ns3::Ptr<ns3::RdmaDriver>> drivers_;
  std::vector<std::vector<Neighbor>> adjacency_;
  std::map<std::pair<unsigned,unsigned>, ns3::Ptr<ns3::QbbNetDevice>> devices_;
  std::map<std::pair<unsigned,unsigned>, Json> ports_;
  std::vector<std::array<uint64_t,4>> queue_events_;
  std::map<std::tuple<unsigned,unsigned,unsigned>, bool> reachable_;
  std::map<uint64_t, Flow> flows_;
  std::map<std::tuple<uint32_t,uint32_t,uint16_t>, uint64_t> port_to_flow_;
  std::map<std::pair<unsigned,bool>,int64_t> local_free_;
  std::map<std::pair<unsigned,bool>,Json> local_links_;
  uint64_t queue_observations_=0;
  unsigned gpus_ = 0, planes_ = 0;
  unsigned fault_drops_ = 0;
};
}
