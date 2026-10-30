#include "fabric.hh"
#include "ns3/custom-header.h"
#include "repair_wire.hh"

namespace moe_rdma {
void Fabric::ConfigureSwitchDrops() {
  if(!config_.contains("switch_drop_capture"))return;
  const auto& c=config_.at("switch_drop_capture");
  Require(c.is_object() && c.size()==4 && c.contains("nodes") && c.contains("start_ns") &&
    c.contains("end_ns") && c.contains("max_events"),"switch_drop_capture needs nodes/start_ns/end_ns/max_events");
  for(const auto* key:{"start_ns","end_ns","max_events"})Require(c.at(key).is_number_integer(),"switch drop bound must be integer");
  Require(c.at("start_ns")>=0 && c.at("end_ns")>c.at("start_ns") && c.at("end_ns")<=MaxSimNs() &&
    c.at("max_events")>=1 && c.at("max_events")<=100000,"invalid switch drop capture bounds");
  switch_drop_start_=c.at("start_ns");switch_drop_end_=c.at("end_ns");switch_drop_limit_=c.at("max_events");
  Require(c.at("nodes").is_array() && c.at("nodes").size()<=256,"switch drop switch selection exceeded");
  const auto& nodes=topology_.at("nodes");
  for(const auto& n:c.at("nodes")) {
    Require(n.is_number_integer() && n>=0 && n<nodes.size(),"switch drop node must be actual switch integer");
    const auto id=n.get<unsigned>();
    Require(nodes.at(id).at("kind")!="gpu" && switch_drop_nodes_.insert(id).second,"invalid/repeated switch drop node");
  }
}
void Fabric::SwitchDropTrace(Fabric* f,unsigned node,ns3::Ptr<const ns3::Packet> p,
                            uint32_t ingress,int32_t egress,uint32_t priority,uint32_t reason) {
  Require(reason>=1 && reason<=4,"unknown native switch drop reason");
  ++f->switch_drop_totals_[reason-1];
  const auto now=ns3::Simulator::Now().GetNanoSeconds();
  if(now<f->switch_drop_start_ || now>=f->switch_drop_end_ ||
    (!f->switch_drop_nodes_.empty() && !f->switch_drop_nodes_.count(node)))return;
  ++f->switch_drop_observations_;
  if(f->switch_drop_events_.size()>=f->switch_drop_limit_)return;
  unsigned repair_bytes=0;const auto h=LogicalHeader(p,&repair_bytes);
  const auto sequence=h.l3Prot==0x11?int64_t(h.udp.seq):(h.l3Prot==0xfc || h.l3Prot==0xfd)?int64_t(h.ack.seq):-1;
  const int64_t payload=h.l3Prot==0x11?p->GetSize()-repair_bytes-h.GetSerializedSize()-(h.roce.present?h.roce.pad+4:0):0;
  f->switch_drop_events_.push_back({now,node,ingress,egress,priority,reason,static_cast<int64_t>(p->GetUid()),
    f->PacketFlow(p),h.l3Prot,sequence,p->GetSize(),payload});
  // No queue operation, packet event, in-flight decrement, or drain scheduling.
}
Json Fabric::SwitchDropReport() const {
  return {{"schema_version","native-switch-drop-capture/1"},{"selection",config_.at("switch_drop_capture")},
    {"scope","evaluation-only SwitchNode local forwarding/admission drops; device receive errors, Qbb queued discards and artificial faults are separate, not additive without correlation"},
    {"reason_codes",{{"ingress_admission",1},{"egress_admission",2},{"no_route",3},{"link_down",4}}},
    {"all_switch_reason_counts",switch_drop_totals_},
    {"counter_scope","all switches, from construction to Report; detail filters do not limit these counters"},
    {"columns",{"time_ns","node","ingress_port","egress_port","priority","reason","packet_uid","message_id",
      "ip_protocol","sequence","model_bytes","data_payload_bytes"}},
    {"events",switch_drop_events_},{"observations",switch_drop_observations_},
    {"complete",switch_drop_observations_==switch_drop_events_.size()},
    {"omitted_events",switch_drop_observations_-switch_drop_events_.size()},
    {"limitations","complete applies only to selected half-open detail window; UID is lineage; message_id is stream ID in persistent mode; pinned egress admission always passes; no new drops are introduced"}};
}
}
