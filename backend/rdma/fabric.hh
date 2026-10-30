#pragma once
#include "json/json.hpp"
#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/rdma-driver.h"
#include "../communication_admission.hh"
#include "../queue_integral.hh"
#include "recovery.hh"
#include "../periodic_data_loss.hh"
#include <functional>
#include <array>
#include <map>
#include <set>
#include <unordered_map>
#include <memory>

namespace moe_rdma {
using Json = nlohmann::json;
void Require(bool ok, const std::string& why);
Json ReadJson(const std::string& path);

// An independent native RDMA network. No ASTRA execution or UB object lives here.
class Fabric {
  friend class PersistentStreams;
  friend class RcPacketProbe;
 public:
  using Done = std::function<void()>;
  Fabric(const Json& topology, const Json& config);
  void Send(uint64_t id, uint32_t src, uint32_t dst, uint64_t bytes, unsigned plane, Done acked,
            Done received = {});
  bool IntraHostPeer(uint32_t src, uint32_t dst) const;
  // Raw local bytes only; shares SendLocal ports with legacy local traffic.
  // No QP, MRT1 frame, physical packet or RDMA acknowledgement is created.
  void SendPeerCopy(uint64_t id, uint32_t src, uint32_t dst, uint64_t bytes, Done delivered);
  bool Drained() const;
  void FinishWhenDrained(Done);
  Json Report();
  unsigned Planes() const { return planes_; }
  uint64_t MaxMessages() const { return config_.contains("limits") ? config_.at("limits").at("max_messages").get<uint64_t>() : 8192; }
  int64_t MaxSimNs() const { return config_.contains("limits") ? config_.at("limits").at("max_sim_ns").get<int64_t>() : 5000000; }
  bool NetworkEventsPending() const {return pending_link_events_!=0;}
 private:
  struct Neighbor { unsigned peer, port, peer_port; };
  struct NicEndpoint {
    unsigned gpu, nic, plane;
    ns3::Ipv4Address address;
    bool alias;
  };
  struct Hop { unsigned node,port;int64_t time; };
  struct Port {
    Json metadata;
    ns3::Ptr<ns3::QbbNetDevice> device;
    uint64_t wire=0,data=0,control=0,packets=0,ecn=0,pause=0,resume=0;
    uint32_t max_queue=0;
    moe_cosim::QueueIntegral integral;
  };
  struct Flow {
    Done done;
    Done received;
    bool data_ready = false, acked = false;
    uint32_t src, dst;
    unsigned plane;
    uint64_t bytes;
    uint64_t received_bytes=0;
    uint16_t port=0;
    int64_t ready=0,first_tx=-1,receive_ready=-1,ack=-1;
    uint64_t packets=0,payload=0,chunks=0,rate_at_ack=0,source_covered_bytes=0;
    int64_t first_packet_uid=-1;
    int64_t admitted=-1,qp_registered=-1,resource_released=-1;
    bool local=false,feedback=false;
    int64_t ingress_start=-1,ingress_end=-1,egress_start=-1,egress_end=-1;
    std::vector<Hop> path;
  };
  struct EndpointHash {
    size_t operator()(const std::tuple<uint32_t,uint32_t,uint16_t>& key) const noexcept {
      uint64_t value=(uint64_t(std::get<0>(key))<<32)|std::get<1>(key);
      value^=uint64_t(std::get<2>(key))*0x9e3779b97f4a7c15ull;
      value=(value^(value>>30))*0xbf58476d1ce4e5b9ull;
      value=(value^(value>>27))*0x94d049bb133111ebull;
      return value^(value>>31);
    }
  };
  static void Noop();
  static void TxTrace(Fabric*, unsigned, unsigned, ns3::Ptr<const ns3::Packet>);
  static void RxTrace(Fabric*, unsigned, unsigned, ns3::Ptr<const ns3::Packet>);
  static void DropTrace(Fabric*, unsigned, unsigned, ns3::Ptr<const ns3::Packet>);
  static void LocalDropTrace(Fabric*, unsigned, unsigned, ns3::Ptr<const ns3::Packet>, uint32_t);
  static void SwitchDropTrace(Fabric*, unsigned, ns3::Ptr<const ns3::Packet>, uint32_t, int32_t, uint32_t, uint32_t);
  void ConfigureSwitchDrops();
  Json SwitchDropReport() const;
  static void QueueTrace(Fabric*, unsigned, unsigned, ns3::Ptr<const ns3::Packet>, uint32_t);
  static void QueueEnqueueTrace(Fabric*, unsigned, unsigned, ns3::Ptr<const ns3::Packet>, uint32_t);
  static void QueueDequeueTrace(Fabric*, unsigned, unsigned, ns3::Ptr<const ns3::Packet>, uint32_t);
  static void PfcTrace(Fabric*, unsigned, unsigned, uint32_t);
  void Acked(ns3::Ptr<ns3::RdmaQueuePair> qp);
  void CheckReceived(uint64_t id);
  void SendLocal(uint64_t id);
  void Inject(uint64_t id);
  void PumpAdmission(unsigned source);
  void ReleaseSender(uint64_t id);
  void Lifecycle(unsigned event,uint64_t id,unsigned source);
  void CheckDrain();
  void RebuildRoutes();
  std::vector<int> RouteDistances(unsigned dst,unsigned plane,unsigned destination_nic=0) const;
  void InstallDestinationRoutes(unsigned dst,unsigned plane,ns3::Ipv4Address,unsigned destination_nic=0);
  void EnableNicBindings();
  ns3::Ipv4Address NicAddress(unsigned gpu,unsigned plane,unsigned nic) const;
  bool NicConnectionReachable(unsigned src,unsigned source_nic,unsigned dst,unsigned destination_nic,unsigned plane) const;
  void InstallLocalRoutes(unsigned node,bool record=false,unsigned event_index=0,const char* state=nullptr);
  void FailLink(Json event,unsigned index);
  void RestoreLink(Json event,unsigned index);
  void UpdateRoutes(Json event,unsigned index,unsigned revision);
  int64_t PacketFlow(ns3::Ptr<const ns3::Packet>) const;
  void PacketEvent(unsigned event, unsigned node, unsigned port, ns3::Ptr<const ns3::Packet>);
  bool Corrupt(unsigned node, unsigned port, ns3::Ptr<ns3::Packet>);
  ns3::Ipv4Address Address(unsigned gpu, unsigned plane) const;
  Json topology_, config_;
  Json rdma_parameters_=Json::array();
  ns3::NodeContainer nodes_;
  std::vector<ns3::Ptr<ns3::RdmaDriver>> drivers_;
  std::vector<std::vector<Neighbor>> adjacency_;
  std::map<std::pair<unsigned,unsigned>,NicEndpoint> nic_endpoints_;
  bool multi_nic_addresses_=false;
  std::map<std::pair<unsigned,unsigned>, ns3::Ptr<ns3::QbbNetDevice>> devices_;
  std::vector<std::vector<Port>> ports_;
  std::vector<std::array<uint64_t,4>> queue_events_;
  std::vector<uint8_t> reachable_;
  std::unordered_map<uint64_t, Flow> flows_;
  std::unordered_map<std::tuple<uint32_t,uint32_t,uint16_t>, uint64_t,EndpointHash> port_to_flow_;
  // Evaluation/fault association only. Persistent IDs are never legacy Flows.
  std::unordered_map<std::tuple<uint32_t,uint32_t,uint16_t>, uint64_t,EndpointHash> stream_packet_ids_;
  std::map<std::pair<unsigned,bool>,int64_t> local_free_;
  std::map<std::pair<unsigned,bool>,Json> local_links_;
  uint64_t queue_observations_=0;
  unsigned gpus_ = 0, planes_ = 0;
  unsigned fault_drops_ = 0;
  std::vector<Json> fault_rules_;
  std::vector<uint64_t> fault_counts_;
  std::vector<std::array<int64_t,5>> fault_events_;
  std::unique_ptr<moe_cosim::PeriodicDataLoss> data_loss_;
  std::set<unsigned> data_loss_nodes_;
  std::vector<std::array<int64_t,12>> packet_events_;
  std::vector<std::array<int64_t,7>> wire_headers_;
  std::map<std::array<int64_t,7>,uint64_t> wire_header_indices_;
  std::vector<uint64_t> event_wire_headers_;
  bool capture_wire_headers_=false;
  uint64_t packet_observations_ = 0;
  uint64_t switch_drop_limit_=0,switch_drop_observations_=0;
  int64_t switch_drop_start_=0,switch_drop_end_=0;
  std::set<unsigned> switch_drop_nodes_;
  std::array<uint64_t,4> switch_drop_totals_{};
  std::vector<std::array<int64_t,12>> switch_drop_events_;
  std::set<unsigned> capture_nodes_;
  uint64_t capture_limit_ = 0;
  int64_t capture_start_ = 0, capture_end_ = 0;
  bool resource_tracking_=false;
  std::string admission_policy_="passthrough";
  uint32_t admission_limit_=0;
  uint64_t admission_byte_limit_=0;
  uint32_t admission_peer_limit_=0;
  uint64_t admission_peer_bytes_=0;
  bool byte_tracking_=false,queue_metrics_=false;
  int64_t decision_ns_=0;
  std::vector<std::unique_ptr<moe_cosim::CommunicationAdmission>> admission_;
  std::vector<bool> deciding_;
  std::vector<std::array<int64_t,8>> lifecycle_events_;
  uint64_t lifecycle_limit_=0,lifecycle_observations_=0;
  std::unique_ptr<RdmaRecovery> recovery_;
  int64_t inflight_packets_=0,failure_id_=-1,failure_ns_=-1;
  Done finish_;
  bool drain_check_pending_=false;
  bool persistent_stream_mode_=false;
  std::set<std::pair<unsigned,unsigned>> routing_down_;
  std::map<unsigned,unsigned> known_link_revision_;
  uint64_t pending_link_events_=0,route_epoch_=0,local_drops_=0;
  Json link_events_=Json::array();
  Done network_state_changed_;
  bool local_failover_=false;
  std::map<unsigned,std::map<uint32_t,std::vector<unsigned>>> route_candidates_;
  Json local_route_updates_=Json::array();
};
}
