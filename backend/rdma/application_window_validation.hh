#pragma once
#include "remote_transactions.hh"

namespace moe_rdma {
// Full candidate admission only; no live memory or target metadata is read.
inline std::vector<ApplicationWindows::View> ApplicationWindowViews(const Json& input) {
  const bool enabled=input.contains("application_window_model");
  Require(enabled==input.contains("application_windows"),"application window model/list must appear together");
  if(!enabled) {
    for(const auto& op:input.at("operations"))Require(!op.contains("application_uses") && !op.contains("release_window"),"application window fields require model");
    return {};
  }
  Require((input.at("schema_version")=="moe-rdma-transactions/4" || input.at("schema_version")=="moe-rdma-transactions/5") &&
          input.at("application_window_model")==Json{{"mode","local_views/1"}} && input.contains("registration_model"),"unsupported application window model/registrations");
  const auto& rows=input.at("application_windows");Require(rows.is_array() && !rows.empty() && rows.size()<=32,"application view count exceeded");
  auto integer=[](const Json& v,uint64_t max) {
    Require(v.is_number_integer() && (v.is_number_unsigned() || v.get<int64_t>()>=0),"invalid application window integer");
    const auto n=v.get<uint64_t>();Require(n<=max,"application window integer out of bounds");return n;
  };
  std::vector<ApplicationWindows::View> out;std::set<uint64_t> ids;
  for(const auto& row:rows) {
    Require(row.is_object() && row.size()==5,"invalid application view fields");
    for(const auto* k:{"id","owner","window_key","address","bytes"})Require(row.contains(k),"missing application view field");
    ApplicationWindows::View v{integer(row.at("id"),65535),static_cast<unsigned>(integer(row.at("owner"),31)),
      static_cast<uint32_t>(integer(row.at("window_key"),UINT32_MAX)),integer(row.at("address"),UINT64_MAX),
      integer(row.at("bytes"),RemoteMemory::MaxLogicalBytes),false,{}};
    const Json* backing=nullptr;
    for(const auto& w:input.at("windows"))if(w.at("owner")==v.owner && w.at("key")==v.key)backing=&w;
    Require(backing && ids.insert(v.id).second && v.bytes,"duplicate application view/unknown backing");
    const uint64_t address=backing->at("address"),size=backing->contains("bytes")?backing->at("bytes").get<uint64_t>():MemoryUnhex(backing->at("hex").get<std::string>()).size();
    Require(v.address>=address && v.address-address<=size && v.bytes<=size-(v.address-address),"application view exceeds backing");
    out.push_back(v);
  }
  return out;
}
inline void ValidateApplicationWindows(const Json& input) {
  const auto views=ApplicationWindowViews(input);if(!input.contains("application_window_model"))return;
  std::map<uint64_t,ApplicationWindows::View> lookup;
  for(const auto& v:views)lookup.emplace(v.id,v);
  std::map<uint64_t,unsigned> sources;
  for(const auto& c:input.at("connections"))sources.emplace(c.at("id"),c.at("src"));
  std::map<uint64_t,std::set<uint64_t>> covers,users;std::set<uint64_t> closed;
  for(const auto& op:input.at("operations")) {
    const auto id=op.at("id").get<uint64_t>();std::set<uint64_t> done;
    for(const auto& d:op.at("after")) {
      const auto p=d.at("operation").get<uint64_t>();Require(covers.count(p),"application dependency must name earlier node");
      done.insert(covers.at(p).begin(),covers.at(p).end());if(d.at("event")=="completed")done.insert(p);
    }
    covers.emplace(id,done);
    const auto owner=op.at("kind")=="local"?op.at("rank").get<unsigned>():sources.at(op.at("connection"));
    std::set<uint64_t> uses;
    if(op.contains("application_uses")) {
      const auto& values=op.at("application_uses");Require(values.is_array() && !values.empty() && values.size()<=8,"application use count exceeded");
      for(const auto& value:values) {
        Require(value.is_number_integer() && (value.is_number_unsigned() || value.get<int64_t>()>=0) && value.get<uint64_t>()<=65535,"invalid application use id");
        const auto v=value.get<uint64_t>();
        Require(lookup.count(v) && lookup.at(v).owner==owner && !closed.count(v) && uses.insert(v).second,"application use unknown/foreign/closed/duplicate");
      }
    }
    auto range=[&](const Json& r,uint64_t bytes) {
      const auto key=r.at("key").get<uint32_t>();const auto address=r.at("address").get<uint64_t>();bool found=false;
      for(auto i:uses) {const auto& v=lookup.at(i);if(v.key==key && address>=v.address && address-v.address<=v.bytes && bytes<=v.bytes-(address-v.address))found=true;}
      Require(found,"local memory range missing covering application use");
    };
    if(op.contains("local_wait"))for(const auto& r:op.at("local_wait"))range(r,8);
    if(op.contains("source"))range(op.at("source"),op.at("source").at("bytes"));
    if(op.contains("copy"))for(const auto* k:{"source","target"})range(op.at("copy").at(k),op.at("copy").at("source").at("bytes"));
    if(op.contains("release_window")) {
      const auto& value=op.at("release_window");Require(value.is_number_integer() && (value.is_number_unsigned() || value.get<int64_t>()>=0) && value.get<uint64_t>()<=65535,"invalid release window id");
      const auto v=value.get<uint64_t>();Require(op.at("kind")=="local" && !op.contains("copy") && !op.contains("local_wait") && !op.contains("application_uses"),"release window requires separate pure local node");
      Require(lookup.count(v) && lookup.at(v).owner==owner && !closed.count(v),"application close unknown/foreign/duplicate");
      for(auto user:users[v])Require(done.count(user),"application close requires completed uses");
      closed.insert(v);
    }
    for(auto v:uses)users[v].insert(id);
  }
}
}
