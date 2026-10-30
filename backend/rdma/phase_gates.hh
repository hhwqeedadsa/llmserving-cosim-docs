#pragma once
#include <cstdint>
#include <functional>
#include <map>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

namespace moe_rdma {
// Pure callback join. It neither advances time nor schedules network traffic.
// The service invokes callbacks on its existing ns-3/ASTRA clock.
class PhaseGates {
 public:
  using Key=std::pair<std::string,std::string>;
  struct Group {
    std::map<int,uint64_t> members;
    std::map<int,int64_t> arrivals;
    std::map<int,std::function<void()>> callbacks;
    int64_t released=-1;
  };
  void Add(const Key& key,const std::map<int,uint64_t>& members) {
    Check(!key.first.empty() && !key.second.empty() && !members.empty(),"empty phase gate");
    Check(!groups_.count(key) && groups_.size()<65536 && member_count_+members.size()<=131072,"phase gate identity/budget exceeded");
    for(auto [rank,node]:members)Check(!bindings_.count({key.first,rank,node}),"phase node reused");
    groups_.emplace(key,Group{members,{},{},-1});member_count_+=members.size();
    for(auto [rank,node]:members)bindings_.emplace(std::make_tuple(key.first,rank,node),key);
  }
  bool Contains(const std::string& graph,int rank,uint64_t node) const {
    return bindings_.count({graph,rank,node});
  }
  std::vector<std::function<void()>> Arrive(const std::string& graph,int rank,uint64_t node,int64_t now,std::function<void()> done) {
    auto binding=bindings_.find({graph,rank,node});Check(binding!=bindings_.end(),"unknown phase member");
    auto& group=groups_.at(binding->second);
    Check(now>=0 && bool(done) && group.released<0 && !group.arrivals.count(rank),"repeated/invalid phase arrival");
    group.arrivals.emplace(rank,now);group.callbacks.emplace(rank,std::move(done));
    if(group.arrivals.size()!=group.members.size())return {};
    for(auto [_,time]:group.arrivals)Check(time<=now,"phase clock went backwards");
    group.released=now;std::vector<std::function<void()>> ready;
    for(auto& [_,callback]:group.callbacks)ready.push_back(std::move(callback));
    group.callbacks.clear();return ready;
  }
  const std::map<Key,Group>& Groups() const {return groups_;}
 private:
  static void Check(bool value,const char* message){if(!value)throw std::runtime_error(message);}
  std::map<Key,Group> groups_;
  std::map<std::tuple<std::string,int,uint64_t>,Key> bindings_;
  size_t member_count_=0;
};
}
