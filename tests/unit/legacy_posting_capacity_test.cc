#include "rdma/legacy_posting_admission.hh"
#include "rdma/low_latency_posting.hh"
#include <iostream>
#include <stdexcept>
#include <vector>

using moe_rdma::LegacyPostingAdmission;
using moe_rdma::LegacyUsesPostingQueue;
using moe_rdma::LowLatencyPostingQueue;
static unsigned checks=0,scenarios=0;
static void Check(bool condition) {
  ++checks;if(!condition)throw std::logic_error("legacy posting admission assertion "+std::to_string(checks));
}

// The real queue core supplies readiness/publication/credit checks. Only this
// CPU harness supplies explicit synthetic completions after NIC consumption;
// it is not a network execution or a hardware progress model.
static bool Execute(unsigned tokens,unsigned rounds,unsigned depth,bool delayed) {
  LowLatencyPostingQueue queue(depth,256);
  std::vector<LowLatencyPostingQueue::Operation> operations;
  for(unsigned round=0;round<rounds;++round) {
    for(unsigned i=0;i<tokens;++i)operations.push_back(queue.ReservePut(operations.size(),i,{272}));
    operations.push_back(queue.ReserveNotification(operations.size()));
  }
  while(true) {
    bool changed=false;
    for(const auto& op:operations) {
      for(auto ticket=op.begin;ticket<op.end;++ticket)
        if(queue.CanPrepare(ticket)){queue.Prepare(ticket);changed=true;}
      if(queue.CanPost(op.id)){queue.Post(op.id);changed=true;}
      if(!delayed) {
        while(const auto entry=queue.ConsumePublished()) {queue.CompleteThrough(entry->ticket+1);changed=true;}
      }
    }
    if(delayed)while(const auto entry=queue.ConsumePublished()){queue.CompleteThrough(entry->ticket+1);changed=true;}
    if(!changed)break;
  }
  if(queue.Drained()){queue.Close();return true;}
  const auto state=queue.Snapshot();Check(state.reserved>state.completed);
  Check((state.prepared_prefix==state.published && state.consumed==state.completed) ||
        state.prepared_prefix>state.published);
  return false;
}

int main() {
  Check(!LegacyUsesPostingQueue(0,0) && LegacyUsesPostingQueue(0,1));
  LegacyPostingAdmission local;
  for(unsigned i=0;i<16;++i)local.Observe(0,0,0,false,i);
  Check(local.MinimumDepth()==1 && local.AllPublished());
  for(unsigned tokens=0;tokens<=16;++tokens)for(unsigned rounds=1;rounds<=4;++rounds) {
    LegacyPostingAdmission admission;
    for(unsigned round=0;round<rounds;++round) {
      for(unsigned i=0;i<tokens;++i)admission.Observe(0,0,1,false,i);
      admission.Observe(0,0,1,true,0);
    }
    // Independent batching oracle: groups of four, plus the tail notification.
    const unsigned expected=tokens>=4?4:tokens+1;
    Check(admission.MinimumDepth()==expected && admission.AllPublished());
    for(const unsigned depth:{1u,2u,4u,8u,16u,32u})for(const bool delayed:{false,true}) {
      ++scenarios;Check(Execute(tokens,rounds,depth,delayed)==(depth>=expected));
    }
  }
  LegacyPostingAdmission distinct;
  distinct.Observe(0,0,1,false,0);distinct.Observe(1,0,1,false,0);
  distinct.Observe(0,0,1,true,0);distinct.Observe(1,0,1,true,0);
  Check(distinct.MinimumDepth()==2 && distinct.AllPublished());
  LegacyPostingAdmission tail;tail.Observe(0,0,1,false,0);Check(!tail.AllPublished());
  std::cout<<"{\"passed\":true,\"checks\":"<<checks<<",\"scenarios\":"<<scenarios
           <<",\"network_executed\":false,\"completions\":\"explicit synthetic callbacks after consumption\"}\n";
}
