#pragma once
#include <cstdint>
#include <stdexcept>

namespace moe_cosim {
// An explicit deterministic fault experiment, never a congestion algorithm.
// Only the caller identifies an eligible destination data-packet arrival.
class PeriodicDataLoss {
 public:
  PeriodicDataLoss(unsigned numerator,unsigned denominator,unsigned offset,int64_t start,int64_t end):
      numerator_(numerator),denominator_(denominator),offset_(offset),start_(start),end_(end) {
    if(denominator<1 || denominator>1024 || numerator<1 || numerator>denominator || offset>=denominator || start<0 || end<=start)
      throw std::invalid_argument("Invalid periodic destination data loss");
  }
  bool Arrival(int64_t now) {
    ++total_;
    if(now<start_ || now>=end_)return false;
    const bool drop=(window_+offset_)%denominator_<numerator_;
    ++window_;if(drop)++dropped_;return drop;
  }
  uint64_t Total() const {return total_;}
  uint64_t Window() const {return window_;}
  uint64_t Dropped() const {return dropped_;}
 private:
  unsigned numerator_,denominator_,offset_;
  int64_t start_,end_;
  uint64_t total_=0,window_=0,dropped_=0;
};
}
