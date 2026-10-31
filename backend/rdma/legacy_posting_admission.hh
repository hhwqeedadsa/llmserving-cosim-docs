#pragma once
#include <algorithm>
#include <cstdint>
#include <map>

namespace moe_rdma {
inline bool LegacyUsesPostingQueue(unsigned source,unsigned destination) {
  return source!=destination;
}

// Necessary capacity for the canonical single-WQE legacy projection. A queue
// cannot wait for ACKs to free its first unpublished span: those bytes have
// never entered the transport. No publication or completion is synthesized.
class LegacyPostingAdmission {
 public:
  void Observe(unsigned context,unsigned source,unsigned destination,
               bool notification,unsigned message_index) {
    if(!LegacyUsesPostingQueue(source,destination))return;
    auto& span=pending_[context];++span;minimum_=std::max(minimum_,span);
    if(notification || (uint64_t(message_index)+1)%4==0)span=0;
  }
  unsigned MinimumDepth() const {return minimum_;}
  bool AllPublished() const {
    return std::all_of(pending_.begin(),pending_.end(),[](const auto& entry){return entry.second==0;});
  }
 private:
  std::map<unsigned,unsigned> pending_;
  unsigned minimum_=1;
};
}
