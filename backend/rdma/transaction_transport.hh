#pragma once
#include "persistent_streams.hh"

namespace moe_rdma {
inline void ValidateTransactionTransport(const Json& input,int64_t max_ns) {
  const bool reference=input.contains("dcqcn_reference");
  const auto recovery=input.value("transport_recovery",Json::object());
  const bool selective=recovery.is_object() && recovery.value("mode",std::string())=="selective_repeat/1";
  if(reference || selective) {
    Require(input.at("schema_version")=="moe-rdma-transactions/5","transaction reference/selective transport requires v5");
    Require(!input.contains("buffer_model"),"transaction reference excludes bounded copied payload pools");
  }
  Require(!selective || reference,"transaction selective transport requires dcqcn_reference");
  if(reference)PersistentStreams::ValidateDcqcnReference(input.at("dcqcn_reference"),max_ns);
}
}
