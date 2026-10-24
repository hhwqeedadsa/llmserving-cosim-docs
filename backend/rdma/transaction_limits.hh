#pragma once
#include "fabric.hh"

namespace moe_rdma {
// Lifetime counts, not NIC capacity, WQE depth or calibrated hardware limits.
struct TransactionLimits {
  unsigned connections=8,remote=32,local=16;
  unsigned Nodes() const {return remote+local;}
};
inline TransactionLimits ReadTransactionLimits(const Json& input) {
  TransactionLimits out;
  if(!input.contains("execution_limits"))return out;
  Require(input.at("schema_version")=="moe-rdma-transactions/5","execution limits require transactions/5");
  const auto& c=input.at("execution_limits");
  Require(c.is_object() && c.size()==3,"execution limits require three explicit fields");
  auto integer=[&](const char* key,unsigned low,unsigned high) {
    Require(c.contains(key) && c.at(key).is_number_integer() && c.at(key)>=low && c.at(key)<=high,
            "invalid execution limit");
    return c.at(key).get<unsigned>();
  };
  out.connections=integer("max_connections",1,64);
  out.remote=integer("max_remote_operations",1,1024);
  out.local=integer("max_local_operations",0,256);
  return out;
}
}
