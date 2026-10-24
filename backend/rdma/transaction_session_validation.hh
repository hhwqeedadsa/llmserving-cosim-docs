#pragma once
#include "remote_transactions.hh"
#include "transaction_effect_order.hh"
#include "bounded_buffers.hh"
#include "memory_registrations.hh"
#include "application_window_validation.hh"

namespace moe_rdma {
// Pure admission validation. Never inspect a live byte value/lease to validate
// a future node: those access checks belong to actual admission/publication.
inline uint64_t SessionInteger(const Json& value,uint64_t maximum=UINT64_MAX) {
  Require(value.is_number_integer() && (value.is_number_unsigned() || value.get<int64_t>()>=0),"invalid session integer");
  const auto n=value.get<uint64_t>();Require(n<=maximum,"session integer out of bounds");return n;
}
inline void SessionKeys(const Json& value,const std::vector<std::string>& keys) {
  Require(value.is_object() && value.size()==keys.size(),"wrong session fields");
  for(const auto& key:keys)Require(value.contains(key),"missing session field");
}
inline void ValidateSessionProgram(const Json& input,const Json& topology,int64_t max_ns) {
  const bool explicit_submission=input.contains("submission_model");
  auto root_keys=std::vector<std::string>{"schema_version","connections","windows","operations","local_model","source_model","local_memory_model"};
  if(explicit_submission)root_keys.push_back("submission_model");
  if(input.contains("transport_recovery"))root_keys.push_back("transport_recovery");
  if(input.contains("buffer_model"))root_keys.push_back("buffer_model");
  for(const auto* name:{"registration_model","registrations","application_window_model","application_windows"})if(input.contains(name))root_keys.push_back(name);
  SessionKeys(input,root_keys);
  const bool sparse=input.at("schema_version")=="moe-rdma-transactions/5";
  if(explicit_submission)Require(sparse && input.at("submission_model")==Json{{"mode","explicit_prefix/1"}},"unsupported submission model");
  Require(sparse || input.at("schema_version")=="moe-rdma-transactions/4","session requires transactions/4 or /5");
  Require(input.at("local_model")==Json{{"mode","serial_per_gpu/1"},{"duration_source","declared_ns"}},"unsupported session local model");
  Require(input.at("source_model")==Json{{"capture","registered_window_at_publish/1"},{"release","transaction_completed/1"}},"unsupported session source model");
  Require(input.at("local_memory_model")==Json{{"mode","guarded_copy/1"},{"acquire","admission/1"},{"capture","start/1"},{"commit","completion/1"}},"unsupported session local memory model");
  std::set<uint64_t> gpus,wire_ports;
  for(const auto& node:topology.at("nodes"))if(node.at("kind")=="gpu")gpus.insert(node.at("id").get<uint64_t>());
  const auto& connections=input.at("connections");
  Require(connections.is_array() && !connections.empty() && connections.size()<=8,"session connection budget exceeded");
  std::map<uint64_t,std::pair<uint64_t,uint64_t>> endpoints;
  std::map<uint64_t,unsigned> destination_nics;
  for(const auto& c:connections) {
    SessionKeys(c,{"id","src","dst","plane","request_port","response_port","source_nic","destination_nic","queue_depth"});
    const auto id=SessionInteger(c.at("id"),7),src=SessionInteger(c.at("src"),31),dst=SessionInteger(c.at("dst"),31);
    Require(src!=dst && gpus.count(src) && gpus.count(dst) && endpoints.emplace(id,std::pair{src,dst}).second,"invalid session endpoints/id");
    SessionInteger(c.at("plane"),31);SessionInteger(c.at("source_nic"),1024);SessionInteger(c.at("destination_nic"),1024);
    destination_nics.emplace(id,c.at("destination_nic"));
    const auto depth=SessionInteger(c.at("queue_depth"),32);Require(depth>0 && (depth&(depth-1))==0,"invalid session queue depth");
    for(const auto* key:{"request_port","response_port"}) {
      const auto port=SessionInteger(c.at(key),65535);Require(port>=10000 && wire_ports.insert(port).second,"invalid/repeated session wire port");
    }
  }
  struct Window {uint64_t address,bytes;bool atomic;};
  std::map<std::pair<uint64_t,uint64_t>,Window> windows;
  const auto& ws=input.at("windows");Require(ws.is_array() && !ws.empty() && ws.size()<=16,"session window budget exceeded");
  for(const auto& w:ws) {
    SessionKeys(w,sparse?std::vector<std::string>{"owner","key","address","allow_atomic","bytes","initialization","segments"}:
      std::vector<std::string>{"owner","key","address","allow_atomic","hex"});
    const auto owner=SessionInteger(w.at("owner"),31),key=SessionInteger(w.at("key"),UINT32_MAX),address=SessionInteger(w.at("address"));
    uint64_t size;
    if(sparse) {
      size=SessionInteger(w.at("bytes"),RemoteMemory::MaxLogicalBytes);
      Require(size>0 && w.at("initialization")=="zero_fill/1" && w.at("segments").is_array() && w.at("segments").size()<=32,"invalid sparse window");
      uint64_t total=0;std::vector<std::pair<uint64_t,uint64_t>> used;
      for(const auto& s:w.at("segments")) {
        SessionKeys(s,{"offset","hex"});const auto offset=SessionInteger(s.at("offset"),size-1);
        const auto bytes=MemoryUnhex(s.at("hex").get<std::string>()).size();
        total+=bytes;Require(bytes>0 && bytes<=size-offset && total<=65536,"invalid sparse initialization range/bytes");
        for(const auto& [a,b]:used)Require(offset+bytes<=a || b<=offset,"overlapping sparse initialization");
        used.emplace_back(offset,offset+bytes);
      }
    } else {size=MemoryUnhex(w.at("hex").get<std::string>()).size();Require(size>0 && size<=65536,"invalid dense window size");}
    Require(gpus.count(owner) && key>0 && address<=UINT64_MAX-size && w.at("allow_atomic").is_boolean(),"invalid session window");
    for(const auto& [location,prior]:windows)if(location.first==owner)
      Require(address+size<=prior.address || prior.address+prior.bytes<=address,"overlapping session windows");
    Require(windows.emplace(std::pair{owner,key},Window{address,size,w.at("allow_atomic")}).second,"repeated session window key");
  }
  auto range=[&](uint64_t owner,uint64_t key,uint64_t address,uint64_t bytes) {
    Require(bytes>0 && windows.count({owner,key}),"unknown/empty session memory range");
    const auto& w=windows.at({owner,key});
    Require(address>=w.address && address-w.address<=w.bytes && bytes<=w.bytes-(address-w.address),"session memory range out of bounds");
  };
  const auto registrations=MemoryRegistrations(input);
  const auto& ops=input.at("operations");Require(ops.is_array() && ops.size()<=48,"session node budget exceeded");
  std::map<uint64_t,bool> local;
  std::map<uint64_t,std::set<uint64_t>> ancestors;
  struct Access {uint64_t id,owner,key,address,bytes;bool atomic;};std::vector<Access> writes;
  unsigned local_count=0,remote_count=0;
  for(const auto& op:ops) {
    const auto id=SessionInteger(op.at("id"),65535);Require(!local.count(id),"reused session operation id");
    const auto kind=op.at("kind").get<std::string>();Require(kind=="local" || kind=="remote","invalid session node kind");
    const bool is_local=kind=="local";
    std::vector<std::string> keys={"id","kind","after"};
    if(is_local)keys.insert(keys.end(),{"rank","duration_ns"});
    else {keys.insert(keys.end(),{"connection","verb","key","address"});keys.push_back(op.contains("source")?"source":op.at("verb")=="put"?"hex":"value");}
    if(explicit_submission && !is_local) {
      keys.push_back("aggregate_requests");
      Require(op.at("aggregate_requests").is_boolean(),"aggregate_requests must be boolean");
    }
    if(op.contains("local_wait"))keys.push_back("local_wait");
    if(op.contains("copy")) {Require(is_local,"copy requires local node");keys.push_back("copy");}
    if(op.contains("queue_fence")) {Require(!is_local && !op.contains("local_wait"),"invalid session queue fence");keys.push_back("queue_fence");}
    for(const auto* name:{"application_uses","release_window"})if(op.contains(name))keys.push_back(name);
    SessionKeys(op,keys);
    Require(op.at("after").is_array() && op.at("after").size()<=32,"invalid session dependencies");
    std::set<std::pair<uint64_t,std::string>> deps;std::set<uint64_t> parents;
    for(const auto& d:op.at("after")) {
      SessionKeys(d,{"operation","event"});const auto p=SessionInteger(d.at("operation"),65535);
      const auto e=d.at("event").get<std::string>();
      Require(local.count(p) && (local.at(p)?e=="completed":((explicit_submission && e=="prepared") || e=="applied" || e=="request_acked" || e=="completed")) && deps.emplace(p,e).second,"invalid session dependency target/event");
      parents.insert(p);parents.insert(ancestors.at(p).begin(),ancestors.at(p).end());
    }
    uint64_t owner;
    if(is_local) {owner=SessionInteger(op.at("rank"),31);Require(gpus.count(owner),"session local rank absent");SessionInteger(op.at("duration_ns"),max_ns);++local_count;}
    else {const auto c=SessionInteger(op.at("connection"),7);Require(endpoints.count(c),"unknown session connection");owner=endpoints.at(c).first;++remote_count;}
    std::vector<std::pair<uint64_t,uint64_t>> resets;
    if(op.contains("local_wait")) {
      const auto& waits=op.at("local_wait");Require(waits.is_array() && !waits.empty() && waits.size()<=8,"invalid session local waits");
      std::set<std::pair<uint64_t,uint64_t>> seen;
      for(const auto& w:waits) {
        std::vector<std::string> wk={"key","address","compare","value"};if(w.contains("reset_to"))wk.push_back("reset_to");SessionKeys(w,wk);
        const auto key=SessionInteger(w.at("key"),UINT32_MAX),address=SessionInteger(w.at("address"));SessionInteger(w.at("value"));
        Require(address%8==0 && (w.at("compare")=="eq" || w.at("compare")=="ge") && seen.emplace(key,address).second,"invalid session wait comparison/address");range(owner,key,address,8);
        if(w.contains("reset_to")) {SessionInteger(w.at("reset_to"));resets.emplace_back(key,address);}
      }
    }
    auto separate_resets=[&](uint64_t key,uint64_t address,uint64_t bytes) {
      for(const auto& [rk,ra]:resets)Require(rk!=key || ra+8<=address || address+bytes<=ra,"session source/copy overlaps own reset");
    };
    if(is_local && op.contains("copy")) {
      const auto& cp=op.at("copy");SessionKeys(cp,{"source","target"});const auto& s=cp.at("source");const auto& t=cp.at("target");
      SessionKeys(s,{"key","address","bytes"});SessionKeys(t,{"key","address"});
      const auto bytes=SessionInteger(s.at("bytes"),32768),sa=SessionInteger(s.at("address")),ta=SessionInteger(t.at("address"));
      for(const auto& part:{s,t}) {const auto key=SessionInteger(part.at("key"),UINT32_MAX),address=SessionInteger(part.at("address"));range(owner,key,address,bytes);separate_resets(key,address,bytes);}
      Require(sa+bytes<=ta || ta+bytes<=sa,"session copy source/target overlap");
    }
    if(!is_local) {
      const auto dst=endpoints.at(op.at("connection")).second,key=SessionInteger(op.at("key"),UINT32_MAX),address=SessionInteger(op.at("address"));
      uint64_t storage_key=key;const RemoteMemory::Registration* access=nullptr;
      if(input.contains("registration_model")) {
        for(const auto& r:registrations)if(r.owner==dst && r.nic==destination_nics.at(op.at("connection")) && r.key==key)access=&r;
        Require(access,"unknown session destination NIC/key registration");storage_key=access->window_key;
      }
      const auto verb=op.at("verb").get<std::string>();Require(verb=="put" || verb=="fetch_add","invalid session verb");uint64_t bytes;
      if(op.contains("source")) {
        Require(verb=="put","session source requires put");const auto& s=op.at("source");SessionKeys(s,{"key","address","bytes"});
        const auto sk=SessionInteger(s.at("key"),UINT32_MAX),sa=SessionInteger(s.at("address"));bytes=SessionInteger(s.at("bytes"),32768);range(owner,sk,sa,bytes);separate_resets(sk,sa,bytes);
      } else if(verb=="put") {bytes=MemoryUnhex(op.at("hex").get<std::string>()).size();Require(bytes>0 && bytes<=32768,"session put length exceeded");}
      else {bytes=8;SessionInteger(op.at("value"));Require(windows.count({dst,storage_key}) && windows.at({dst,storage_key}).atomic &&
        (!access || access->atomic) && address%8==0,"session atomic denied/unaligned");}
      range(dst,storage_key,address,bytes);
      if(access)Require(address>=access->address && address-access->address<=access->bytes &&
        bytes<=access->bytes-(address-access->address),"session registration range exceeded");
      for(const auto& w:writes)if(w.owner==dst && address<w.address+w.bytes && w.address<address+bytes)
        Require((verb=="fetch_add" && w.atomic) || parents.count(w.id),"session overlapping remote writes lack causal order");
      writes.push_back({id,dst,key,address,bytes,verb=="fetch_add"});
      if(op.contains("queue_fence")) {
        const auto& fs=op.at("queue_fence");Require(fs.is_array() && !fs.empty() && fs.size()<=8,"invalid session fence budget");std::set<uint64_t> seen;
        for(const auto& item:fs) {const auto c=SessionInteger(item,7);Require(endpoints.count(c) && endpoints.at(c).first==owner && seen.insert(c).second,"invalid session fence connection");}
      }
    }
    Require(local_count<=16 && remote_count<=32,"session kind count budget exceeded");local[id]=is_local;ancestors[id]=std::move(parents);
  }
  ValidateTransactionEffectOrder(input);
  ValidateBoundedBuffers(input);
  ValidateApplicationWindows(input);
}
}
