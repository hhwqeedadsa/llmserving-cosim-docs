#pragma once
#include "control_channel.hh"
#include "fabric.hh"

namespace moe_rdma {
// No scheduling, priority or packet changes. Host pipe waits do not advance
// simulation time. A recording overflow fails explicitly rather than sampling.
class ExecutionEvents {
 public:
  ExecutionEvents(ControlChannel& control, uint64_t maximum):control_(control),maximum_(maximum) {
    Require(maximum<=262144,"execution event budget exceeds limit");
  }
  bool Enabled() const {return maximum_!=0;}
  void Emit(Json event) {
    if(!Enabled())return;
    Require(count_<maximum_,"execution event budget exceeded");
    event["schema_version"]="moe-execution-event/1";
    event["type"]="OBSERVE";event["sequence"]=count_;
    event["time_ns"]=ns3::Simulator::Now().GetNanoSeconds();
    control_.Send(event.dump());++count_;
  }
  Json Report() const {return {{"schema_version","moe-execution-events/1"},
    {"max_events",maximum_},{"events",count_}};}
 private:
  ControlChannel& control_;
  uint64_t maximum_,count_=0;
};
}
