#pragma once
#include "fabric.hh"
#include <algorithm>

namespace moe_rdma {
inline uint64_t SelfInteger(const Json& value,uint64_t maximum) {
  Require(value.is_number_integer() && (value.is_number_unsigned() || value.get<int64_t>()>=0),"invalid self-peer integer");
  const auto n=value.get<uint64_t>();Require(n<=maximum,"self-peer integer exceeds limit");return n;
}
inline void SelfKeys(const Json& value,const std::vector<std::string>& keys) {
  Require(value.is_object() && value.size()==keys.size(),"wrong self-peer fields");
  for(const auto& key:keys)Require(value.contains(key),"missing self-peer field");
}
// Pure admission before network allocation/session mutation. No physical
// loopback/NIC service cost is inferred from this declared functional model.
inline void ValidateSelfServices(const Json& input,const Json& topology,int64_t max_ns) {
  std::map<uint64_t,bool> selves;unsigned count=0;
  const bool enabled=input.contains("self_peer_model");
  if(enabled) {
    Require(input.at("schema_version")=="moe-rdma-transactions/5" && !input.contains("buffer_model"),"self-peer requires v5 without copied buffers");
    Require(input.at("self_peer_model")==Json{{"mode","declared_service_fifo/1"}},"unsupported self-peer model");
  }
  for(const auto& c:input.at("connections")) {
    const bool self=c.contains("self_service");
    Require((c.at("src")==c.at("dst"))==self,"self-peer requires explicit service and matching endpoints");
    selves.emplace(c.at("id"),self);if(!self)continue;++count;
    Require(enabled,"self-peer model is missing");
    SelfKeys(c,{"id","src","dst","plane","source_nic","destination_nic","queue_depth","self_service"});
    const auto owner=SelfInteger(c.at("src"),31),nic=SelfInteger(c.at("source_nic"),1024),plane=SelfInteger(c.at("plane"),15);
    Require(nic>0 && c.at("destination_nic")==nic,"self-peer NIC mismatch");
    const auto gpu=std::find_if(topology.at("nodes").begin(),topology.at("nodes").end(),[&](const auto& n){return n.at("id")==owner && n.at("kind")=="gpu";});
    Require(gpu!=topology.at("nodes").end(),"self-peer owner is not a GPU");
    bool found=false;
    for(const auto& link:topology.at("links"))for(const bool reverse:{false,true}) {
      const auto* side=reverse?"dst":"src";const auto* port=reverse?"dst_port":"src_port";
      if(link.at(side)!=owner || link.at(port)!=nic)continue;
      const auto peer=link.at(reverse?"src":"dst");
      for(const auto& node:topology.at("nodes"))if(node.at("id")==peer && node.contains("plane") && node.at("plane")==plane)found=true;
    }
    Require(found,"self-peer NIC absent or on another plane");
    const auto& service=c.at("self_service");SelfKeys(service,{"apply_ns","complete_ns"});
    const auto apply=SelfInteger(service.at("apply_ns"),max_ns),complete=SelfInteger(service.at("complete_ns"),max_ns);
    Require(apply<=complete,"self-peer completion precedes application");
  }
  Require(enabled==(count>0),"self-peer model requires self connections");
  Require(count<input.at("connections").size(),"self-peer service requires at least one remote connection");
  std::set<uint64_t> self_ops;
  for(const auto& op:input.at("operations"))if(op.value("kind",std::string("remote"))!="local" && op.contains("connection") && selves.at(op.at("connection").get<uint64_t>()))self_ops.insert(op.at("id").get<uint64_t>());
  for(const auto& op:input.at("operations"))for(const auto& d:op.at("after"))
    Require(d.at("event")!="request_acked" || !self_ops.count(d.at("operation")),"self-peer service has no network request ACK");
}
}
