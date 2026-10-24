#pragma once
#include "remote_transactions.hh"

namespace moe_rdma {
// Shared strict admission for probes and sessions; no live memory is inspected.
inline std::vector<RemoteMemory::Registration> MemoryRegistrations(const Json& input) {
  const bool enabled=input.contains("registration_model");
  Require(enabled==input.contains("registrations"),"registration model/list must appear together");
  if(!enabled)return {};
  Require((input.at("schema_version")=="moe-rdma-transactions/4" || input.at("schema_version")=="moe-rdma-transactions/5") &&
          input.at("registration_model")==Json{{"mode","nic_scoped_aliases/1"}},"unsupported registration model");
  const auto& rows=input.at("registrations");Require(rows.is_array() && !rows.empty() && rows.size()<=32,"registration count exceeded");
  auto integer=[](const Json& v,uint64_t maximum) {
    Require(v.is_number_integer() && (v.is_number_unsigned() || v.get<int64_t>()>=0),"invalid registration integer");
    const auto n=v.get<uint64_t>();Require(n<=maximum,"registration integer out of bounds");return n;
  };
  std::set<std::pair<unsigned,unsigned>> nics;
  for(const auto& c:input.at("connections")) {
    nics.emplace(c.at("src"),c.at("source_nic"));nics.emplace(c.at("dst"),c.at("destination_nic"));
  }
  std::vector<RemoteMemory::Registration> out;
  for(const auto& row:rows) {
    Require(row.is_object() && row.size()==8,"wrong registration fields");
    for(const auto* k:{"id","owner","nic","key","window_key","address","bytes","allow_atomic"})Require(row.contains(k),"missing registration field");
    Require(row.at("allow_atomic").is_boolean(),"registration atomic permission must be boolean");
    RemoteMemory::Registration r{integer(row.at("id"),65535),static_cast<unsigned>(integer(row.at("owner"),31)),
      static_cast<unsigned>(integer(row.at("nic"),1024)),static_cast<uint32_t>(integer(row.at("key"),UINT32_MAX)),
      static_cast<uint32_t>(integer(row.at("window_key"),UINT32_MAX)),integer(row.at("address"),UINT64_MAX),
      integer(row.at("bytes"),RemoteMemory::MaxLogicalBytes),row.at("allow_atomic")};
    Require(r.key && r.bytes && nics.count({r.owner,r.nic}),"registration empty key/range or unbound NIC");
    const Json* storage=nullptr;
    for(const auto& w:input.at("windows"))if(w.at("owner")==r.owner && w.at("key")==r.window_key)storage=&w;
    Require(storage,"unknown registration storage");
    const uint64_t start=storage->at("address"),bytes=storage->contains("bytes")?storage->at("bytes").get<uint64_t>():
      MemoryUnhex(storage->at("hex").get<std::string>()).size();
    Require(r.address>=start && r.address-start<=bytes && r.bytes<=bytes-(r.address-start),"registration range exceeds storage");
    Require(!r.atomic || storage->at("allow_atomic").get<bool>(),"registration grants denied atomic permission");
    for(const auto& prior:out)Require(prior.id!=r.id && !(prior.owner==r.owner && prior.nic==r.nic && prior.key==r.key),"repeated registration id or owner/NIC/key");
    out.push_back(r);
  }
  return out;
}
}
