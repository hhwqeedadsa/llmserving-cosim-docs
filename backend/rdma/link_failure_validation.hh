#pragma once
#include "fabric.hh"

namespace moe_rdma {
// Validate the whole fault schedule before allocating the native network.
inline Json LinkFailureEvents(const Json& config,const Json& topology,int64_t maximum) {
  if(!config.contains("link_failures"))return Json::array();
  const auto& model=config.at("link_failures");
  Require(model.is_object() && model.size()==2 && model.contains("mode") && model.contains("events") &&
          model.at("mode")=="switch_links/1","unsupported link failure model");
  const auto& events=model.at("events");
  Require(events.is_array() && !events.empty() && events.size()<=8,"require 1..8 switch link failures");
  auto integer=[](const Json& v,uint64_t bound) {
    Require(v.is_number_integer() && (v.is_number_unsigned() || v.get<int64_t>()>=0),"invalid link failure integer");
    auto n=v.get<uint64_t>();Require(n<=bound,"link failure integer exceeds bound");return n;
  };
  Json output=Json::array();std::set<std::pair<unsigned,unsigned>> seen;
  const auto& nodes=topology.at("nodes");
  for(const auto& e:events) {
    Require(e.is_object() && e.size()==4,"wrong link failure event fields");
    for(const auto* k:{"src","dst","time_ns","route_update_delay_ns"})Require(e.contains(k),"missing link failure field");
    const auto a=integer(e.at("src"),1279),b=integer(e.at("dst"),1279);
    const auto time=integer(e.at("time_ns"),maximum-1);
    integer(e.at("route_update_delay_ns"),maximum-1-time);
    Require(a<b && b<nodes.size() && nodes.at(a).at("id")==a && nodes.at(b).at("id")==b &&
      nodes.at(a).at("kind")!="gpu" && nodes.at(b).at("kind")!="gpu" &&
      nodes.at(a).at("plane")==nodes.at(b).at("plane") && seen.emplace(a,b).second,
      "link failures require unique upward switch pairs in one plane");
    const Json* edge=nullptr;
    for(const auto& link:topology.at("links"))if(link.at("src")==a && link.at("dst")==b)edge=&link;
    Require(edge,"link failure must select an actual physical edge");
    Json row=e;row["src_port"]=edge->at("src_port");row["dst_port"]=edge->at("dst_port");output.push_back(row);
  }
  return output;
}
}
