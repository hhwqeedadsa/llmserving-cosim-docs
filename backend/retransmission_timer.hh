#pragma once
#include <algorithm>
#include <cstdint>
#include <stdexcept>

namespace moe_cosim {
// Source-local cumulative-ACK timer. It cannot access receiver or network state.
class RetransmissionTimer {
 public:
  enum class Expiry { Retry, Exhausted };
  RetransmissionTimer(uint64_t bytes,int64_t initial,int64_t maximum,unsigned retries):
      bytes_(bytes),initial_(initial),maximum_(maximum),rto_(initial),limit_(retries) {
    if(!bytes || initial<1 || maximum<initial || maximum>1000000000 || retries<1 || retries>16)
      throw std::invalid_argument("Invalid declared retransmission timer bounds");
  }
  void Sent(uint64_t end,int64_t now) {
    Time(now);
    if(failed_ || Complete() || !end || end>bytes_)throw std::logic_error("Invalid source transmission progress");
    high_=std::max(high_,end);
    if(deadline_<0 && high_>ack_)deadline_=now+rto_;
  }
  bool Confirm(uint64_t ack,int64_t now) {
    Time(now);
    if(failed_ || ack>high_)throw std::logic_error("ACK exceeds actually transmitted source bytes");
    if(ack<=ack_)return false; // Duplicate or stale feedback never postpones timeout.
    ack_=ack;retries_=0;rto_=initial_;
    deadline_=high_>ack_?now+rto_:-1;
    return true;
  }
  Expiry Expire(int64_t now) {
    Time(now);
    if(failed_ || deadline_<0 || now<deadline_ || high_<=ack_)
      throw std::logic_error("Stale or premature retransmission expiry");
    ++expiries_;
    if(retries_==limit_) {failed_=true;deadline_=-1;return Expiry::Exhausted;}
    ++retries_;++total_;rto_=std::min(maximum_,rto_*2);deadline_=now+rto_;
    return Expiry::Retry;
  }
  bool Complete() const {return ack_==bytes_;}
  bool Failed() const {return failed_;}
  uint64_t Ack() const {return ack_;}
  uint64_t High() const {return high_;}
  unsigned Retries() const {return retries_;}
  uint64_t TotalRetries() const {return total_;}
  uint64_t Expiries() const {return expiries_;}
  int64_t Deadline() const {return deadline_;}
  int64_t Rto() const {return rto_;}
 private:
  void Time(int64_t now) {
    if(now<now_ || now<0 || now>2000000000)throw std::logic_error("Invalid retransmission clock");
    now_=now;
  }
  uint64_t bytes_,ack_=0,high_=0,total_=0,expiries_=0;
  int64_t initial_,maximum_,rto_,deadline_=-1,now_=0;
  unsigned limit_,retries_=0;
  bool failed_=false;
};
}
