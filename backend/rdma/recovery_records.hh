#pragma once
#include "json/json.hpp"
#include "../retransmission_timer.hh"
#include <array>
#include <vector>

namespace moe_rdma {
// Passive bounded records only. This class owns no protocol state or callbacks.
class RecoveryRecords {
 public:
  using Json=nlohmann::json;
  explicit RecoveryRecords(uint64_t maximum,bool adaptive):maximum_(maximum),adaptive_(adaptive) {}
  void Source(int64_t now,unsigned code,unsigned node,uint64_t id,const moe_cosim::RetransmissionTimer& t) {
    ++observations_;if(Full())return;
    events_.push_back({now,code,node,static_cast<int64_t>(id),static_cast<int64_t>(t.Ack()),
      static_cast<int64_t>(t.High()),t.Retries(),t.Rto(),t.Deadline()});
  }
  void Receiver(const std::array<int64_t,8>& row) {++observations_;if(!Full())receiver_.push_back(row);}
  void Rtt(int64_t now,unsigned event,uint64_t id,uint64_t start,uint64_t end) {
    if(!adaptive_)return;
    ++observations_;if(!Full())rtt_.push_back({now,event,static_cast<int64_t>(id),static_cast<int64_t>(start),static_cast<int64_t>(end)});
  }
  Json Report() const {
    const auto saved=events_.size()+receiver_.size()+rtt_.size();
    Json r={{"events",events_},{"columns",{"time_ns","event","source","message_id","acknowledged_bytes","highest_sent_bytes","retries_since_progress","rto_ns","deadline_ns"}},
      {"event_codes",{{"timer_started",0},{"ack_progress",1},{"timeout_retransmit",2},{"complete",3},{"retry_exhausted",4},{"old_feedback",6}}},
      {"receiver_events",receiver_},{"receiver_columns",{"time_ns","receiver","sender_ipv4","receiver_ipv4","sender_port","receiver_port","ack_sequence","duplicate_offset"}},
      {"observations",observations_},{"complete",observations_==saved},{"omitted_events",observations_-saved}};
    if(adaptive_) {
      r["rtt_events"]=rtt_;r["rtt_columns"]={"time_ns","event","message_id","start_or_ack","end_bytes"};
      r["rtt_event_codes"]={{"source_packet",0},{"ack",1},{"nack",2},{"timeout",3}};
    }
    return r;
  }
 private:
  bool Full() const {return events_.size()+receiver_.size()+rtt_.size()>=maximum_;}
  uint64_t maximum_,observations_=0;
  bool adaptive_;
  std::vector<std::array<int64_t,9>> events_;
  std::vector<std::array<int64_t,8>> receiver_;
  std::vector<std::array<int64_t,5>> rtt_;
};
}
