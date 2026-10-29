#include "astra_adapter.hh"
#include <cmath>
#include <limits>

namespace moe_rdma {
static int64_t Now() { return ns3::Simulator::Now().GetNanoSeconds(); }
void Messages::Register(const Json& messages) {
  Require(messages.is_array() && operations_.size()+messages.size()<=fabric_.MaxMessages(),"logical message budget exceeded");
  std::set<std::tuple<int,int,int>> incoming;
  std::set<uint64_t> ids;
  for(const auto& m:messages) {
    const auto id=m.at("id").get<uint64_t>();
    auto key=std::make_tuple(m.at("src").get<int>(),m.at("dst").get<int>(),m.at("tag").get<int>());
    Require(ids.insert(id).second && !operations_.count(id) && incoming.insert(key).second && !keys_.count(key),
            "reused logical message identity");
    Require(m.at("src")!=m.at("dst") && m.at("bytes").get<uint64_t>()>0,"local or empty network message");
  }
  transport_.Register(messages);
  for(const auto& m:messages) {
    const auto id=m.at("id").get<uint64_t>();
    keys_.emplace(std::make_tuple(m.at("src").get<int>(),m.at("dst").get<int>(),m.at("tag").get<int>()),id);
    Operation op;op.src=m.at("src");op.dst=m.at("dst");op.plane=m.at("plane");op.bytes=m.at("bytes");
    op.id=id;
    operations_.emplace(id,std::move(op));
  }
}
void Messages::Deliver(uint64_t id) {
  auto& op=operations_.at(id);
  if(op.received && op.posted && !op.recv_called) {
    op.recv_called=true;op.recv_callback=Now();++recv_callbacks_;
    auto callback=std::move(op.recv_done);callback();
  }
  if(op.acked && !op.send_called) {
    op.send_called=true;op.send_callback=Now();++send_callbacks_;
    auto callback=std::move(op.send_done);callback();
  }
}
void Messages::Post(bool send,int rank,uint64_t bytes,int type,int peer,int tag,void(*callback)(void*),void* arg) {
  Require(type==AstraSim::UINT8 && callback,"ASTRA native messages must use byte counts and a callback");
  const auto key=send?std::make_tuple(rank,peer,tag):std::make_tuple(peer,rank,tag);
  Require(keys_.count(key),"unregistered ASTRA message");
  const auto id=keys_.at(key);auto& op=operations_.at(id);
  Require(bytes==op.bytes,"ASTRA count differs from descriptor");
  if(send) {
    Require(!op.sent,"SEND posted twice");op.sent=true;op.send_done=[callback,arg](){callback(arg);};
    op.send_post=Now();++send_posts_;
    transport_.Send(id,op.src,op.dst,bytes,op.plane,
      [this,id](){operations_.at(id).acked=true;Deliver(id);},
      [this,id](){operations_.at(id).received=true;Deliver(id);});
  } else {
    Require(!op.posted,"RECV posted twice");op.posted=true;op.recv_done=[callback,arg](){callback(arg);};
    op.recv_post=Now();++recv_posts_;
    ns3::Simulator::ScheduleNow([this,id](){Deliver(id);});
  }
}
bool Messages::Complete(const std::vector<uint64_t>& messages) const {
  for(const auto id:messages) {
    const auto& op=operations_.at(id);
    if(!op.send_called || !op.recv_called)return false;
  }
  return true;
}
Json Messages::Report() {
  Json result=Json::array();
  const auto time=[](int64_t value)->Json{return value<0?Json(nullptr):Json(value);};
  for(const auto& item:operations_) {
    const auto& op=item.second;
    result.push_back({{"id",op.id},{"send_post_ns",time(op.send_post)},{"recv_post_ns",time(op.recv_post)},
                     {"send_callback_ns",time(op.send_callback)},{"recv_callback_ns",time(op.recv_callback)}});
  }
  return result;
}
Json Messages::HostCounters() const {
  return {{"registered_messages",operations_.size()},{"send_posts",send_posts_},{"recv_posts",recv_posts_},
    {"send_callbacks",send_callbacks_},{"recv_callbacks",recv_callbacks_},
    {"outstanding_send_callbacks",send_posts_-send_callbacks_},
    {"outstanding_recv_callbacks",recv_posts_-recv_callbacks_}};
}
AstraSim::timespec_t Adapter::sim_get_time() { return {AstraSim::NS,static_cast<long double>(Now())}; }
void Adapter::sim_schedule(AstraSim::timespec_t delta,void(*callback)(void*),void* arg) {
  Require(delta.time_res==AstraSim::NS && std::isfinite(delta.time_val) && delta.time_val>=0 && callback,
          "ASTRA requires nonnegative nanosecond events");
  const auto rounded=std::ceil(delta.time_val);
  Require(rounded<=static_cast<long double>(std::numeric_limits<int64_t>::max()-Now()),"event time overflow");
  ns3::Simulator::Schedule(ns3::NanoSeconds(static_cast<int64_t>(rounded)),[callback,arg](){callback(arg);});
}
int Adapter::sim_send(void*,uint64_t count,int type,int peer,int tag,AstraSim::sim_request*,void(*callback)(void*),void* arg) {
  messages_.Post(true,rank,count,type,peer,tag,callback,arg);return 0;
}
int Adapter::sim_recv(void*,uint64_t count,int type,int peer,int tag,AstraSim::sim_request*,void(*callback)(void*),void* arg) {
  messages_.Post(false,rank,count,type,peer,tag,callback,arg);return 0;
}
}
