#pragma once
#include <cstdint>
#include <map>
#include <set>
#include <stdexcept>
#include <vector>

namespace moe_rdma {
// Local application references only. Never consulted by packet authorization;
// releasing the last view does NOT retire an MR or establish a DMA fence.
class ApplicationWindows {
 public:
  struct View {
    uint64_t id; unsigned owner; uint32_t key; uint64_t address,bytes;
    bool closed=false; std::set<uint64_t> users;
  };
  void Add(View view) {
    Check(views_.size()<32 && view.bytes && view.address<=UINT64_MAX-view.bytes &&
          !view.closed && view.users.empty() && !views_.count(view.id),"invalid application view");
    views_.emplace(view.id,std::move(view));
  }
  void CheckUses(uint64_t operation,unsigned owner,const std::vector<uint64_t>& ids) const {
    Check(!active_.count(operation),"duplicate application use");
    Check(ids.size()<=8,"application use count exceeded");std::set<uint64_t> unique;
    for(const auto id:ids) {
      const auto& v=Get(id);
      Check(v.owner==owner && !v.closed && unique.insert(id).second,"invalid/closed application use");
    }
  }
  void Acquire(uint64_t operation,unsigned owner,const std::vector<uint64_t>& ids) {
    CheckUses(operation,owner,ids);
    if(ids.empty())return;
    active_.emplace(operation,ids);for(auto id:ids)views_.at(id).users.insert(operation);
  }
  std::vector<uint64_t> Release(uint64_t operation) {
    const auto it=active_.find(operation);if(it==active_.end())return {};
    auto ids=it->second;for(auto id:ids)views_.at(id).users.erase(operation);
    active_.erase(it);return ids;
  }
  void Close(uint64_t id,unsigned owner) {
    const auto& v=Get(id);
    Check(v.owner==owner && !v.closed && v.users.empty(),"application close requires owned open unused view");
    views_.at(id).closed=true;
  }
  uint64_t References(unsigned owner,uint32_t key) const {
    uint64_t n=0;for(const auto& [id,v]:views_)if(v.owner==owner && v.key==key && !v.closed)++n;
    return n;
  }
  const View& Get(uint64_t id) const {
    const auto it=views_.find(id);Check(it!=views_.end(),"unknown application view");return it->second;
  }
  const std::map<uint64_t,View>& Views() const {return views_;}
  bool Drained() const {return active_.empty();}
 private:
  static void Check(bool ok,const char* why){if(!ok)throw std::invalid_argument(why);}
  std::map<uint64_t,View> views_;
  std::map<uint64_t,std::vector<uint64_t>> active_;
};
}
