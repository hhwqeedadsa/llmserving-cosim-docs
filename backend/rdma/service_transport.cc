#include "service_transport.hh"

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
  streams_->ConfigureDcqcnReference(config.at("dcqcn_reference"));
  if(config.contains("source_rate_capture"))streams_->ConfigureRateCapture(config.at("source_rate_capture"));
  for(const auto& s:config.at("streams")) {
    auto key=std::make_tuple(s.at("src").get<unsigned>(),s.at("dst").get<unsigned>(),s.at("plane").get<unsigned>());
    Require(bindings_.emplace(key,s.at("id").get<uint64_t>()).second,"ambiguous ASTRA stream binding");
    streams_->Open(s.at("id"),s.at("src"),s.at("dst"),s.at("plane"),s.at("wire_port"),
                   s.at("source_nic"),s.at("destination_nic"),s.at("queue_depth"));
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
  streams_->Submit(m.at("stream"),id,bytes,std::move(received),std::move(acked));
}
void ServiceTransport::Finish(Fabric::Done done) {
  if(!streams_) {fabric_.FinishWhenDrained(std::move(done));return;}
  Require(!finish_,"persistent service finish requested twice");finish_=std::move(done);CheckDrain();
}
void ServiceTransport::CheckDrain() {
  if(finish_ && streams_->Drained()) {
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
  return {{"kind","astra_persistent_streams/1"},{"messages",actual},{"native",native},
    {"registered_messages",messages_.size()},{"scope","actual ASTRA scheduled messages; native stream IDs and connection byte intervals retained; no saved-DAG scheduling substitution"}};
}
}
