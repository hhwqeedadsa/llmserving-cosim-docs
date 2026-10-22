#pragma once
#include <algorithm>
#include <cstdint>
#include <stdexcept>

namespace moe_cosim {
// One source-local RTT probe at a time. Conservative Karn sampling: after a
// retransmission/NACK/timeout, wait until all previously sent bytes are ACKed
// before probing a *new* original packet. No receiver/queue/route state.
class AckRttEstimator {
 public:
  AckRttEstimator(int64_t initial,int64_t minimum,int64_t maximum,int64_t granularity):
      minimum_(minimum),maximum_(maximum),granularity_(granularity),base_(initial) {
    if(minimum<1 || initial<minimum || maximum<initial || maximum>1000000000 ||
       granularity<1 || granularity>maximum)throw std::invalid_argument("Invalid RTT estimator bounds");
  }
  void Sent(uint64_t start,uint64_t end,int64_t now) {
    Time(now);
    if(start>=end || start>high_)throw std::logic_error("RTT packet is empty or has source gap");
    if(start<high_)Ambiguous();
    else if(probe_time_<0 && ack_>=barrier_) {probe_end_=end;probe_time_=now;}
    high_=std::max(high_,end);
  }
  int64_t Confirm(uint64_t ack,int64_t now,bool nack=false) {
    Time(now);last_sample_=0;
    if(ack>high_)throw std::logic_error("RTT feedback exceeds emitted bytes");
    if(nack)Ambiguous();
    if(ack<=ack_)return 0;
    if(probe_time_>=0 && ack>=probe_end_) {
      const auto sample=now-probe_time_;
      if(sample<1)throw std::logic_error("RTT sample must be positive");
      if(!samples_) {srtt_=sample;variance_=(sample+1)/2;}
      else {
        const auto error=srtt_>sample?srtt_-sample:sample-srtt_;
        variance_=(3*variance_+error+3)/4; // round upward to integer ns
        srtt_=(7*srtt_+sample+7)/8;
      }
      base_=std::clamp(srtt_+std::max(granularity_,4*variance_),minimum_,maximum_);
      ++samples_;last_sample_=sample;probe_time_=-1;
    }
    ack_=ack;
    return last_sample_;
  }
  void Timeout(int64_t now) {Time(now);last_sample_=0;Ambiguous();}
  int64_t BaseRto() const {return base_;}
  int64_t Srtt() const {return srtt_;}
  int64_t Variance() const {return variance_;}
  int64_t LastSample() const {return last_sample_;}
  uint64_t Samples() const {return samples_;}
  uint64_t DiscardedProbes() const {return discarded_;}
  uint64_t Barrier() const {return barrier_;}
  uint64_t ProbeEnd() const {return probe_end_;}
  int64_t ProbeTime() const {return probe_time_;}
 private:
  void Time(int64_t now) {
    if(now<now_ || now<0 || now>2000000000)throw std::logic_error("Invalid RTT observation clock");
    now_=now;
  }
  void Ambiguous() {
    barrier_=std::max(barrier_,high_);
    if(probe_time_>=0) {++discarded_;probe_time_=-1;}
  }
  int64_t minimum_,maximum_,granularity_,base_,srtt_=0,variance_=0,last_sample_=0,probe_time_=-1,now_=0;
  uint64_t high_=0,ack_=0,barrier_=0,probe_end_=0,samples_=0,discarded_=0;
};
}
