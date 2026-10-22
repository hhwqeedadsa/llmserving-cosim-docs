#pragma once
#include <algorithm>
#include <cstdint>
#include <map>
#include <stdexcept>
#include <utility>

namespace moe_cosim {
// Finite source-local conservative RTO history. Destination addresses identify
// distinct planes in the adapter. This is not an RTT sample cache: a timeout
// can raise the next QP's seed without fabricating any RTT observation.
class RecoveryHistory {
 public:
  using Key=std::pair<uint32_t,uint32_t>;
  struct Entry {int64_t rto,evidence_ns;uint64_t access;};
  struct Seed {int64_t rto;bool hit;};
  RecoveryHistory(uint32_t maximum,int64_t age,int64_t ceiling):maximum_(maximum),age_(age),ceiling_(ceiling) {
    if(!maximum || maximum>256 || age<1 || age>1000000000 || ceiling<1 || ceiling>1000000000)
      throw std::invalid_argument("Invalid bounded source recovery history");
  }
  Seed Start(Key key,int64_t now,int64_t initial) {
    Time(now);if(initial<1 || initial>ceiling_)throw std::invalid_argument("Invalid history default RTO");
    Expire(now);auto it=entries_.find(key);
    if(it==entries_.end()) {++misses_;return {initial,false};}
    ++hits_;it->second.access=++serial_;
    return {std::max(initial,it->second.rto),true};
  }
  void Observe(Key key,int64_t now,int64_t conservative_rto) {
    Time(now);
    if(conservative_rto<1 || conservative_rto>ceiling_)throw std::invalid_argument("History evidence RTO out of bounds");
    Expire(now);auto it=entries_.find(key);
    if(it==entries_.end()) {
      if(entries_.size()==maximum_) {
        const auto victim=std::min_element(entries_.begin(),entries_.end(),[](const auto& a,const auto& b){return a.second.access<b.second.access;});
        entries_.erase(victim);++evictions_;
      }
      entries_.emplace(key,Entry{conservative_rto,now,++serial_});
    } else {
      it->second={std::max(it->second.rto,conservative_rto),now,++serial_};
    }
    ++updates_;
  }
  const std::map<Key,Entry>& Entries() const {return entries_;}
  uint64_t Hits() const {return hits_;}
  uint64_t Misses() const {return misses_;}
  uint64_t Expirations() const {return expirations_;}
  uint64_t Evictions() const {return evictions_;}
  uint64_t Updates() const {return updates_;}
 private:
  void Time(int64_t now) {
    if(now<now_ || now<0 || now>2000000000)throw std::logic_error("Invalid recovery history clock");
    now_=now;
  }
  void Expire(int64_t now) {
    for(auto it=entries_.begin();it!=entries_.end();) {
      if(now-it->second.evidence_ns>=age_) {it=entries_.erase(it);++expirations_;}
      else ++it;
    }
  }
  uint32_t maximum_;
  int64_t age_,ceiling_,now_=0;
  uint64_t serial_=0,hits_=0,misses_=0,expirations_=0,evictions_=0,updates_=0;
  std::map<Key,Entry> entries_;
};
}
