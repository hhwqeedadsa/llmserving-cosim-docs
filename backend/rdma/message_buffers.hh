#pragma once
#include <algorithm>
#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace moe_rdma {
// Actual host byte storage for an EXPLICIT service functional mode. Addresses
// are offsets in a per-simulated-GPU arena, not CUDA addresses, MRs or rkeys.
// Source and target occupy the SAME arena namespace; labels/epochs cannot hide
// aliasing. The transport must supply actual newly accepted packet bytes.
class MessageBuffers {
 public:
  struct Row {
    uint64_t id,bytes,source_address,target_address;
    unsigned src,dst;
    std::vector<uint8_t> source,target;
    uint64_t received=0,source_digest=0,target_digest=0;
    int64_t post_ns=-1,ready_ns=-1,ack_ns=-1,scope_finished_ns=-1,target_released_ns=-1;
    bool source_live=true,target_live=true;
  };
  struct Event {
    std::string kind;uint64_t id,address,bytes,offset,used,global_used,digest;unsigned owner;int64_t time_ns;
  };
  MessageBuffers(uint64_t capacity,uint64_t total,uint64_t messages,uint64_t events):
    capacity_(capacity),total_(total),max_messages_(messages),max_events_(events) {
    Check(capacity>0 && capacity<=64*1024*1024 && total>0 && total<=128*1024*1024 &&
          messages>0 && messages<=131072 && events>=7*messages && events<=1000000,
          "invalid message buffer capacity/budget");
  }
  static uint64_t Digest(const std::vector<uint8_t>& bytes) {
    uint64_t value=14695981039346656037ULL;
    for(auto b:bytes){value^=b;value*=1099511628211ULL;}return value;
  }
  static std::vector<uint8_t> Marker(uint64_t id,uint64_t bytes) {
    Check(bytes>0 && bytes<=1024*1024,"invalid marker length");
    std::vector<uint8_t> result(bytes);
    for(uint64_t i=0;i<bytes;++i)result[i]=uint8_t((id>>(8*(i%8)))^(i/8));
    return result;
  }
  void Post(uint64_t id,unsigned src,unsigned dst,uint64_t bytes,int64_t now) {
    Admission(now,2);
    Check(src!=dst && bytes>0 && bytes<=1024*1024 && !rows_.count(id) && rows_.size()<max_messages_,
          "invalid/repeated buffered message");
    Check(2*bytes<=total_-used_,"message buffers total retained byte budget exceeded");
    // Both reservations and the event budget are checked BEFORE any mutation.
    const auto source=Find(src,bytes),target=Find(dst,bytes);
    Row row{id,bytes,source,target,src,dst,Marker(id,bytes),std::vector<uint8_t>(bytes,0)};
    row.post_ns=now;row.source_digest=Digest(row.source);
    rows_.emplace(id,std::move(row));
    owners_[src]+=bytes;owners_[dst]+=bytes;used_+=2*bytes;
    peaks_[src]=std::max(peaks_[src],owners_[src]);peaks_[dst]=std::max(peaks_[dst],owners_[dst]);
    peak_=std::max(peak_,used_);
    Emit("source_acquired",rows_.at(id),src,source,bytes,0,0,now);
    Emit("target_acquired",rows_.at(id),dst,target,bytes,0,0,now);
  }
  const std::vector<uint8_t>& Source(uint64_t id) const {
    const auto& row=Get(id);Check(row.source_live,"source buffer already released");return row.source;
  }
  bool Write(uint64_t id,const std::vector<uint8_t>& bytes,int64_t now) {
    auto& row=Mutable(id);const auto offset=row.received;
    Check(row.target_live && row.ready_ns<0 && !bytes.empty() && bytes.size()<=row.bytes-offset,
          "late/duplicate/out-of-range target buffer write");
    const bool ready=bytes.size()==row.bytes-offset;Admission(now,ready?2:1);
    std::copy(bytes.begin(),bytes.end(),row.target.begin()+offset);row.received+=bytes.size();
    Emit("target_write",row,row.dst,row.target_address,bytes.size(),offset,Digest(bytes),now);
    if(ready) {
      row.ready_ns=now;row.target_digest=Digest(row.target);
      Emit("target_ready",row,row.dst,row.target_address,row.bytes,0,row.target_digest,now);
    }
    return ready;
  }
  void Ack(uint64_t id,int64_t now) {
    auto& row=Mutable(id);Check(row.source_live && row.ack_ns<0,"duplicate message buffer ACK");
    const bool release=row.scope_finished_ns>=0 && row.ready_ns>=0;
    Admission(now,release?2:1);row.ack_ns=now;row.source_live=false;
    owners_.at(row.src)-=row.bytes;used_-=row.bytes;std::vector<uint8_t>().swap(row.source);
    Emit("source_released",row,row.src,row.source_address,row.bytes,0,row.source_digest,now);
    if(release)ReleaseTarget(row,now);
  }
  void FinishScope(uint64_t id,int64_t now) {
    auto& row=Mutable(id);
    Check(row.target_live && row.ready_ns>=0 && row.scope_finished_ns<0,"unfinished/repeated message buffer scope release");
    Admission(now,row.ack_ns>=0?2:1);row.scope_finished_ns=now;
    Emit("scope_finished",row,row.dst,row.target_address,row.bytes,0,0,now);
    if(row.ack_ns>=0)ReleaseTarget(row,now);
  }
  const Row& Get(uint64_t id) const {
    const auto it=rows_.find(id);Check(it!=rows_.end(),"unknown buffered message");return it->second;
  }
  const std::map<uint64_t,Row>& Rows() const {return rows_;}
  const std::vector<Event>& Events() const {return events_;}
  const std::map<unsigned,uint64_t>& Owners() const {return owners_;}
  const std::map<unsigned,uint64_t>& Peaks() const {return peaks_;}
  uint64_t Used() const {return used_;}
  uint64_t Peak() const {return peak_;}
 private:
  static void Check(bool ok,const char* reason){if(!ok)throw std::invalid_argument(reason);}
  Row& Mutable(uint64_t id){return const_cast<Row&>(std::as_const(*this).Get(id));}
  uint64_t Find(unsigned owner,uint64_t bytes) const {
    Check(bytes<=capacity_,"message exceeds per-GPU buffer capacity");
    std::vector<std::pair<uint64_t,uint64_t>> held;
    for(const auto& [id,row]:rows_) {
      if(row.src==owner && row.source_live)held.emplace_back(row.source_address,row.source_address+row.bytes);
      if(row.dst==owner && row.target_live)held.emplace_back(row.target_address,row.target_address+row.bytes);
    }
    std::sort(held.begin(),held.end());uint64_t cursor=0;
    for(auto [begin,end]:held) {if(bytes<=begin-cursor)return cursor;cursor=end;}
    Check(bytes<=capacity_-cursor,"per-GPU message buffer capacity exceeded");return cursor;
  }
  void Admission(int64_t now,uint64_t count) const {
    Check(now>=0 && now>=now_,"message buffer time moved backwards");
    Check(count<=max_events_-events_.size(),"message buffer event budget exceeded");
  }
  void Emit(const char* kind,const Row& row,unsigned owner,uint64_t address,uint64_t bytes,
            uint64_t offset,uint64_t digest,int64_t now) {
    events_.push_back({kind,row.id,address,bytes,offset,owners_.at(owner),used_,digest,owner,now});now_=now;
  }
  void ReleaseTarget(Row& row,int64_t now) {
    Check(row.target_live && row.ready_ns>=0 && row.ack_ns>=0 && row.scope_finished_ns>=0,
          "target release before receive/ACK/scope completion");
    row.target_live=false;row.target_released_ns=now;
    owners_.at(row.dst)-=row.bytes;used_-=row.bytes;std::vector<uint8_t>().swap(row.target);
    Emit("target_released",row,row.dst,row.target_address,row.bytes,0,row.target_digest,now);
  }
  uint64_t capacity_,total_,max_messages_,max_events_,used_=0,peak_=0;
  int64_t now_=0;
  std::map<uint64_t,Row> rows_;
  std::map<unsigned,uint64_t> owners_,peaks_;
  std::vector<Event> events_;
};
}
