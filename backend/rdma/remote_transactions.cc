#include "remote_transactions.hh"

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
RemoteTransactions::RemoteTransactions(Fabric& fabric,const Json& input):transport_(fabric) {
  Require(input.at("schema_version")=="moe-rdma-transactions/1" ||
          input.at("schema_version")=="moe-rdma-transactions/2" ||
          input.at("schema_version")=="moe-rdma-transactions/3" ||
          input.at("schema_version")=="moe-rdma-transactions/4","wrong transaction schema");
  local_memory_mode_=input.at("schema_version")=="moe-rdma-transactions/4";
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
  Require(input.at("connections").is_array() && !input.at("connections").empty() && input.at("connections").size()<=8,
          "transaction connection budget exceeded");
  for(const auto& c:input.at("connections")) {
    const auto id=c.at("id").get<uint64_t>();
    const auto src=c.at("src").get<unsigned>(),dst=c.at("dst").get<unsigned>();
    Require(id<=7 && !connections_.count(id),"invalid transaction connection id");
    connections_.try_emplace(id,src,dst,c.at("queue_depth"));
    transport_.Open(id*2,src,dst,c.at("plane"),c.at("request_port"),c.at("source_nic"),c.at("destination_nic"),64);
    transport_.Open(id*2+1,dst,src,c.at("plane"),c.at("response_port"),c.at("destination_nic"),c.at("source_nic"),64);
    transport_.SetReceiver(id*2,[this,id](auto bytes){
      connections_.at(id).requests.Feed(bytes,[this,id](const auto& frame){Request(id,frame);});
    });
    transport_.SetReceiver(id*2+1,[this,id](auto bytes){
      connections_.at(id).responses.Feed(bytes,[this,id](const auto& frame){Response(id,frame);});
    });
  }
  Require(input.at("windows").is_array() && !input.at("windows").empty() && input.at("windows").size()<=16,
          "invalid remote windows");
  for(const auto& w:input.at("windows"))memory_.Register({w.at("owner"),w.at("key"),w.at("address"),
      w.at("allow_atomic"),MemoryUnhex(w.at("hex"))});
  transport_.changed=[this](){if(changed)changed();};
}
unsigned RemoteTransactions::LocalOwner(const Json& input) const {
  if(input.value("kind",std::string())=="local")return input.at("rank").get<unsigned>();
  return connections_.at(input.at("connection")).src;
}
Json RemoteTransactions::LocalWaits(const Json& input) const {
  Json out=Json::array();
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
  if(input.contains("copy")) {
    const auto id=input.at("id").get<uint64_t>();auto copy=Copy(input);
    Require(!local_copies_.count(id) && local_copies_.size()<16,"invalid local copy identity/budget");
    // All potential conflicts precede condition consumption and acquisition.
    memory_.CheckRead(copy.source);
    memory_.CheckWritable(copy.target.owner,copy.target.key,copy.target.address,copy.target.bytes);
    ConsumeConditions(input,LocalWaits(input));
    memory_.AcquireRead(65536+id,copy.source);memory_.AcquireWrite(id,copy.target);
    local_copies_.emplace(id,copy);LocalMemoryEvent(id,"acquired",copy);return;
  }
  ConsumeConditions(input,LocalWaits(input));
}
RemoteTransactions::LocalCopy RemoteTransactions::Copy(const Json& input) const {
  Require(local_memory_mode_ && input.at("kind")=="local","copy requires v4 local node");
  const auto owner=input.at("rank").get<unsigned>();const auto& copy=input.at("copy");
  Require(copy.is_object() && copy.size()==2 && copy.contains("source") && copy.contains("target"),"invalid local copy fields");
  auto range=[&](const Json& value,bool source,uint64_t bytes=0) {
    Require(value.is_object() && value.size()==(source?3u:2u),"invalid local copy range fields");
    auto integer=[&](const char* key,uint64_t maximum) {
      const auto& v=value.at(key);
      Require(v.is_number_integer() && (v.is_number_unsigned() || v.get<int64_t>()>=0),"invalid local copy integer");
      const auto n=v.get<uint64_t>();Require(n<=maximum,"local copy integer out of bounds");return n;
    };
    RemoteMemory::ReadLease result{owner,static_cast<uint32_t>(integer("key",UINT32_MAX)),integer("address",UINT64_MAX),source?integer("bytes",32768):bytes};
    memory_.CheckRange(result);return result;
  };
  LocalCopy result;result.source=range(copy.at("source"),true);result.target=range(copy.at("target"),false,result.source.bytes);
  const auto& a=result.source;const auto& b=result.target;
  Require(a.address+a.bytes<=b.address || b.address+b.bytes<=a.address,"local copy source/target overlap");
  for(const auto& w:LocalWaits(input))if(w.contains("reset_to")) {
    const auto address=w.at("address").get<uint64_t>();
    Require((address+8<=a.address || a.address+a.bytes<=address) &&
            (address+8<=b.address || b.address+b.bytes<=address),"local copy overlaps own condition reset");
  }
  return result;
}
void RemoteTransactions::ValidateLocalMemory(const Json& input) const {if(input.contains("copy"))Copy(input);}
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
  LocalMemoryEvent(id,"captured",copy,&copy.captured);
}
void RemoteTransactions::CompleteLocal(const Json& input) {
  if(!input.contains("copy"))return;
  const auto id=input.at("id").get<uint64_t>();auto& copy=local_copies_.at(id);
  Require(copy.started && !copy.completed,"invalid local copy completion");
  memory_.CommitWrite(id,copy.captured);LocalMemoryEvent(id,"committed",copy,&copy.captured);
  memory_.ReleaseRead(65536+id);memory_.ReleaseWrite(id);copy.completed=true;
  LocalMemoryEvent(id,"released",copy);
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
    Require(selected.is_array() && !selected.empty() && selected.size()<=8,"invalid queue fence budget");
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
  Require(operations_.size()<32 && !operations_.count(id) && connections_.count(connection),"invalid transaction work");
  auto& c=connections_.at(connection);
  Operation op;op.id=id;op.connection=connection;op.ready=MemoryNow();
  op.request.sequence=c.operations.size();op.request.address=input.at("address");op.request.key=input.at("key");
  if(input.at("verb")=="put") {
    op.request.verb=MemoryFrame::Put;
    if(input.contains("source")) {
      op.source=Source(input);op.has_source=true;op.request.body.resize(op.source.bytes); // length only, never sent before capture
    } else op.request.body=MemoryUnhex(input.at("hex"));
  }
  else {
    Require(input.at("verb")=="fetch_add","unsupported memory transaction verb");
    op.request.verb=MemoryFrame::FetchAdd;op.request.body=MemoryInteger(input.at("value"));
  }
  EncodeMemoryFrame(op.request);
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
  c.operations.push_back(id);operations_.emplace(id,std::move(op));Pump(connection);
}
void RemoteTransactions::Pump(uint64_t connection) {
  auto& c=connections_.at(connection);
  for(const auto id:c.operations) {
    auto& op=operations_.at(id);
    if(op.published>=0 || !c.queue.CanPrepare(op.ticket))continue;
    if(op.has_source) {
      const auto& s=op.source;
      op.request.body=memory_.ReadBytes(s.owner,s.key,s.address,s.bytes);
      SourceEvent(id,"captured",s,&op.request.body);
    }
    c.queue.Prepare(op.ticket);c.queue.PublishThrough(op.ticket+1);
    const auto work=c.queue.ConsumePublished();Require(work && work->work_id==id,"transaction queue consumption mismatch");
    op.published=MemoryNow();
    transport_.SubmitPayload(connection*2,transport_work_++,EncodeMemoryFrame(op.request),[this,id,connection](){
      auto& w=operations_.at(id);Require(w.request_ack<0,"duplicate request ACK callback");
      w.request_ack=MemoryNow();Retire(connection);
    });
  }
}
void RemoteTransactions::Request(uint64_t connection,const MemoryFrame& frame) {
  auto& c=connections_.at(connection);
  // All authority, address, value and duplicate decisions come from actual bytes
  // and target state. Source operation metadata is used below only to report the
  // application event, not to decide what to write or return.
  const auto result=memory_.Apply(connection,c.dst,frame);
  target_events_.push_back({{"order",memory_order_++},{"time_ns",MemoryNow()},{"connection",connection},{"sequence",frame.sequence},
    {"verb",frame.verb},{"key",frame.key},{"address",frame.address},{"body_hex",MemoryHex(frame.body)},
    {"duplicate",result.duplicate},{"old_value",result.old_value}});
  Require(frame.sequence<c.operations.size(),"target request has no submitted application observer");
  auto& op=operations_.at(c.operations.at(frame.sequence));
  if(result.duplicate)++duplicates_;
  else {Require(op.applied<0,"duplicate target application");op.applied=MemoryNow();}
  if(result.atomic) {
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
  op.returned_value=value;op.response=MemoryNow();Retire(connection);
}
void RemoteTransactions::Retire(uint64_t connection) {
  auto& c=connections_.at(connection);
  for(const auto id:c.operations) {
    auto& op=operations_.at(id);
    if(op.completed>=0)continue;
    if(op.request_ack<0 || (op.request.verb==MemoryFrame::FetchAdd && op.response<0))break;
    op.completed=MemoryNow();c.queue.CompleteThrough(op.ticket+1);
    if(op.has_source) {memory_.ReleaseRead(id);SourceEvent(id,"released",op.source);}
  }
  Pump(connection);if(changed)ns3::Simulator::ScheduleNow(changed);
}
bool RemoteTransactions::Occurred(uint64_t id,const std::string& event) const {
  const auto it=operations_.find(id);if(it==operations_.end())return false;
  if(event=="applied")return it->second.applied>=0;
  if(event=="request_acked")return it->second.request_ack>=0;
  Require(event=="completed","unsupported transaction event");return it->second.completed>=0;
}
bool RemoteTransactions::Drained() const {
  if(!memory_.ReadLeases().empty() || !memory_.WriteLeases().empty())return false;
  for(const auto& item:connections_)if(!item.second.queue.Drained() || !item.second.requests.Empty() || !item.second.responses.Empty())return false;
  return transport_.Drained();
}
void RemoteTransactions::Close() {
  Require(Drained(),"transaction close before drain");
  for(auto& item:connections_)item.second.queue.Close();
  transport_.Close();
}
Json RemoteTransactions::Report() const {
  Json out={{"schema_version","moe-rdma-transaction-result/1"},{"operations",Json::array()},{"windows",Json::array()},
    {"connections",Json::array()},{"duplicate_requests",duplicates_},{"gpu_visibility_modeled",false},
    {"hardware_calibrated",false},{"roce_wire_equivalent",false},
    {"scope","MRT1 functional addressed put/fetch-add with actual native byte request/response transport; not a GIN timing baseline"}};
  for(const auto& item:operations_) {
    const auto& op=item.second;
    Json value={{"id",op.id},{"connection",op.connection},{"sequence",op.request.sequence},{"ticket",op.ticket},
      {"verb",op.request.verb==MemoryFrame::Put?"put":"fetch_add"},{"ready_ns",op.ready},{"published_ns",op.published},
      {"applied_ns",op.applied},{"request_acked_ns",op.request_ack},{"response_ns",op.response},{"completed_ns",op.completed}};
    if(op.request.verb==MemoryFrame::FetchAdd && op.response>=0)value["returned_value"]=op.returned_value;
    out["operations"].push_back(value);
  }
  for(const auto& w:memory_.Windows())out["windows"].push_back({{"owner",w.owner},{"key",w.key},{"address",w.address},
    {"allow_atomic",w.atomic},{"hex",MemoryHex(w.bytes)}});
  for(const auto& item:connections_) {
    const auto q=item.second.queue.Snapshot();
    out["connections"].push_back({{"id",item.first},{"src",item.second.src},{"dst",item.second.dst},
      {"queue_depth",item.second.queue.Depth()},{"reserved",q.reserved},{"published",q.published},
      {"completed",q.completed},{"closed",q.closed}});
  }
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
    out["active_local_memory_leases"]=Json::array();
    for(const auto& item:local_copies_)if(!item.second.completed) {
      auto range=[](const auto& r){return Json{{"owner",r.owner},{"key",r.key},{"address",r.address},{"bytes",r.bytes}};};
      out["active_local_memory_leases"].push_back({{"operation",item.first},
        {"source",range(item.second.source)},{"target",range(item.second.target)}});
    }
  }
  out["transport"]=transport_.Report();return out;
}
}
