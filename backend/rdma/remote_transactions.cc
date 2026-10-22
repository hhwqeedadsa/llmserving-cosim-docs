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
  Require(input.at("schema_version")=="moe-rdma-transactions/1","wrong transaction schema");
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
void RemoteTransactions::Submit(const Json& input) {
  const auto id=input.at("id").get<uint64_t>(),connection=input.at("connection").get<uint64_t>();
  Require(operations_.size()<32 && !operations_.count(id) && connections_.count(connection),"invalid transaction work");
  auto& c=connections_.at(connection);
  Operation op;op.id=id;op.connection=connection;op.ready=MemoryNow();
  op.request.sequence=c.operations.size();op.request.address=input.at("address");op.request.key=input.at("key");
  if(input.at("verb")=="put") {op.request.verb=MemoryFrame::Put;op.request.body=MemoryUnhex(input.at("hex"));}
  else {
    Require(input.at("verb")=="fetch_add","unsupported memory transaction verb");
    op.request.verb=MemoryFrame::FetchAdd;op.request.body=MemoryInteger(input.at("value"));
  }
  EncodeMemoryFrame(op.request);
  op.ticket=c.queue.Reserve(id,id);c.operations.push_back(id);operations_.emplace(id,std::move(op));Pump(connection);
}
void RemoteTransactions::Pump(uint64_t connection) {
  auto& c=connections_.at(connection);
  for(const auto id:c.operations) {
    auto& op=operations_.at(id);
    if(op.published>=0 || !c.queue.CanPrepare(op.ticket))continue;
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
  target_events_.push_back({{"time_ns",MemoryNow()},{"connection",connection},{"sequence",frame.sequence},
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
  out["target_events"]=target_events_;out["transport"]=transport_.Report();return out;
}
}
