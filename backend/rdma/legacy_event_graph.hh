#pragma once
#include <cstdint>
#include <deque>
#include <stdexcept>
#include <vector>

namespace moe_rdma {
// Dependency graph only. External nodes require a real backend callback.
class LegacyEventGraph {
 public:
  struct Node {std::vector<unsigned> children;unsigned missing=0;bool external=false,released=false,queued=false,done=false;int64_t time=-1;};
  unsigned Add(const std::vector<unsigned>& dependencies,bool external) {
    const auto id=nodes_.size();nodes_.push_back({{},static_cast<unsigned>(dependencies.size()),external,false,false,false,-1});
    for(const auto parent:dependencies) {
      if(parent>=id)throw std::invalid_argument("legacy dependency is not earlier");
      nodes_[parent].children.push_back(id);
    }
    Queue(id);return id;
  }
  bool Empty() const {return ready_.empty();}
  unsigned Take() {if(Empty())throw std::logic_error("empty legacy event queue");const auto id=ready_.front();ready_.pop_front();return id;}
  void Release(unsigned id) {
    auto& n=nodes_.at(id);
    if(!n.external || n.released)throw std::logic_error("invalid/repeated external legacy callback");
    n.released=true;Queue(id);
  }
  void Fire(unsigned id,int64_t time) {
    auto& n=nodes_.at(id);
    if(n.done || n.missing || (n.external && !n.released) || !n.queued || time<0)
      throw std::logic_error("legacy event before real dependencies");
    n.done=true;n.time=time;++completed_;
    for(const auto child:n.children) {auto& c=nodes_.at(child);if(!c.missing)throw std::logic_error("legacy indegree underflow");--c.missing;Queue(child);}
  }
  const Node& Get(unsigned id) const {return nodes_.at(id);}
  bool Complete() const {return completed_==nodes_.size();}
  std::size_t Size() const {return nodes_.size();}
 private:
  void Queue(unsigned id) {auto& n=nodes_.at(id);if(!n.queued && !n.missing && (!n.external || n.released)){n.queued=true;ready_.push_back(id);}}
  std::vector<Node> nodes_;std::deque<unsigned> ready_;std::size_t completed_=0;
};
}
