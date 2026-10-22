#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <stdexcept>

namespace moe_rdma {
// Explicit functional reference to DCQCN 2015 Fig. 6/7 and equations 1--4.
// Integer nanosecond deadlines; no simulator, global truth, or hardware claims.
class DcqcnNp {
 public:
  explicit DcqcnNp(int64_t period):period_(period) {
    if(period<=0)throw std::invalid_argument("NP period must be positive");
  }
  // A deadline is resolved before a mark at the same timestamp, independent
  // of which ns-3 callback was inserted first. Caller enqueues one real CNP
  // for each true return; generation/enqueue, rather than wire TX, starts N.
  bool Mark(int64_t now) {
    bool emitted=false;
    if(deadline_>=0 && now>=deadline_)emitted=Expire(now);
    if(deadline_<0) { deadline_=now+period_;return true; }
    pending_=true;return emitted;
  }
  bool Expire(int64_t now) {
    if(now!=deadline_)throw std::logic_error("NP expiry must match deadline");
    const bool emit=pending_;pending_=false;
    deadline_=emit?now+period_:-1;return emit;
  }
  bool Pending() const { return pending_; }
  int64_t Deadline() const { return deadline_; }
 private:
  int64_t period_,deadline_=-1;
  bool pending_=false;
};

struct DcqcnRpConfig {
  int64_t alpha_ns, recovery_ns;
  uint64_t recovery_bytes, minimum_bps, additive_bps, hyper_bps;
  unsigned fast_rounds;
  double gain;
};

class DcqcnRp {
 public:
  DcqcnRp(DcqcnRpConfig config,uint64_t maximum):c_(config),maximum_(maximum),rate_(maximum),target_(maximum) {
    if(c_.alpha_ns<=0 || c_.recovery_ns<=0 || !c_.recovery_bytes || !c_.minimum_bps ||
       c_.minimum_bps>maximum || !c_.fast_rounds || !c_.additive_bps || !c_.hyper_bps ||
       !std::isfinite(c_.gain) || c_.gain<=0 || c_.gain>1)
      throw std::invalid_argument("invalid reference RP configuration");
  }
  void Cnp(int64_t now) {
    target_=rate_;
    rate_=std::max(c_.minimum_bps,static_cast<uint64_t>(std::floor(rate_*(1-alpha_/2))));
    alpha_=(1-c_.gain)*alpha_+c_.gain;
    active_=true;timer_rounds_=byte_rounds_=0;remaining_=c_.recovery_bytes;
    alpha_due_=now+c_.alpha_ns;recovery_due_=now+c_.recovery_ns;
  }
  void Alpha(int64_t now) {
    if(!active_ || now!=alpha_due_)throw std::logic_error("RP alpha deadline mismatch");
    alpha_=(1-c_.gain)*alpha_;alpha_due_=now+c_.alpha_ns;
  }
  void Timer(int64_t now) {
    if(!active_ || now!=recovery_due_)throw std::logic_error("RP recovery deadline mismatch");
    recovery_due_=now+c_.recovery_ns;++timer_rounds_;Recover();
  }
  // Count only actual source-transmitted model frame bytes after a CNP.
  // Timer and byte paths keep separate iteration counts, as Fig. 7 requires.
  unsigned Sent(uint64_t bytes) {
    unsigned events=0;
    if(!active_)return events;
    while(bytes>=remaining_) {
      bytes-=remaining_;remaining_=c_.recovery_bytes;++byte_rounds_;Recover();++events;
    }
    remaining_-=bytes;return events;
  }
  uint64_t Rate() const { return rate_; }
  uint64_t Target() const { return target_; }
  uint64_t Remaining() const { return active_?remaining_:c_.recovery_bytes; }
  double AlphaValue() const { return alpha_; }
  unsigned TimerRounds() const { return timer_rounds_; }
  unsigned ByteRounds() const { return byte_rounds_; }
  bool Active() const { return active_; }
  int64_t AlphaDue() const { return alpha_due_; }
  int64_t RecoveryDue() const { return recovery_due_; }
 private:
  void Recover() {
    // Fig.7 increments the triggering counter BEFORE these strict comparisons.
    // Hyper increase uses an explicitly configured fixed step (native fork's
    // convention), not an unspecified QCN acceleration or hardware claim.
    if(std::max(timer_rounds_,byte_rounds_)>=c_.fast_rounds) {
      const auto step=std::min(timer_rounds_,byte_rounds_)>c_.fast_rounds?c_.hyper_bps:c_.additive_bps;
      target_+=std::min(step,maximum_-target_);
    }
    rate_=rate_/2+target_/2+(rate_%2+target_%2)/2;
    rate_=std::max(c_.minimum_bps,std::min(maximum_,rate_));
  }
  DcqcnRpConfig c_;
  uint64_t maximum_,rate_,target_,remaining_=0;
  double alpha_=1;
  unsigned timer_rounds_=0,byte_rounds_=0;
  int64_t alpha_due_=-1,recovery_due_=-1;
  bool active_=false;
};
}
