#pragma once
#include "execution_events.hh"

namespace moe_rdma {
struct SourceAckWatchdogOptions {
  bool enabled=false;
  int64_t timeout=0;
  uint64_t messages=0,notifications=0;
  Json json;
  static SourceAckWatchdogOptions Parse(const Json& value) {
    SourceAckWatchdogOptions out;
    if(value.is_null())return out;
    Require(value.is_object() && value.size()==4 && value.contains("schema_version") &&
      value.contains("timeout_ns") && value.contains("max_messages") && value.contains("max_notifications"),
      "invalid source ACK watchdog fields");
    Require(value.at("schema_version")=="moe-source-ack-watchdog/1","invalid source ACK watchdog schema");
    auto number=[&](const char* key,uint64_t maximum) {
      const auto& v=value.at(key);
      Require(v.is_number_integer() && v>=1 && v<=maximum,"invalid source ACK watchdog numeric limit");
      return v.get<uint64_t>();
    };
    out.timeout=number("timeout_ns",1000000000);
    out.messages=number("max_messages",65536);
    out.notifications=number("max_notifications",65536);
    out.enabled=true;out.json=value;return out;
  }
};

// A local SEND-post-to-confirmation deadline, not a loss detector. The timer
// does not read receiver state, retry state or the fault-injection definition.
class SourceAckWatchdog {
 public:
  SourceAckWatchdog(ControlChannel& control,ExecutionEvents& events,const SourceAckWatchdogOptions& option):
    control_(control),events_(events),option_(option) {
    Require(!option_.enabled || events_.Enabled(),"source ACK watchdog requires execution events");
  }
  std::function<void(const Json&)> on_timeout;
  void Observe(const std::string& kind,const std::string& graph,uint64_t message,int rank) {
    if(!option_.enabled)return;
    if(kind=="message_send_post") {
      Require(states_.size()<option_.messages && !states_.count(message),"source ACK watchdog message budget/identity violation");
      const auto now=ns3::Simulator::Now().GetNanoSeconds();
      Require(now<=INT64_MAX-option_.timeout,"source ACK watchdog deadline overflow");
      auto inserted=states_.emplace(message,State{graph,rank,now,now+option_.timeout});
      inserted.first->second.timer=ns3::Simulator::Schedule(ns3::NanoSeconds(option_.timeout),[this,message](){
        auto& state=states_.at(message);
        Require(!state.complete && !state.notified,"source ACK watchdog fired after confirmation/twice");
        Require(notifications_<option_.notifications,"source ACK watchdog notification budget exceeded");
        Json notice={{"schema_version","moe-source-ack-timeout/1"},{"type","SOURCE_ACK_TIMEOUT"},
          {"sequence",notifications_},{"time_ns",ns3::Simulator::Now().GetNanoSeconds()},
          {"observed_events",events_.Count()},{"graph_id",state.graph},{"rank",state.rank},
          {"message",message},{"send_post_ns",state.post},{"deadline_ns",state.deadline}};
        control_.Send(notice.dump());state.notified=true;++notifications_;
        if(on_timeout)on_timeout(notice);
      });
    } else if(kind=="message_send_complete") {
      Require(states_.count(message),"source ACK watchdog confirmation without post");
      auto& state=states_.at(message);
      Require(!state.complete && state.graph==graph && state.rank==rank,"source ACK watchdog confirmation identity mismatch");
      if(!state.notified){ns3::Simulator::Cancel(state.timer);++cancelled_;}
      state.complete=true;++completed_;
    }
  }
  Json Report() const {
    return {{"schema_version","moe-source-ack-watchdog-report/1"},{"configuration",option_.json},
      {"messages",states_.size()},{"source_completions",completed_},{"cancelled_timers",cancelled_},
      {"notifications",notifications_},{"pending_timers",states_.size()-cancelled_-notifications_},
      {"unconfirmed_messages",states_.size()-completed_}};
  }
 private:
  struct State {std::string graph;int rank;int64_t post,deadline;ns3::EventId timer;bool notified=false,complete=false;};
  ControlChannel& control_;ExecutionEvents& events_;SourceAckWatchdogOptions option_;
  std::map<uint64_t,State> states_;
  uint64_t notifications_=0,cancelled_=0,completed_=0;
};
}
