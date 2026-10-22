#pragma once
#include "retransmission_timer.hh"
#include "ack_rtt_estimator.hh"
#include <optional>

namespace moe_cosim {
// Pure protocol composition. The adapter owns NIC/QP/EventId lifetimes; this
// class owns sequence safety, timer transitions and optional RTT estimation.
class SourceRecovery {
 public:
  SourceRecovery(uint64_t bytes,int64_t initial,int64_t maximum,unsigned retries,
                 int64_t minimum=0,int64_t granularity=0):timer_(bytes,initial,maximum,retries,minimum) {
    if(minimum || granularity)rtt_.emplace(initial,minimum,maximum,granularity);
  }
  void Sent(uint64_t start,uint64_t end,int64_t now) {
    timer_.Sent(end,now);
    if(rtt_)rtt_->Sent(start,end,now);
  }
  bool Confirm(uint64_t ack,int64_t now,bool nack=false) {
    if(ack>timer_.High() || timer_.Failed())throw std::logic_error("Invalid source ACK for recovery");
    if(rtt_)rtt_->Confirm(ack,now,nack);
    return timer_.Confirm(ack,now,rtt_?rtt_->BaseRto():0);
  }
  RetransmissionTimer::Expiry Expire(int64_t now) {
    const auto result=timer_.Expire(now);
    if(rtt_)rtt_->Timeout(now);
    return result;
  }
  const RetransmissionTimer& Timer() const {return timer_;}
  const std::optional<AckRttEstimator>& Rtt() const {return rtt_;}
 private:
  RetransmissionTimer timer_;
  std::optional<AckRttEstimator> rtt_;
};
}
