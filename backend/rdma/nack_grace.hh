#pragma once
#include <cstdint>
#include <limits>
#include <stdexcept>

namespace moe_cosim {
// Classical fixed NACK grace, not RACK/IRN. Only actual source feedback and
// source time enter this state. One timer and one retry per cumulative hole.
class NackGrace {
 public:
  explicit NackGrace(int64_t wait):wait_(wait) {
    if(wait<1 || wait>1000000000)throw std::invalid_argument("invalid NACK grace wait");
  }
  void Feedback(int64_t now,uint64_t cumulative,bool nack) {
    if(now<0 || now>std::numeric_limits<int64_t>::max()-wait_)
      throw std::invalid_argument("invalid NACK grace clock");
    if(cumulative>prefix_) {prefix_=cumulative;deadline_=-1;serviced_=false;}
    if(nack && cumulative==prefix_ && !serviced_ && deadline_<0)deadline_=now+wait_;
  }
  bool Expire(int64_t now) {
    if(deadline_<0 || now<deadline_)return false;
    deadline_=-1;serviced_=true;return true;
  }
  void Rto() {deadline_=-1;serviced_=true;}
  void Cancel() {deadline_=-1;}
  uint64_t Prefix() const {return prefix_;}
  int64_t Deadline() const {return deadline_;}
  bool Serviced() const {return serviced_;}
 private:
  int64_t wait_,deadline_=-1;
  uint64_t prefix_=0;
  bool serviced_=false;
};
}
