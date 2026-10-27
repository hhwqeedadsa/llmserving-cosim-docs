#include "remote_transactions.hh"
#include "transaction_effect_order.hh"
#include "bounded_buffers.hh"
#include "memory_registrations.hh"
#include "application_window_validation.hh"
#include "transaction_transport.hh"

namespace moe_rdma {
static int64_t MemoryNow(){return ns3::Simulator::Now().GetNanoSeconds();}
std::vector<uint8_t> MemoryUnhex(const std::string& text) {
  Require(text.size()%2==0 && text.size()<=131072,"invalid/big hex memory value");
  auto digit=[](char c)->unsigned {
    if(c>='0' && c<='9')return c-'0';
    if(c>='a' && c<='f')return c-'a'+10;
    throw std::invalid_argument("hex must be lowercase canonical digits");
  };
  std::vector<uint8_t> bytes;bytes.reserve(text.size()/2);
  for(std::size_t i=0;i<text.size();i+=2)bytes.push_back((digit(text[i])<<4)|digit(text[i+1]));
  return bytes;
}
std::string MemoryHex(const std::vector<uint8_t>& bytes) {
  static const char digits[]="0123456789abcdef";std::string text;text.reserve(bytes.size()*2);
  for(auto byte:bytes){text+=digits[byte>>4];text+=digits[byte&15];}return text;
}
RemoteTransactions::RemoteTransactions(Fabric& fabric,const Json& input):
    fabric_(fabric),execution_limits_(ReadTransactionLimits(input)),explicit_limits_(input.contains("execution_limits")),
    transport_(fabric,{2*execution_limits_.connections,2*execution_limits_.remote},input.value("transport_recovery",Json()),input.contains("peer_copy_model")) {
  ValidateTransactionTransport(input,fabric.MaxSimNs());
  if(input.contains("dcqcn_reference"))transport_.ConfigureDcqcnReference(input.at("dcqcn_reference"));
  if(explicit_limits_)memory_.ConfigureRequestLimits(execution_limits_.connections,execution_limits_.remote,execution_limits_.Nodes());
  ValidateBoundedBuffers(input);
  bounded_buffers_=input.contains("buffer_model");
  if(bounded_buffers_) {
    buffer_model_=input.at("buffer_model");
    transport_.ConfigurePayloadPool(buffer_model_.at("transport_bytes_per_source"),buffer_model_.contains("on_source_isolation"));
    if(buffer_model_.contains("target_delivery"))memory_.ConfigureCarrierOncePut();
    else memory_.ConfigureCacheBudget(buffer_model_.at("target_cache_bytes_per_owner"));
  }
  Require(!input.contains("transport_recovery") || input.at("transport_recovery").is_object(),"transport_recovery must be an object");
  ValidateTransactionEffectOrder(input);
  Require(input.at("schema_version")=="moe-rdma-transactions/1" ||
          input.at("schema_version")=="moe-rdma-transactions/2" ||
          input.at("schema_version")=="moe-rdma-transactions/3" ||
          input.at("schema_version")=="moe-rdma-transactions/4" ||
          input.at("schema_version")=="moe-rdma-transactions/5","wrong transaction schema");
  const bool sparse=input.at("schema_version")=="moe-rdma-transactions/5";
  peer_copy_mode_=input.contains("peer_copy_model");
  if(peer_copy_mode_)Require(sparse && input.at("peer_copy_model")==Json{{"mode","topology_intra_host_crossbar/1"}} &&
    !input.contains("application_window_model") && !input.contains("buffer_model"),"unsupported peer copy model combination");
  if(peer_copy_mode_)Require(static_cast<uint64_t>(std::count_if(input.at("operations").begin(),input.at("operations").end(),
    [](const auto& op){return op.value("kind",std::string())=="local" && op.contains("copy") && op.at("copy").at("target").contains("owner");}))<=fabric_.MaxMessages(),"peer flow count exceeds protocol budget");
  explicit_submission_=input.contains("submission_model");
  staged_submission_=input.value("submission_model",Json())==Json{{"mode","staged_prefix/1"}};
  if(explicit_submission_)Require(sparse && (staged_submission_ || input.at("submission_model")==Json{{"mode","explicit_prefix/1"}}),"unsupported submission model");
  if(!explicit_submission_)for(const auto& op:input.at("operations"))
    Require(!op.contains("aggregate_requests"),"aggregate_requests requires explicit submission");
  local_memory_mode_=sparse || input.at("schema_version")=="moe-rdma-transactions/4";
  source_mode_=local_memory_mode_ || input.at("schema_version")=="moe-rdma-transactions/3";
  if(local_memory_mode_) {
    local_memory_model_=input.at("local_memory_model");
    Require(local_memory_model_==Json{{"mode","guarded_copy/1"},{"acquire","admission/1"},
      {"capture","start/1"},{"commit","completion/1"}},"unsupported local memory model");
  }
  if(source_mode_) {
    source_model_=input.at("source_model");
    Require(source_model_==Json{{"capture","registered_window_at_publish/1"},{"release","transaction_completed/1"}},
            "unsupported transaction source model");
  }
  Require(input.at("connections").is_array() && !input.at("connections").empty() && input.at("connections").size()<=execution_limits_.connections,
          "transaction connection budget exceeded");
  for(const auto& c:input.at("connections")) {
    const auto id=c.at("id").get<uint64_t>();
    const auto src=c.at("src").get<unsigned>(),dst=c.at("dst").get<unsigned>();
    Require(id<execution_limits_.connections && !connections_.count(id),"invalid transaction connection id");
    connections_.try_emplace(id,src,dst,c.at("destination_nic"),c.at("queue_depth"),execution_limits_.remote);
    if(c.contains("self_service")) {
      auto& local=connections_.at(id);local.self=true;
      local.self_apply=c.at("self_service").at("apply_ns");local.self_complete=c.at("self_service").at("complete_ns");
      continue;
    }
    transport_.Open(id*2,src,dst,c.at("plane"),c.at("request_port"),c.at("source_nic"),c.at("destination_nic"),64);
    transport_.Open(id*2+1,dst,src,c.at("plane"),c.at("response_port"),c.at("destination_nic"),c.at("source_nic"),64);
    transport_.SetReceiver(id*2,[this,id](auto bytes){
      connections_.at(id).requests.Feed(bytes,[this,id](const auto& frame){Request(id,frame);});
    });
    transport_.SetReceiver(id*2+1,[this,id](auto bytes){
      connections_.at(id).responses.Feed(bytes,[this,id](const auto& frame){Response(id,frame);});
    });
  }
  transport_.ValidateFaultBindings();
  Require(input.at("windows").is_array() && !input.at("windows").empty() && input.at("windows").size()<=16,
          "invalid remote windows");
  for(const auto& w:input.at("windows")) {
    if(sparse) {
      Require(w.at("initialization")=="zero_fill/1","unsupported sparse initialization");
      std::vector<std::pair<uint64_t,std::vector<uint8_t>>> segments;
      for(const auto& s:w.at("segments"))segments.emplace_back(s.at("offset"),MemoryUnhex(s.at("hex")));
      memory_.RegisterSparse(w.at("owner"),w.at("key"),w.at("address"),w.at("allow_atomic"),w.at("bytes"),segments);
    } else memory_.Register({w.at("owner"),w.at("key"),w.at("address"),w.at("allow_atomic"),MemoryUnhex(w.at("hex"))});
  }
  const auto registrations=MemoryRegistrations(input);
  if(input.contains("registration_model")) {
    memory_.ConfigureRegistrations();
    for(const auto& r:registrations)memory_.RegisterAccess(r);
  }
  ValidateApplicationWindows(input);
  application_window_mode_=input.contains("application_window_model");
  for(const auto& v:ApplicationWindowViews(input))application_windows_.Add(v);
  transport_.changed=[this](){
    if(bounded_buffers_)for(const auto& [id,c]:connections_)Pump(id);
    if(changed)changed();
  };
}
void RemoteTransactions::CheckApplicationUse(const Json& input) const {
  if(!application_window_mode_)return;
  const auto owner=LocalOwner(input);
  for(const auto& value:input.value("application_uses",Json::array())) {
    const auto& v=application_windows_.Get(value.get<uint64_t>());
    Require(v.owner==owner && !v.closed,"application use requires owned open view");
  }
}
void RemoteTransactions::AcquireApplicationUse(const Json& input) {
  if(!application_window_mode_)return;
  const auto ids=input.value("application_uses",std::vector<uint64_t>{});
  application_windows_.Acquire(input.at("id"),LocalOwner(input),ids);
  if(!ids.empty())application_events_.push_back({{"order",memory_order_++},{"time_ns",MemoryNow()},
    {"operation",input.at("id")},{"kind","acquire"},{"views",ids}});
}
void RemoteTransactions::ReleaseApplicationUse(uint64_t id) {
  if(!application_window_mode_)return;
  const auto ids=application_windows_.Release(id);
  if(!ids.empty())application_events_.push_back({{"order",memory_order_++},{"time_ns",MemoryNow()},
    {"operation",id},{"kind","release"},{"views",ids}});
}
unsigned RemoteTransactions::LocalOwner(const Json& input) const {
  if(input.value("kind",std::string())=="local")return input.at("rank").get<unsigned>();
  return connections_.at(input.at("connection")).src;
}
Json RemoteTransactions::LocalWaits(const Json& input) const {
  Json out=Json::array();
  CheckApplicationUse(input);
  if(!input.contains("local_wait"))return out;
  const auto owner=LocalOwner(input);
  const auto& waits=input.at("local_wait");
  Require(waits.is_array() && !waits.empty() && waits.size()<=8,"invalid local wait budget");
  std::set<std::pair<uint32_t,uint64_t>> seen;
  for(const auto& condition:waits) {
    Require(condition.is_object() && condition.size()==(condition.contains("reset_to")?5u:4u),"invalid local wait fields");
    auto integer=[&](const char* name,uint64_t maximum=UINT64_MAX) {
      const auto& value=condition.at(name);
      Require(value.is_number_integer() && (value.is_number_unsigned() || value.get<int64_t>()>=0),"invalid local condition integer");
      const auto n=value.get<uint64_t>();Require(n<=maximum,"local condition integer out of bounds");return n;
    };
    const auto key=static_cast<uint32_t>(integer("key",UINT32_MAX));
    Require(key>0,"local condition key must be nonzero");
    const auto address=integer("address"),expected=integer("value");
    if(condition.contains("reset_to"))integer("reset_to");
    const auto compare=condition.at("compare").get<std::string>();
    Require((compare=="eq" || compare=="ge") && seen.emplace(key,address).second,"invalid/repeated local wait");
    const auto value=memory_.Load64(owner,key,address);
    Json row=condition;row["owner"]=owner;row["observed_value"]=value;
    row["matched"]=compare=="eq"?value==expected:value>=expected;out.push_back(row);
  }
  return out;
}
bool RemoteTransactions::LocalReady(const Json& input) const {
  for(const auto& condition:LocalWaits(input))if(!condition.at("matched").get<bool>())return false;
  return true;
}
void RemoteTransactions::ConsumeConditions(const Json& input,const Json& observations) {
  if(observations.empty())return;
  const auto owner=LocalOwner(input);
  for(const auto& condition:observations)Require(condition.at("matched"),"consume before local conditions satisfied");
  // Precheck every reset before the first write, including outstanding readers.
  for(const auto& condition:observations)if(condition.contains("reset_to"))
    memory_.CheckWritable(owner,condition.at("key"),condition.at("address"),8);
  for(const auto& condition:observations)if(condition.contains("reset_to"))
    memory_.Store64(owner,condition.at("key"),condition.at("address"),condition.at("reset_to"));
  local_events_.push_back({{"order",memory_order_++},{"time_ns",MemoryNow()},{"operation",input.at("id")},
    {"owner",owner},{"conditions",observations}});
}
void RemoteTransactions::AdmitLocal(const Json& input) {
  Require(input.at("kind")=="local","local admission requires local kind");
  CheckApplicationUse(input);
  if(input.contains("copy")) {
    const auto id=input.at("id").get<uint64_t>();auto copy=Copy(input);
    Require(!local_copies_.count(id) && local_copies_.size()<execution_limits_.local,"invalid local copy identity/budget");
    // All potential conflicts precede condition consumption and acquisition.
    memory_.CheckRead(copy.source);
    memory_.CheckWritable(copy.target.owner,copy.target.key,copy.target.address,copy.target.bytes);
    ConsumeConditions(input,LocalWaits(input));
    memory_.AcquireRead(65536+id,copy.source);memory_.AcquireWrite(id,copy.target);
    local_copies_.emplace(id,copy);LocalMemoryEvent(id,"acquired",copy);AcquireApplicationUse(input);return;
  }
  ConsumeConditions(input,LocalWaits(input));
  AcquireApplicationUse(input);
}
RemoteTransactions::LocalCopy RemoteTransactions::Copy(const Json& input) const {
  Require(local_memory_mode_ && input.at("kind")=="local","copy requires v4 local node");
  const auto owner=input.at("rank").get<unsigned>();const auto& copy=input.at("copy");
  Require(copy.is_object() && copy.size()==2 && copy.contains("source") && copy.contains("target"),"invalid local copy fields");
  auto range=[&](const Json& value,bool source,uint64_t bytes=0) {
    const bool peer=!source && value.contains("owner");
    Require(value.is_object() && value.size()==(source?3u:peer?3u:2u),"invalid local copy range fields");
    auto integer=[&](const char* key,uint64_t maximum) {
      const auto& v=value.at(key);
      Require(v.is_number_integer() && (v.is_number_unsigned() || v.get<int64_t>()>=0),"invalid local copy integer");
      const auto n=v.get<uint64_t>();Require(n<=maximum,"local copy integer out of bounds");return n;
    };
    const auto target_owner=peer?static_cast<unsigned>(integer("owner",31)):owner;
    if(peer)Require(peer_copy_mode_ && input.at("duration_ns")==0 && fabric_.IntraHostPeer(owner,target_owner),
      "peer copy requires zero declared duration and enabled same-server crossbar");
    RemoteMemory::ReadLease result{target_owner,static_cast<uint32_t>(integer("key",UINT32_MAX)),integer("address",UINT64_MAX),source?integer("bytes",32768):bytes};
    memory_.CheckRange(result);return result;
  };
  LocalCopy result;result.source=range(copy.at("source"),true);result.target=range(copy.at("target"),false,result.source.bytes);
  const auto& a=result.source;const auto& b=result.target;
  Require(a.owner!=b.owner || a.address+a.bytes<=b.address || b.address+b.bytes<=a.address,"local copy source/target overlap");
  for(const auto& w:LocalWaits(input))if(w.contains("reset_to")) {
    const auto address=w.at("address").get<uint64_t>();
    Require((address+8<=a.address || a.address+a.bytes<=address) &&
            (b.owner!=owner || address+8<=b.address || b.address+b.bytes<=address),"local copy overlaps own condition reset");
  }
  return result;
}
void RemoteTransactions::ValidateLocalMemory(const Json& input) const {if(input.contains("copy"))Copy(input);}
void RemoteTransactions::ExecuteLocal(const Json& input,Fabric::Done completed) {
  StartLocal(input);
  auto finish=[this,input,completed=std::move(completed)](){CompleteLocal(input);completed();};
  if(input.contains("copy") && input.at("copy").at("target").contains("owner")) {
    const auto& copy=local_copies_.at(input.at("id").get<uint64_t>());
    fabric_.SendPeerCopy(input.at("id"),copy.source.owner,copy.target.owner,copy.source.bytes,std::move(finish));
  } else ns3::Simulator::Schedule(ns3::NanoSeconds(input.at("duration_ns").get<int64_t>()),std::move(finish));
}
void RemoteTransactions::LocalMemoryEvent(uint64_t id,const char* kind,const LocalCopy& copy,const std::vector<uint8_t>* bytes) {
  auto range=[](const auto& r){return Json{{"owner",r.owner},{"key",r.key},{"address",r.address},{"bytes",r.bytes}};};
  Json row={{"order",memory_order_++},{"time_ns",MemoryNow()},{"operation",id},{"kind",kind},
    {"source",range(copy.source)},{"target",range(copy.target)}};
  if(bytes)row["hex"]=MemoryHex(*bytes);
  local_memory_events_.push_back(row);
}
void RemoteTransactions::StartLocal(const Json& input) {
  if(!input.contains("copy"))return;
  const auto id=input.at("id").get<uint64_t>();auto& copy=local_copies_.at(id);
  Require(!copy.started && !copy.completed,"duplicate local copy start");
  const auto& s=copy.source;copy.captured=memory_.ReadBytes(s.owner,s.key,s.address,s.bytes);copy.started=true;
  if(bounded_buffers_) {
    Require(copy.captured.size()<=buffer_model_.at("local_copy_bytes_per_owner").get<uint64_t>(),"local copy pool capacity exceeded");
    local_copy_peaks_[s.owner]=std::max(local_copy_peaks_[s.owner],uint64_t(copy.captured.size()));
  }
  LocalMemoryEvent(id,"captured",copy,&copy.captured);
}
void RemoteTransactions::CompleteLocal(const Json& input) {
  if(input.contains("release_window")) {
    Require(application_window_mode_,"release window requires model");
    const auto id=input.at("release_window").get<uint64_t>();
    application_windows_.Close(id,input.at("rank"));const auto& v=application_windows_.Get(id);
    application_events_.push_back({{"order",memory_order_++},{"time_ns",MemoryNow()},{"operation",input.at("id")},
      {"kind","close"},{"view",id},{"owner",v.owner},{"window_key",v.key},
      {"remaining_application_refs",application_windows_.References(v.owner,v.key)}});
  }
  if(!input.contains("copy")){ReleaseApplicationUse(input.at("id"));return;}
  const auto id=input.at("id").get<uint64_t>();auto& copy=local_copies_.at(id);
  Require(copy.started && !copy.completed,"invalid local copy completion");
  memory_.CommitWrite(id,copy.captured);LocalMemoryEvent(id,"committed",copy,&copy.captured);
  memory_.ReleaseRead(65536+id);memory_.ReleaseWrite(id);copy.completed=true;
  LocalMemoryEvent(id,"released",copy);
  if(bounded_buffers_)std::vector<uint8_t>().swap(copy.captured);
  ReleaseApplicationUse(id);
}
RemoteMemory::ReadLease RemoteTransactions::Source(const Json& input) const {
  Require(source_mode_ && input.at("verb")=="put" && !input.contains("hex"),"source requires v3 put without hex");
  const auto& s=input.at("source");
  Require(s.is_object() && s.size()==3,"invalid source fields");
  auto integer=[&](const char* key,uint64_t maximum) {
    const auto& v=s.at(key);
    Require(v.is_number_integer() && (v.is_number_unsigned() || v.get<int64_t>()>=0),"invalid source integer");
    const auto n=v.get<uint64_t>();Require(n<=maximum,"source integer out of bounds");return n;
  };
  const auto owner=connections_.at(input.at("connection")).src;
  RemoteMemory::ReadLease range{owner,static_cast<uint32_t>(integer("key",UINT32_MAX)),integer("address",UINT64_MAX),integer("bytes",32768)};
  memory_.CheckRead(range);
  // Consuming a counter inside this same source would change the data before
  // publication. Require separate application steps instead of picking an order.
  for(const auto& w:LocalWaits(input))if(w.contains("reset_to")) {
    const auto address=w.at("address").get<uint64_t>();
    Require(address+8<=range.address || range.address+range.bytes<=address,"source overlaps own condition reset");
  }
  return range;
}
void RemoteTransactions::ValidateSource(const Json& input) const {if(input.contains("source"))Source(input);}
void RemoteTransactions::SourceEvent(uint64_t id,const char* kind,RemoteMemory::ReadLease s,const std::vector<uint8_t>* bytes) {
  Json row={{"order",memory_order_++},{"time_ns",MemoryNow()},{"operation",id},{"kind",kind},
    {"owner",s.owner},{"key",s.key},{"address",s.address},{"bytes",s.bytes}};
  if(bytes)row["hex"]=MemoryHex(*bytes);
  source_events_.push_back(row);
}
bool RemoteTransactions::QueueFenceReady(const Json& input) {
  if(!input.contains("queue_fence"))return true;
  const auto id=std::to_string(input.at("id").get<uint64_t>());
  const auto owner=connections_.at(input.at("connection")).src;
  Require(!input.contains("local_wait"),"queue fence and local wait require separate application steps");
  if(!queue_fences_.contains(id)) {
    const auto& selected=input.at("queue_fence");
    Require(selected.is_array() && !selected.empty() && selected.size()<=execution_limits_.connections,"invalid queue fence budget");
    Json targets=Json::array();std::set<uint64_t> seen;
    for(const auto& value:selected) {
      const auto connection=value.get<uint64_t>();
      Require(seen.insert(connection).second && connections_.count(connection) &&
              connections_.at(connection).src==owner,"queue fence must select unique source-owned connections");
      const auto& c=connections_.at(connection);
      targets.push_back({{"connection",connection},{"target",c.queue.FlushTarget()},{"covered_operations",c.operations}});
    }
    queue_fences_[id]={{"operation",input.at("id")},{"owner",owner},{"captured_ns",MemoryNow()},
                       {"completed_ns",-1},{"targets",targets}};
  }
  auto& fence=queue_fences_[id];
  if(fence.at("completed_ns").get<int64_t>()>=0)return true;
  for(const auto& target:fence.at("targets"))
    if(!connections_.at(target.at("connection")).queue.FlushComplete(target.at("target")))return false;
  fence["completed_ns"]=MemoryNow();return true;
}
void RemoteTransactions::Submit(const Json& input) {
  const auto id=input.at("id").get<uint64_t>(),connection=input.at("connection").get<uint64_t>();
  Require(operations_.size()<execution_limits_.remote && !operations_.count(id) && connections_.count(connection),"invalid transaction work");
  CheckApplicationUse(input);
  auto& c=connections_.at(connection);
  Operation op;
  if(explicit_submission_) {
    Require(input.at("aggregate_requests").is_boolean(),"aggregate_requests must be boolean");
    op.aggregate=input.at("aggregate_requests").get<bool>();
  }
  op.id=id;op.connection=connection;op.ready=MemoryNow();
  op.request.sequence=c.operations.size();op.request.address=input.at("address");op.request.key=input.at("key");
  if(input.at("verb")=="put") {
    op.request.verb=MemoryFrame::Put;
    if(input.contains("source")) {
      op.source=Source(input);op.has_source=true;
      if(!bounded_buffers_)op.request.body.resize(op.source.bytes); // legacy length placeholder
    } else op.request.body=MemoryUnhex(input.at("hex"));
  }
  else {
    Require(input.at("verb")=="fetch_add","unsupported memory transaction verb");
    op.request.verb=MemoryFrame::FetchAdd;op.request.body=MemoryInteger(input.at("value"));
  }
  if(!bounded_buffers_)EncodeMemoryFrame(op.request);
  const auto observations=LocalWaits(input);
  for(const auto& condition:observations)Require(condition.at("matched"),"submit before target-local wait satisfied");
  if(input.contains("queue_fence")) {
    const auto name=std::to_string(id);
    Require(queue_fences_.contains(name) && queue_fences_.at(name).at("completed_ns").get<int64_t>()>=0,
            "submit before captured queue fence completed");
  }
  op.ticket=c.queue.Reserve(id,id);
  // Check all conditions before any consume, using only this GPU's received
  // memory. Source operation truth never supplies a value to the predicates.
  ConsumeConditions(input,observations);
  if(op.has_source) {memory_.AcquireRead(id,op.source);SourceEvent(id,"acquired",op.source);}
  AcquireApplicationUse(input);
  c.operations.push_back(id);operations_.emplace(id,std::move(op));dirty_progress_.insert(id);Pump(connection);
}
void RemoteTransactions::Pump(uint64_t connection) {
  if(staged_submission_) {PumpStaged(connection);return;}
  if(explicit_submission_) {PumpExplicit(connection);return;}
  auto& c=connections_.at(connection);
  for(const auto id:c.operations) {
    auto& op=operations_.at(id);
    if(op.published>=0 || !c.queue.CanPrepare(op.ticket))continue;
    if(bounded_buffers_ && !transport_.CanSubmitPayload(connection*2,32+op.source.bytes))break;
    if(op.has_source) {
      const auto& s=op.source;
      op.request.body=memory_.ReadBytes(s.owner,s.key,s.address,s.bytes);
      SourceEvent(id,"captured",s,&op.request.body);
      if(bounded_buffers_)source_scratch_peaks_[s.owner]=std::max(source_scratch_peaks_[s.owner],uint64_t(op.request.body.size()));
    }
    c.queue.Prepare(op.ticket);c.queue.PublishThrough(op.ticket+1);
    const auto work=c.queue.ConsumePublished();Require(work && work->work_id==id,"transaction queue consumption mismatch");
    op.published=MemoryNow();dirty_progress_.insert(id);
    if(c.self) {EnqueueSelf(op);continue;}
    transport_.SubmitPayload(connection*2,transport_work_++,EncodeMemoryFrame(op.request),[this,id,connection](){
      auto& w=operations_.at(id);Require(w.request_ack<0,"duplicate request ACK callback");
      w.request_ack=MemoryNow();dirty_progress_.insert(id);Retire(connection);
    });
    if(bounded_buffers_)std::vector<uint8_t>().swap(op.request.body);
  }
}
// Preparation handles descriptors only; source bytes remain protected from
// admission and are captured when the logical prefix becomes NIC-consumable.
void RemoteTransactions::PumpExplicit(uint64_t connection) {
  auto& c=connections_.at(connection);
  bool prepared=false;
  for(const auto id:c.operations) {
    auto& op=operations_.at(id);
    if(op.prepared>=0 || !c.queue.CanPrepare(op.ticket))continue;
    c.queue.Prepare(op.ticket);op.prepared=MemoryNow();dirty_progress_.insert(id);prepared=true;
    if(!op.aggregate) {
      c.queue.PublishThrough(op.ticket+1);
      publications_.push_back({{"connection",connection},{"exclusive",op.ticket+1},
        {"operation",id},{"time_ns",MemoryNow()}});
      while(const auto work=c.queue.ConsumePublished())Send(operations_.at(work->work_id));
    }
  }
  if(prepared && changed)ns3::Simulator::ScheduleNow(changed);
}
void RemoteTransactions::ValidateStages(const Json& prepare,const Json& publish) const {
  Require(staged_submission_ || (prepare.empty() && publish.empty()),"provider stages require staged_prefix/1");
  for(unsigned k=0;k<2;++k) {
    const auto& rows=k?publish:prepare;std::set<uint64_t> seen;
    Require(rows.is_array() && rows.size()<=execution_limits_.remote,"invalid provider stage control count");
    for(const auto& row:rows) {
      Require(row.is_number_integer() && (row.is_number_unsigned() || row.get<int64_t>()>=0),"invalid provider stage identity");
      const auto id=row.get<uint64_t>();
      Require(operations_.count(id) && seen.insert(id).second,"unknown or duplicate provider stage identity");
      const auto& op=operations_.at(id);
      Require(k?(!op.aggregate && op.prepared>=0 && !op.publish_requested):!op.prepare_requested,
              "provider stage repeated, early or aggregated publication");
    }
  }
}
void RemoteTransactions::ApplyStages(const Json& prepare,const Json& publish) {
  // The session has already validated the whole command and all callbacks.
  std::set<uint64_t> connections;
  for(const auto& row:prepare) {
    auto& op=operations_.at(row.get<uint64_t>());op.prepare_requested=true;connections.insert(op.connection);
  }
  for(const auto connection:connections)PumpStaged(connection);
  for(const auto& row:publish) {
    auto& op=operations_.at(row.get<uint64_t>());op.publish_requested=true;
    auto& c=connections_.at(op.connection);const auto previous=c.queue.Snapshot().published;
    const auto exclusive=std::max(previous,op.ticket+1);
    c.queue.PublishThrough(exclusive);
    publications_.push_back({{"connection",op.connection},{"operation",op.id},{"requested",op.ticket+1},
      {"previous",previous},{"exclusive",exclusive},{"time_ns",MemoryNow()}});
    while(const auto work=c.queue.ConsumePublished())Send(operations_.at(work->work_id));
  }
  if((!prepare.empty() || !publish.empty()) && changed)ns3::Simulator::ScheduleNow(changed);
}
void RemoteTransactions::PumpStaged(uint64_t connection) {
  auto& c=connections_.at(connection);bool changed_state=false;
  for(const auto id:c.operations) {
    auto& op=operations_.at(id);
    if(!op.prepare_requested || op.descriptor_ready>=0 || !c.queue.CanPrepare(op.ticket))continue;
    c.queue.Prepare(op.ticket);op.descriptor_ready=MemoryNow();dirty_progress_.insert(id);changed_state=true;
  }
  const auto prefix=c.queue.Snapshot().prepared_prefix;
  for(const auto id:c.operations) {
    auto& op=operations_.at(id);
    // Ordered ready CAS cannot finish beyond an earlier unready producer.
    if(op.descriptor_ready<0 || op.prepared>=0 || op.ticket>=prefix)continue;
    op.prepared=MemoryNow();dirty_progress_.insert(id);changed_state=true;
  }
  if(changed_state && changed)ns3::Simulator::ScheduleNow(changed);
}
void RemoteTransactions::Send(Operation& op) {
  Require(op.prepared>=0 && op.published<0,"invalid transaction publication");
  if(op.has_source) {
    const auto& s=op.source;
    op.request.body=memory_.ReadBytes(s.owner,s.key,s.address,s.bytes);
    SourceEvent(op.id,"captured",s,&op.request.body);
  }
  op.published=MemoryNow();dirty_progress_.insert(op.id);const auto id=op.id,connection=op.connection;
  if(connections_.at(connection).self) {EnqueueSelf(op);return;}
  transport_.SubmitPayload(connection*2,transport_work_++,EncodeMemoryFrame(op.request),[this,id,connection](){
    auto& w=operations_.at(id);Require(w.request_ack<0,"duplicate request ACK callback");
    w.request_ack=MemoryNow();dirty_progress_.insert(id);Retire(connection);
  });
}
void RemoteTransactions::EnqueueSelf(Operation& op) {
  auto& c=connections_.at(op.connection);
  const auto begin=std::max(MemoryNow(),c.self_available);const auto id=op.id;
  c.self_available=begin+c.self_complete;
  ns3::Simulator::Schedule(ns3::NanoSeconds(begin-MemoryNow()),[this,id]() {
    auto& operation=operations_.at(id);Require(operation.self_started<0,"duplicate self service start");
    operation.self_started=MemoryNow();dirty_progress_.insert(id);
  });
  // The source bytes were captured at publication under their existing lease.
  // No physical packet, fake ACK or GIN wire frame is generated for self.
  const auto frame=op.request;const auto connection=op.connection;
  ns3::Simulator::Schedule(ns3::NanoSeconds(begin+c.self_apply-MemoryNow()),[this,connection,frame]() {Request(connection,frame);});
  ns3::Simulator::Schedule(ns3::NanoSeconds(begin+c.self_complete-MemoryNow()),[this,id,connection]() {
    auto& operation=operations_.at(id);Require(operation.applied>=0 && operation.self_finished<0,"invalid self service completion");
    operation.self_finished=MemoryNow();dirty_progress_.insert(id);Retire(connection);
  });
}
void RemoteTransactions::Request(uint64_t connection,const MemoryFrame& frame) {
  auto& c=connections_.at(connection);
  // All authority, address, value and duplicate decisions come from actual bytes
  // and target state. Source operation metadata is used below only to report the
  // application event, not to decide what to write or return.
  const auto result=memory_.ApplyOnNic(connection,c.dst,c.destination_nic,frame);
  target_events_.push_back({{"order",memory_order_++},{"time_ns",MemoryNow()},{"connection",connection},{"sequence",frame.sequence},
    {"verb",frame.verb},{"key",frame.key},{"address",frame.address},{"body_hex",MemoryHex(frame.body)},
    {"duplicate",result.duplicate},{"old_value",result.old_value}});
  if(memory_.NicScoped())target_events_.back()["destination_nic"]=c.destination_nic;
  if(c.self)target_events_.back()["delivery"]="declared_self_service/1";
  Require(frame.sequence<c.operations.size(),"target request has no submitted application observer");
  auto& op=operations_.at(c.operations.at(frame.sequence));
  if(result.duplicate)++duplicates_;
  else {Require(op.applied<0,"duplicate target application");op.applied=MemoryNow();dirty_progress_.insert(op.id);}
  if(c.self && result.atomic)op.returned_value=result.old_value;
  if(result.atomic && !c.self) {
    MemoryFrame reply;reply.verb=MemoryFrame::Reply;reply.sequence=frame.sequence;reply.body=MemoryInteger(result.old_value);
    transport_.SubmitPayload(connection*2+1,transport_work_++,EncodeMemoryFrame(reply),{});
  }
  if(changed)ns3::Simulator::ScheduleNow(changed);
}
void RemoteTransactions::Response(uint64_t connection,const MemoryFrame& frame) {
  auto& c=connections_.at(connection);
  Require(frame.verb==MemoryFrame::Reply && frame.sequence<c.operations.size(),"invalid transaction response");
  auto& op=operations_.at(c.operations.at(frame.sequence));
  Require(op.request.verb==MemoryFrame::FetchAdd && op.published>=0,"response for non-atomic/unpublished work");
  const auto value=MemoryRead(frame.body.data(),8);
  if(op.response>=0) {Require(value==op.returned_value,"conflicting duplicate response");return;}
  op.returned_value=value;op.response=MemoryNow();dirty_progress_.insert(op.id);Retire(connection);
}
void RemoteTransactions::Retire(uint64_t connection) {
  auto& c=connections_.at(connection);
  for(const auto id:c.operations) {
    auto& op=operations_.at(id);
    if(op.completed>=0)continue;
    if(c.self?op.self_finished<0:(op.request_ack<0 || (op.request.verb==MemoryFrame::FetchAdd && op.response<0)))break;
    op.completed=MemoryNow();dirty_progress_.insert(id);c.queue.CompleteThrough(op.ticket+1);
    if(op.has_source) {memory_.ReleaseRead(id);SourceEvent(id,"released",op.source);}
    ReleaseApplicationUse(id);
  }
  Pump(connection);if(changed)ns3::Simulator::ScheduleNow(changed);
}
bool RemoteTransactions::Occurred(uint64_t id,const std::string& event) const {
  const auto it=operations_.find(id);if(it==operations_.end())return false;
  if(explicit_submission_ && event=="prepared")return it->second.prepared>=0;
  if(event=="applied")return it->second.applied>=0;
  if(event=="request_acked")return it->second.request_ack>=0;
  Require(event=="completed","unsupported transaction event");return it->second.completed>=0;
}
bool RemoteTransactions::Drained() const {
  if(!memory_.ReadLeases().empty() || !memory_.WriteLeases().empty() || !application_windows_.Drained())return false;
  for(const auto& item:connections_)if(!item.second.queue.Drained() || !item.second.requests.Empty() || !item.second.responses.Empty())return false;
  return transport_.Drained();
}
void RemoteTransactions::Close() {
  Require(Drained(),"transaction close before drain");
  for(auto& item:connections_)item.second.queue.Close();
  transport_.Close();
}
Json RemoteTransactions::ProgressRow(const Operation& op) const {
    Json value={{"id",op.id},{"connection",op.connection},{"sequence",op.request.sequence},{"ticket",op.ticket},
      {"verb",op.request.verb==MemoryFrame::Put?"put":"fetch_add"},{"ready_ns",op.ready},{"published_ns",op.published},
      {"applied_ns",op.applied},{"request_acked_ns",op.request_ack},{"response_ns",op.response},{"completed_ns",op.completed}};
    if(connections_.at(op.connection).self) {
      value["delivery"]="declared_self_service/1";value["self_started_ns"]=op.self_started;value["self_completed_ns"]=op.self_finished;
    }
    if(op.request.verb==MemoryFrame::FetchAdd && (op.response>=0 || op.self_finished>=0))value["returned_value"]=op.returned_value;
    if(explicit_submission_)value["prepared_ns"]=op.prepared;
    if(staged_submission_)value["descriptor_ready_ns"]=op.descriptor_ready;
    return value;
}
Json RemoteTransactions::OperationProgress() const {
  Json rows=Json::array();
  for(const auto& [id,op]:operations_)rows.push_back(ProgressRow(op));
  return rows;
}
Json RemoteTransactions::TakeOperationProgressUpdates() {
  Json rows=Json::array();
  for(const auto id:dirty_progress_)rows.push_back(ProgressRow(operations_.at(id)));
#ifdef MOE_VERIFY_PROGRESS_UPDATES
  // Small-case development oracle only: derive changes independently from
  // all current row values, rather than trusting the dirty marks being tested.
  Json expected=Json::array();
  for(const auto& row:OperationProgress()) {
    const auto id=std::to_string(row.at("id").get<uint64_t>());
    if(!progress_shadow_.contains(id) || progress_shadow_.at(id)!=row)expected.push_back(row);
    progress_shadow_[id]=row;
  }
  Require(rows==expected,"incremental operation progress differs from full snapshot changes");
#endif
  dirty_progress_.clear();
  return rows;
}
Json RemoteTransactions::Report() const {
  Json out={{"schema_version","moe-rdma-transaction-result/1"},{"operations",OperationProgress()},{"windows",Json::array()},
    {"connections",Json::array()},{"duplicate_requests",duplicates_},{"gpu_visibility_modeled",false},
    {"hardware_calibrated",false},{"roce_wire_equivalent",false},
    {"scope","MRT1 functional addressed put/fetch-add with actual native byte request/response transport; not a GIN timing baseline"}};
  if(std::any_of(connections_.begin(),connections_.end(),[](const auto& c){return c.second.self;})) {
    out["self_peer_model"]={{"mode","declared_service_fifo/1"},{"hardware_calibrated",false},{"physical_loopback_modeled",false}};
    out["scope"]="Mixed actual MRT1/native remote transport and explicit functional self service; not a GIN timing baseline";
  }
  if(explicit_limits_)out["execution_limits"]={{"max_connections",execution_limits_.connections},
    {"max_remote_operations",execution_limits_.remote},{"max_local_operations",execution_limits_.local}};
  for(const auto& w:memory_.Windows()) {
    Json row={{"owner",w.owner},{"key",w.key},{"address",w.address},{"allow_atomic",w.atomic}};
    if(w.logical_bytes) {
      row["bytes"]=w.logical_bytes;row["initialization"]="zero_fill/1";row["pages"]=Json::array();
      row["resident_bytes"]=w.pages.size()*RemoteMemory::PageBytes;
      for(const auto& [page,bytes]:w.pages) {
        const auto offset=page*RemoteMemory::PageBytes,length=std::min(RemoteMemory::PageBytes,w.logical_bytes-offset);
        row["pages"].push_back({{"offset",offset},{"hex",MemoryHex({bytes.begin(),bytes.begin()+length})}});
      }
    } else row["hex"]=MemoryHex(w.bytes);
    out["windows"].push_back(row);
  }
  if(memory_.NicScoped()) {
    out["registration_model"]={{"mode","nic_scoped_aliases/1"}};out["registrations"]=Json::array();
    for(const auto& r:memory_.Registrations())out["registrations"].push_back({{"id",r.id},{"owner",r.owner},{"nic",r.nic},
      {"key",r.key},{"window_key",r.window_key},{"address",r.address},{"bytes",r.bytes},{"allow_atomic",r.atomic}});
  }
  if(application_window_mode_) {
    Json views=Json::array(),domains=Json::array();std::set<std::pair<unsigned,uint32_t>> backing;
    for(const auto& [id,v]:application_windows_.Views()) {
      views.push_back({{"id",id},{"owner",v.owner},{"window_key",v.key},{"address",v.address},{"bytes",v.bytes},
        {"closed",v.closed},{"active_operations",v.users}});backing.emplace(v.owner,v.key);
    }
    for(const auto& [owner,key]:backing) {
      const auto refs=application_windows_.References(owner,key);Json aliases=Json::array();
      for(const auto& r:memory_.Registrations())if(r.owner==owner && r.window_key==key)aliases.push_back(r.id);
      domains.push_back({{"owner",owner},{"window_key",key},{"application_refs",refs},{"registration_ids",aliases},
        {"state",refs?"application_refs_live":"no_application_refs_provider_pending"}});
    }
    out["application_windows"]={{"mode","local_views/1"},{"views",views},{"backing_domains",domains},{"events",application_events_},
      {"provider_retirement_modeled",false},{"scope","declared local application views only; static NIC permissions remain active; not public deregistration, MR/DMA retirement or safe storage reuse"}};
  }
  for(const auto& item:connections_) {
    const auto q=item.second.queue.Snapshot();
    out["connections"].push_back({{"id",item.first},{"src",item.second.src},{"dst",item.second.dst},
      {"queue_depth",item.second.queue.Depth()},{"reserved",q.reserved},{"published",q.published},
      {"completed",q.completed},{"closed",q.closed}});
    if(item.second.self)out["connections"].back()["self_service"]={{"mode","declared_service_fifo/1"},
      {"apply_ns",item.second.self_apply},{"complete_ns",item.second.self_complete},
      {"physical_packets",0},{"hardware_qps_modeled",false},{"resource_creations",1},
      {"scope","per-connection functional service; no modeled NIC contention, loopback path, network ACK or GIN CQ"}};
  }
  if(explicit_submission_)out["submission"]={{"mode",staged_submission_?"staged_prefix/1":"explicit_prefix/1"},{"publications",publications_}};
  out["target_events"]=target_events_;out["local_events"]=local_events_;
  out["local_wait_scope"]="functional local LE64 eq/ge, optional consume; no GPU coherence or polling cost";
  out["queue_fences"]=Json::array();
  for(const auto& fence:queue_fences_.items())out["queue_fences"].push_back(fence.value());
  out["queue_fence_scope"]="snapshot of source-owned MRT1 application queue prefixes, ACK/response completion; not GIN flush or GPU source reuse";
  if(source_mode_) {
    out["source_model"]=source_model_;out["source_events"]=source_events_;out["active_source_leases"]=Json::array();
    for(const auto& item:memory_.ReadLeases()) {
      if(item.first>=65536)continue; // local-copy readers have a separate namespace/report
      const auto& s=item.second;
      out["active_source_leases"].push_back({{"operation",item.first},{"owner",s.owner},{"key",s.key},{"address",s.address},{"bytes",s.bytes}});
    }
  }
  if(local_memory_mode_) {
    out["local_memory_model"]=local_memory_model_;out["local_memory_events"]=local_memory_events_;
    if(peer_copy_mode_)out["peer_copy_model"]={{"mode","topology_intra_host_crossbar/1"},
      {"physical_rdma_packets",false},{"gpu_coherence_modeled",false},{"hardware_calibrated",false}};
    out["active_local_memory_leases"]=Json::array();
    for(const auto& item:local_copies_)if(!item.second.completed) {
      auto range=[](const auto& r){return Json{{"owner",r.owner},{"key",r.key},{"address",r.address},{"bytes",r.bytes}};};
      out["active_local_memory_leases"].push_back({{"operation",item.first},
        {"source",range(item.second.source)},{"target",range(item.second.target)}});
    }
  }
  if(bounded_buffers_) {
    if(buffer_model_.contains("target_delivery")) {
      Json contexts=Json::array();
      for(const auto& r:memory_.ReplayStates())contexts.push_back({{"context",r.context},{"next_sequence",r.next},
        {"retained_requests",r.requests},{"retained_encoded_bytes",r.encoded_bytes}});
      out["target_delivery"]={{"mode","carrier_once_put/1"},{"contexts",contexts},
        {"scope","monotonic PUT on the current contiguous byte carrier; application replay rejected; excludes decoder, metadata, packets and observation copies"}};
    }
    std::map<unsigned,uint64_t> registered,local,leases;
    for(const auto& w:memory_.Windows())registered[w.owner]+=RemoteMemory::Size(w);
    for(const auto& [id,copy]:local_copies_)local[copy.source.owner]+=copy.captured.size();
    for(const auto& [id,op]:operations_)if(op.has_source && op.completed<0)leases[op.source.owner]+=op.source.bytes;
    Json owners=Json::array();uint64_t bodies=0;
    auto peak=[](const auto& values,unsigned owner)->uint64_t {const auto it=values.find(owner);return it==values.end()?0:it->second;};
    for(const auto& [id,op]:operations_)bodies+=op.request.body.size();
    for(const auto& [owner,bytes]:registered) {
      const auto it=memory_.CacheBytes().find(owner);
      owners.push_back({{"owner",owner},{"registered_bytes",bytes},
        {"target_cache_bytes",it==memory_.CacheBytes().end()?0:it->second},
        {"source_lease_referenced_bytes",leases[owner]},{"local_copy_snapshot_bytes",local[owner]},
        {"local_copy_snapshot_peak_bytes",peak(local_copy_peaks_,owner)},{"source_capture_scratch_peak_bytes",peak(source_scratch_peaks_,owner)}});
    }
    out["buffer_retention"]={{"config",buffer_model_},{"owners",owners},{"retained_operation_body_bytes",bodies},
      {"scope","named retained data contents only; source leases are references, not extra allocation; excludes metadata, decoder temporaries, packets and observation/report copies"}};
  }
  out["transport"]=transport_.Report();return out;
}
}
