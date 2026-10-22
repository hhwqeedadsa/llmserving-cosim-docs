#include "recovery_history.hh"
#include "source_recovery.hh"
#include <stdexcept>
using moe_cosim::RecoveryHistory;
void Check(bool v){if(!v)throw std::runtime_error("Recovery history property failed");}
template<class F>void Reject(F f){bool yes=false;try{f();}catch(const std::exception&){yes=true;}Check(yes);}
int main(){
  RecoveryHistory h(2,100,160);
  auto cold=h.Start({0,10},0,2);Check(!cold.hit && cold.rto==2);
  h.Observe({0,10},2,4);h.Observe({0,10},6,8);
  auto warm=h.Start({0,10},7,2);Check(warm.hit && warm.rto==8);
  h.Observe({0,10},8,3);Check(h.Start({0,10},9,2).rto==8); // Conservative maximum, not a fake RTT.
  Check(!h.Start({1,10},9,2).hit && !h.Start({0,11},9,2).hit);
  h.Observe({0,11},10,12);h.Start({0,10},11,2);h.Observe({0,12},12,16);
  Check(h.Entries().size()==2 && !h.Entries().count({0,11}) && h.Evictions()==1);
  Check(h.Start({0,10},107,2).hit); // Reading did not refresh the evidence age.
  Check(!h.Start({0,10},108,2).hit && h.Expirations()==1);
  Check(!h.Start({0,12},112,2).hit && h.Entries().empty());
  Reject([&]{h.Start({0,10},111,2);});Reject([]{RecoveryHistory bad(0,1,10);});
  Reject([]{RecoveryHistory bad(257,1,10);});Reject([]{RecoveryHistory bad(1,0,10);});
  Reject([&]{h.Observe({0,10},113,161);});
  // A zero-sample short QP can seed later QPs from real timeout backoff.
  RecoveryHistory q(2,1000,64);
  moe_cosim::SourceRecovery first(1,2,64,8,1,1);first.Sent(0,1,0);
  first.Expire(2);q.Observe({0,10},2,first.Timer().Rto());
  first.Expire(6);q.Observe({0,10},6,first.Timer().Rto());first.Confirm(1,10);
  Check(first.Rtt()->Samples()==0 && q.Start({0,10},11,2).rto==8);
  // History only chooses the next seed: it never mutates an existing timer.
  Check(first.Timer().Rto()==2 && first.Timer().Complete());
}
