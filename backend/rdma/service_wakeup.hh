#pragma once
#include "fabric.hh"

namespace moe_rdma {
// One replaceable control deadline in the same ns-3 clock as the fabric.
// It wakes the frontend; it never manufactures graph or message completion.
class ServiceWakeup {
 public:
  using Fired=std::function<void(int64_t)>;
  ServiceWakeup(uint64_t limit,int64_t maximum,Fired fired):limit_(limit),maximum_(maximum),fired_(std::move(fired)) {}
  bool Enabled() const{return limit_>0;}
  bool Pending() const{return pending_;}
  void Update(const Json& value) {
    Require(Enabled(),"undeclared service wakeup");
    int64_t target=-1;
    if(!value.is_null()) {
      Require(value.is_number_integer() && value>Now() && value<maximum_,"wakeup deadline must be strictly future and before simulation stop");
      target=value.get<int64_t>();
    }
    if((pending_ && target==deadline_) || (!pending_ && target<0))return;
    const auto needed=unsigned(pending_)+unsigned(target>=0);
    // Reserve the eventual fired event before changing a live timer.
    Require(events_.size()+needed+unsigned(target>=0)<=limit_,"service wakeup event budget exceeded");
    if(pending_) {
      ns3::Simulator::Cancel(timer_);Record("cancelled");pending_=false;
    }
    if(target>=0) {
      deadline_=target;ticket_=next_ticket_++;pending_=true;Record("armed");
      timer_=ns3::Simulator::Schedule(ns3::NanoSeconds(target-Now()),[this](){
        Require(pending_ && Now()==deadline_,"service wakeup fired outside live deadline");
        Record("fired");pending_=false;fired_(deadline_);
      });
    }
  }
  Json Report() const {
    return {{"schema_version","moe-service-wakeup-report/1"},{"max_events",limit_},
      {"events",events_},{"pending_deadline_ns",pending_?Json(deadline_):Json(nullptr)}};
  }
 private:
  static int64_t Now(){return ns3::Simulator::Now().GetNanoSeconds();}
  void Record(const char* kind) {
    Require(events_.size()<limit_,"service wakeup event budget exceeded");
    events_.push_back({{"sequence",events_.size()},{"time_ns",Now()},{"kind",kind},
      {"deadline_ns",deadline_},{"ticket",ticket_}});
  }
  uint64_t limit_,next_ticket_=0,ticket_=0;
  int64_t maximum_,deadline_=-1;bool pending_=false;
  ns3::EventId timer_;Fired fired_;Json events_=Json::array();
};
}
