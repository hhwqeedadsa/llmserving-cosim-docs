#pragma once
#include "legacy_execution_validation.hh"

namespace moe_rdma {
inline void ValidateLegacyShared(const Json& input,const Json& topology,const Json& protocol) {
  Require(input.is_object() && input.size()==4 && input.at("schema_version")=="moe-legacy-shared-execution/1",
          "invalid shared legacy schema/fields");
  const auto& groups=input.at("groups");
  Require(groups.is_array() && !groups.empty() && groups.size()<=2,"shared legacy requires one or two groups");
  unsigned streams=0,work=0,operations=0,events=0;uint64_t mandatory=0;
  std::set<std::string> ids;std::set<unsigned> endpoints;
  const auto mtu=protocol.at("payload_bytes").get<uint64_t>();
  Require(mtu>0 && mtu<=65536,"invalid shared legacy payload size");
  for(const auto& group:groups) {
    Require(group.is_object() && group.size()==2 && group.at("id").is_string(),"invalid shared legacy group fields");
    const auto id=group.at("id").get<std::string>();
    Require(!id.empty() && id.size()<=64 && ids.insert(id).second,"duplicate/invalid shared legacy group id");
    const auto& execution=group.at("execution");
    Require(execution.at("transport_recovery")==input.at("transport_recovery") &&
            execution.at("dcqcn_reference")==input.at("dcqcn_reference"),"shared groups must use the declared common transport");
    ValidateLegacyExecution(execution,topology,protocol,200000);
    for(const auto& gpu:execution.at("rank_to_gpu"))
      Require(endpoints.insert(gpu.get<unsigned>()).second,"shared legacy groups overlap physical endpoints");
    streams+=execution.at("limits").at("streams").get<unsigned>();
    work+=execution.at("limits").at("work").get<unsigned>();
    operations+=execution.at("operations").size();events+=execution.at("events").size();
    for(const auto& op:execution.at("operations"))if(op.at(4)!=op.at(5)) {
      mandatory+=(32+op.at(8).get<uint64_t>()+mtu-1)/mtu;
      if(op.at(3)!="data")mandatory+=(36+mtu-1)/mtu;
    }
  }
  Require(streams<=32768 && 20000+streams<=65536 && work<=65536 && operations<=65536 && events<=280000,
          "shared legacy aggregate lifetime budget exceeded");
  Require(input.at("dcqcn_reference").at("max_events")>=mandatory,
          "shared legacy reference events below aggregate mandatory source sends; rejected before Fabric");
}
}
