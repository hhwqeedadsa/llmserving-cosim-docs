#include "service_transport.hh"
#include <iomanip>
#include <sstream>

namespace moe_rdma {
ServiceTransport::ServiceTransport(Fabric& fabric,const Json& config):fabric_(fabric) {
  if(config.is_null())return;
  Require(config.at("schema_version")=="moe-astra-message-transport/1","wrong ASTRA transport schema");
  const auto& c=config.at("limits");max_work_=c.at("max_work");
  Require(max_work_<=fabric.MaxMessages(),"transport work exceeds native message budget");
  Require(config.at("transport_recovery").value("on_exhausted",std::string("stop_simulation"))=="stop_simulation",
          "isolated failures not yet propagated through ASTRA");
  PersistentStreams::Limits limits{c.at("max_streams"),c.at("max_work"),c.at("max_link_transmissions")};
  streams_=std::make_unique<PersistentStreams>(fabric,limits,config.at("transport_recovery"));
  if(config.contains("message_buffers")) {
    const auto& b=config.at("message_buffers");
    Require(b.is_object() && b.size()==7 && b.at("schema_version")=="moe-service-message-buffers/1" &&
      b.at("content")=="deterministic_message_marker/1","invalid service message buffer model");
    for(const auto* key:{"capacity_bytes_per_gpu","max_retained_bytes","max_messages","max_events","max_payload_capture_bytes"})
      Require(b.at(key).is_number_integer() && b.at(key)>0,"invalid service message buffer integer");
    Require(b.at("max_messages")>=max_work_,"message buffer history below transport work budget");
    buffers_=std::make_unique<MessageBuffers>(b.at("capacity_bytes_per_gpu"),b.at("max_retained_bytes"),
      b.at("max_messages"),b.at("max_events"));buffer_configuration_=b;
    streams_->ConfigureMessagePayload(b.at("max_payload_capture_bytes"));
  }
  streams_->ConfigureDcqcnReference(config.at("dcqcn_reference"));
  if(config.contains("source_rate_capture"))streams_->ConfigureRateCapture(config.at("source_rate_capture"));
  for(const auto& s:config.at("streams")) {
    auto key=std::make_tuple(s.at("src").get<unsigned>(),s.at("dst").get<unsigned>(),s.at("plane").get<unsigned>());
    Require(bindings_.emplace(key,s.at("id").get<uint64_t>()).second,"ambiguous ASTRA stream binding");
    streams_->Open(s.at("id"),s.at("src"),s.at("dst"),s.at("plane"),s.at("wire_port"),
                   s.at("source_nic"),s.at("destination_nic"),s.at("queue_depth"));
    if(buffers_) {
      const auto id=s.at("id").get<uint64_t>();incoming_.emplace(id,Incoming{});
      streams_->SetReceiver(id,[this,id](auto bytes){ReceiveBytes(id,std::move(bytes));});
    }
  }
  streams_->ValidateFaultBindings();
  streams_->changed=[this](){CheckDrain();};
}
void ServiceTransport::Register(const Json& messages) {
  if(!streams_)return;
  Require(messages_.size()+messages.size()<=max_work_,"ASTRA persistent work count exceeded");
  auto totals=totals_;std::map<uint64_t,Json> fresh;
  for(const auto& m:messages) {
    const auto id=m.at("id").get<uint64_t>(),bytes=m.at("bytes").get<uint64_t>();
    const auto key=std::make_tuple(m.at("src").get<unsigned>(),m.at("dst").get<unsigned>(),m.at("plane").get<unsigned>());
    Require(bindings_.count(key) && !messages_.count(id) && !fresh.count(id) && bytes>0 && bytes<=1024*1024,
            "unbound, duplicate or oversized ASTRA persistent message");
    auto row=m;row["stream"]=bindings_.at(key);totals[bindings_.at(key)]+=bytes;
    Require(totals[bindings_.at(key)]<=64*1024*1024,"ASTRA persistent stream sequence budget exceeded");
    fresh.emplace(id,std::move(row));
  }
  messages_.insert(fresh.begin(),fresh.end());totals_=std::move(totals);
}
void ServiceTransport::Send(uint64_t id,unsigned src,unsigned dst,uint64_t bytes,unsigned plane,
                            Fabric::Done acked,Fabric::Done received) {
  if(!streams_) {fabric_.Send(id,src,dst,bytes,plane,std::move(acked),std::move(received));return;}
  const auto& m=messages_.at(id);
  Require(m.at("src")==src && m.at("dst")==dst && m.at("bytes")==bytes && m.at("plane")==plane &&
          sent_.insert(id).second,"ASTRA message differs from registered binding or sent twice");
  if(!buffers_) {streams_->Submit(m.at("stream"),id,bytes,std::move(received),std::move(acked));return;}
  buffers_->Post(id,src,dst,bytes,ns3::Simulator::Now().GetNanoSeconds());
  const auto stream=m.at("stream").get<uint64_t>();
  incoming_.at(stream).pending.push_back(id);received_callbacks_.emplace(id,std::move(received));
  // Payload contents are copied into actual Packets by PersistentStreams. The
  // receiver below receives packet fragments, never reads Source(id).
  streams_->SubmitPayload(stream,id,buffers_->Source(id),[this,id,acked=std::move(acked)](){
    buffers_->Ack(id,ns3::Simulator::Now().GetNanoSeconds());if(acked)acked();
  });
}
void ServiceTransport::ReceiveBytes(uint64_t stream,PersistentStreams::Bytes bytes) {
  auto& queue=incoming_.at(stream).pending;std::size_t offset=0;
  while(offset<bytes.size()) {
    Require(!queue.empty(),"packet bytes exceed posted service receive buffers");
    const auto id=queue.front();const auto& row=buffers_->Get(id);
    const auto take=std::min(uint64_t(bytes.size()-offset),row.bytes-row.received);
    Require(take>0,"service receive made no progress");
    PersistentStreams::Bytes fragment(bytes.begin()+offset,bytes.begin()+offset+take);offset+=take;
    if(buffers_->Write(id,fragment,ns3::Simulator::Now().GetNanoSeconds())) {
      queue.pop_front();auto done=std::move(received_callbacks_.at(id));received_callbacks_.erase(id);
      if(done)done();
    }
  }
}
void ServiceTransport::FinishMessageScopes(const std::vector<uint64_t>& messages) {
  if(!buffers_)return;
  for(auto id:messages)buffers_->FinishScope(id,ns3::Simulator::Now().GetNanoSeconds());
}
void ServiceTransport::Finish(Fabric::Done done) {
  if(!streams_) {fabric_.FinishWhenDrained(std::move(done));return;}
  Require(!finish_,"persistent service finish requested twice");finish_=std::move(done);CheckDrain();
}
void ServiceTransport::CheckDrain() {
  if(finish_ && streams_->Drained()) {
    if(buffers_)Require(buffers_->Used()==0 && received_callbacks_.empty() &&
      std::all_of(incoming_.begin(),incoming_.end(),[](const auto& p){return p.second.pending.empty();}),
      "service drained with live message buffers/receivers");
    streams_->Close();Require(fabric_.Drained(),"persistent transport closed before fabric drain");
    auto done=std::move(finish_);finish_={};done();
  }
}
Json ServiceTransport::Report() const {
  Require(bool(streams_),"legacy service has no persistent report");
  auto native=streams_->Report();Json actual=Json::array();
  const auto time=[](int64_t ns)->Json {return ns<0?Json(nullptr):Json(ns);};
  for(const auto& w:native.at("work")) {
    auto m=messages_.at(w.at("id").get<uint64_t>());
    m["ready_ns"]=time(w.at("ready_ns"));m["published_ns"]=time(w.at("published_ns"));
    m["receive_ready_ns"]=time(w.at("accepted_ns"));m["ack_ns"]=time(w.at("acked_ns"));
    m["begin"]=w.at("begin");m["end"]=w.at("end");actual.push_back(std::move(m));
  }
  Json report={{"kind","astra_persistent_streams/1"},{"messages",actual},{"native",native},
    {"registered_messages",messages_.size()},{"scope","actual ASTRA scheduled messages; native stream IDs and connection byte intervals retained; no saved-DAG scheduling substitution"}};
  if(buffers_)report["message_buffers"]=BufferReport();return report;
}
Json ServiceTransport::BufferReport() const {
  const auto hex=[](uint64_t value){std::ostringstream out;out<<std::hex<<std::setfill('0')<<std::setw(16)<<value;return out.str();};
  Json rows=Json::array(),events=Json::array(),owners=Json::array();
  const auto time=[](int64_t value)->Json{return value<0?Json(nullptr):Json(value);};
  for(const auto& [id,r]:buffers_->Rows())rows.push_back({{"id",id},{"src",r.src},{"dst",r.dst},{"bytes",r.bytes},
    {"source_address",r.source_address},{"target_address",r.target_address},{"received_bytes",r.received},
    {"source_digest_fnv64",hex(r.source_digest)},{"target_digest_fnv64",hex(r.target_digest)},
    {"post_ns",time(r.post_ns)},{"receive_ready_ns",time(r.ready_ns)},{"ack_ns",time(r.ack_ns)},
    {"scope_finished_ns",time(r.scope_finished_ns)},{"target_released_ns",time(r.target_released_ns)},
    {"source_live",r.source_live},{"target_live",r.target_live}});
  for(const auto& e:buffers_->Events())events.push_back({{"kind",e.kind},{"message",e.id},{"owner",e.owner},
    {"address",e.address},{"bytes",e.bytes},{"offset",e.offset},{"time_ns",e.time_ns},
    {"used_bytes",e.used},{"global_used_bytes",e.global_used},{"digest_fnv64",hex(e.digest)}});
  for(const auto& [owner,used]:buffers_->Owners())owners.push_back({{"owner",owner},{"used_bytes",used},{"peak_bytes",buffers_->Peaks().at(owner)}});
  return {{"schema_version","moe-service-message-buffer-report/1"},{"configuration",buffer_configuration_},
    {"messages",rows},{"events",events},{"owners",owners},{"used_bytes",buffers_->Used()},{"peak_bytes",buffers_->Peak()},
    {"scope","actual synthetic marker bytes; per-GPU arena offsets; packet-delivered target writes; source ACK and execution-scope retirement; no CUDA/MR/activation/numerical expert or application recovery proof"}};
}
}
