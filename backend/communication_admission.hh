#pragma once
#include <cstdint>
#include <deque>
#include <map>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>
#include <utility>

namespace moe_cosim {
// One source's software admission credits. No simulator, graph, receiver truth,
// future route, or remote queue is accessible through this interface.
class CommunicationAdmission {
 public:
  struct Ready { uint64_t id; uint32_t peer; uint64_t bytes; int64_t ready_ns; };
  CommunicationAdmission(std::string policy,uint32_t limit,uint64_t maximum):
      policy_(std::move(policy)),limit_(limit),maximum_(maximum) {
    if(!maximum_ || (policy_!="passthrough" && policy_!="fifo" && policy_!="destination_round_robin") ||
       (policy_=="passthrough" ? limit_!=0 : limit_==0))
      throw std::invalid_argument("Invalid software communication admission policy/limit");
  }
  void Request(Ready task,int64_t now) {
    CheckTime(now);
    if(!task.bytes || task.ready_ns<0 || task.ready_ns>now || seen_.size()>=maximum_ || seen_.count(task.id))
      throw std::invalid_argument("Admission requires unique, currently ready bounded work");
    seen_.insert(task.id);time_=now;
    if(policy_=="destination_round_robin")peers_[task.peer].push_back(task);
    else fifo_.push_back(task);
    ++pending_;
  }
  std::optional<Ready> Take(int64_t now) {
    CheckTime(now);time_=now;
    if(!pending_ || !Available())return {};
    Ready task;
    if(policy_=="destination_round_robin") {
      auto it=last_peer_?peers_.upper_bound(*last_peer_):peers_.begin();
      if(it==peers_.end())it=peers_.begin();
      task=it->second.front();it->second.pop_front();last_peer_=it->first;
      if(it->second.empty())peers_.erase(it);
    } else { task=fifo_.front();fifo_.pop_front(); }
    --pending_;
    if(!active_.insert(task.id).second)throw std::logic_error("Admission granted twice");
    return task;
  }
  void Complete(uint64_t id,int64_t now) {
    CheckTime(now);
    if(!active_.erase(id))throw std::logic_error("Release must name an active admission owner");
    time_=now;
  }
  bool Available() const {return !limit_ || active_.size()<limit_;}
  uint64_t Pending() const {return pending_;}
  const std::set<uint64_t>& Active() const {return active_;}
  uint32_t Limit() const {return limit_;} // 0 means deliberately uncapped.
  std::vector<uint64_t> PendingIds() const {
    std::vector<uint64_t> result;
    for(const auto& task:fifo_)result.push_back(task.id);
    for(const auto& peer:peers_)for(const auto& task:peer.second)result.push_back(task.id);
    return result;
  }
 private:
  void CheckTime(int64_t now) const {
    if(now<time_ || now<0)throw std::invalid_argument("Admission clock moved backwards");
  }
  std::string policy_;
  uint32_t limit_;
  uint64_t maximum_,pending_=0;
  int64_t time_=0;
  std::optional<uint32_t> last_peer_;
  std::deque<Ready> fifo_;
  std::map<uint32_t,std::deque<Ready>> peers_;
  std::set<uint64_t> active_;
  std::unordered_set<uint64_t> seen_;
};
}
