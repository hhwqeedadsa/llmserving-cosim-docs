#pragma once
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <iterator>
#include <map>
#include <stdexcept>

namespace moe_cosim {
// Reference selective repeat, not a production RoCE/IRN wire format. Sender
// state is updated exclusively by a parsed feedback frame at the source.
struct SelectiveAck {
  uint32_t begin=0,end=0;
  bool received=false;
  std::array<uint8_t,16> Encode() const {
    std::array<uint8_t,16> bytes{};
    const uint32_t words[4]={0x4d535231u,begin,end,received?1u:0u};
    for(unsigned w=0;w<4;++w)for(unsigned b=0;b<4;++b)bytes[4*w+b]=words[w]>>(24-8*b);
    return bytes;
  }
  static SelectiveAck Decode(const std::array<uint8_t,16>& bytes) {
    uint32_t words[4]={};
    for(unsigned w=0;w<4;++w)for(unsigned b=0;b<4;++b)words[w]=(words[w]<<8)|bytes[4*w+b];
    if(words[0]!=0x4d535231u || words[3]>1 ||
       (words[3] && words[1]>=words[2]) || (!words[3] && (words[1] || words[2])))
      throw std::invalid_argument("invalid MSR1 feedback extension");
    return {words[1],words[2],bool(words[3])};
  }
};

class SelectiveReceiver {
 public:
  static constexpr std::size_t kMaxPackets=4096;
  explicit SelectiveReceiver(uint64_t capacity):capacity_(capacity) {
    if(!capacity || capacity>2*1024*1024)throw std::invalid_argument("invalid reorder capacity");
  }
  SelectiveAck Receive(uint32_t begin,uint32_t end) {
    if(begin>=end)throw std::invalid_argument("empty selective packet");
    if(end<=prefix_)return {begin,end,true};
    if(begin<prefix_)throw std::invalid_argument("partially overlapping selective packet");
    auto next=held_.lower_bound(begin);
    if(next!=held_.end() && next->first==begin) {
      if(next->second!=end)throw std::invalid_argument("selective packet boundary changed");
      return {begin,end,true};
    }
    if((next!=held_.end() && next->first<end) ||
       (next!=held_.begin() && std::prev(next)->second>begin))
      throw std::invalid_argument("overlapping selective reorder range");
    if(begin==prefix_) {
      prefix_=end;
      while(!held_.empty() && held_.begin()->first==prefix_) {
        const auto range=*held_.begin();used_-=range.second-range.first;
        prefix_=range.second;held_.erase(held_.begin());
      }
      return {begin,end,true};
    }
    if(end-begin>capacity_-used_ || held_.size()==kMaxPackets) {
      ++rejected_;return {};
    }
    held_.emplace(begin,end);used_+=end-begin;peak_=std::max(peak_,used_);
    return {begin,end,true};
  }
  uint32_t Prefix() const {return prefix_;}
  uint64_t Used() const {return used_;}
  uint64_t Peak() const {return peak_;}
  uint64_t Rejected() const {return rejected_;}
  const std::map<uint32_t,uint32_t>& Held() const {return held_;}
 private:
  uint64_t capacity_,used_=0,peak_=0,rejected_=0;
  uint32_t prefix_=0;
  std::map<uint32_t,uint32_t> held_;
};

class SelectiveSender {
 public:
  void Validate(uint32_t cumulative,const SelectiveAck& ack,uint64_t high) const {
    if(cumulative>high || (ack.received && (ack.begin>=ack.end || ack.end>high ||
        (ack.begin<=cumulative && ack.end>cumulative))))
      throw std::invalid_argument("selective feedback covers unsent or partial bytes");
  }
  void Feedback(uint32_t cumulative,const SelectiveAck& ack,uint64_t high) {
    Validate(cumulative,ack,high);prefix_=std::max(prefix_,cumulative);
    while(!received_.empty() && received_.begin()->second<=prefix_)received_.erase(received_.begin());
    if(ack.received && ack.end>prefix_) {
      uint32_t begin=std::max(prefix_,ack.begin),end=ack.end;
      auto next=received_.lower_bound(begin);
      if(next!=received_.begin() && std::prev(next)->second>=begin)--next;
      while(next!=received_.end() && next->first<=end) {
        if(next->second<begin){++next;continue;}
        begin=std::min(begin,next->first);end=std::max(end,next->second);next=received_.erase(next);
      }
      received_.emplace(begin,end);
      if(received_.size()>SelectiveReceiver::kMaxPackets)
        throw std::runtime_error("selective sender interval budget exceeded");
    }
  }
  uint64_t Skip(uint64_t cursor) const {
    cursor=std::max(cursor,uint64_t(prefix_));
    auto next=received_.upper_bound(static_cast<uint32_t>(cursor));
    if(next!=received_.begin()) {
      --next;if(next->first<=cursor && cursor<next->second)cursor=next->second;
    }
    return cursor;
  }
  bool FastRetry(uint32_t cumulative) {
    if(cumulative!=prefix_ || (fast_valid_ && last_fast_==cumulative))return false;
    last_fast_=cumulative;fast_valid_=true;return true;
  }
  const std::map<uint32_t,uint32_t>& Received() const {return received_;}
 private:
  uint32_t prefix_=0,last_fast_=0;
  bool fast_valid_=false;
  std::map<uint32_t,uint32_t> received_;
};
}
