#pragma once
#include "json/json.hpp"
#include "ns3/rdma-hw.h"
#include "../source_recovery.hh"
#include "../recovery_history.hh"
#include "recovery_records.hh"
#include <array>
#include <functional>
#include <map>
#include <memory>
#include <vector>

namespace moe_rdma {
// Opt-in protocol extension at native NIC callbacks, separate from observers.
class RdmaRecovery {
 public:
  using Json=nlohmann::json;
  explicit RdmaRecovery(const Json&,std::function<void(uint64_t)> failed);
  ~RdmaRecovery();
  void Attach(unsigned node,ns3::Ptr<ns3::RdmaHw> hw);
  bool Idle() const;
  bool Failed() const {return failed_;}
  Json Report() const;
 private:
  struct State {
    State(unsigned n,ns3::Ptr<ns3::RdmaHw> h,ns3::Ptr<ns3::RdmaQueuePair> q,
          int64_t initial,int64_t maximum,unsigned retries,int64_t minimum,int64_t granularity):
          node(n),hw(h),qp(q),protocol(q->m_size,initial,maximum,retries,minimum,granularity),
          seed_rto(initial),history_key(n,q->dip.Get()) {}
    unsigned node;
    ns3::Ptr<ns3::RdmaHw> hw;
    ns3::Ptr<ns3::RdmaQueuePair> qp;
    moe_cosim::SourceRecovery protocol;
    int64_t seed_rto;
    bool history_hit=false;
    moe_cosim::RecoveryHistory::Key history_key;
    ns3::EventId event;
  };
  static void Sent(RdmaRecovery*,unsigned,ns3::RdmaHw*,ns3::Ptr<ns3::RdmaQueuePair>,ns3::Ptr<ns3::Packet>,ns3::Time);
  static int Receive(RdmaRecovery*,unsigned,ns3::RdmaHw*,ns3::Ptr<ns3::Packet>,ns3::CustomHeader&);
  void Arm(uint64_t,State&);
  void Fire(uint64_t);
  void Event(unsigned,uint64_t,const State&);
  void DuplicateAck(unsigned,ns3::Ptr<ns3::RdmaHw>,ns3::CustomHeader&,ns3::Ptr<ns3::RdmaRxQueuePair>);
  moe_cosim::RecoveryHistory* History(unsigned node);
  Json options_;
  int64_t initial_,maximum_,minimum_=0,granularity_=0;
  unsigned limit_;
  uint64_t duplicate_acks_=0,old_feedback_=0;
  RecoveryRecords records_;
  std::map<unsigned,std::unique_ptr<moe_cosim::RecoveryHistory>> histories_;
  bool failed_=false;
  std::function<void(uint64_t)> failure_;
  std::map<uint64_t,std::unique_ptr<State>> states_;
  // Own the callback targets, but never bind an owning Ptr into their devices:
  // RdmaHw owns its NICs, so that would form a reference cycle.
  std::vector<ns3::Ptr<ns3::RdmaHw>> attached_;
};
}
