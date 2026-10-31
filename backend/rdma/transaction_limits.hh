#pragma once
#include "fabric.hh"

namespace moe_rdma {
// Lifetime counts, not NIC capacity, WQE depth or calibrated hardware limits.
struct TransactionLimits {
  unsigned connections=8,remote=32,local=16;
  bool scaleout=false;
  unsigned Nodes() const {return remote+local;}
  unsigned MaxOwner() const {return scaleout?511:31;}
  unsigned Windows() const {return scaleout?128:16;}
  unsigned Registrations() const {return scaleout?256:32;}
  unsigned Waits() const {return scaleout?32:8;}
  unsigned Dependencies() const {return scaleout?128:32;}
  unsigned Decisions() const {return scaleout?1024:256;}
  unsigned LinkTransmissions() const {return scaleout?2000000:200000;}
};
inline TransactionLimits ReadTransactionLimits(const Json& input) {
  TransactionLimits out;
  if(!input.contains("execution_limits"))return out;
  Require(input.at("schema_version")=="moe-rdma-transactions/5","execution limits require transactions/5");
  const auto& c=input.at("execution_limits");
  Require(c.is_object(),"execution limits must be an object");
  out.scaleout=c.contains("profile");
  Require(c.size()==(out.scaleout?4u:3u),"wrong execution limit fields");
  if(out.scaleout)Require(c.at("profile")=="scaleout_ep32/1","unsupported transaction capacity profile");
  auto integer=[&](const char* key,unsigned low,unsigned high) {
    Require(c.contains(key) && c.at(key).is_number_integer() && c.at(key)>=low && c.at(key)<=high,
            "invalid execution limit");
    return c.at(key).get<unsigned>();
  };
  out.connections=integer("max_connections",1,out.scaleout?2048:64);
  out.remote=integer("max_remote_operations",1,out.scaleout?16384:1024);
  out.local=integer("max_local_operations",0,out.scaleout?1024:256);
  return out;
}
}
