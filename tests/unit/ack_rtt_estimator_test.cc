#include "source_recovery.hh"
#include <stdexcept>
using moe_cosim::AckRttEstimator;
using moe_cosim::SourceRecovery;
using moe_cosim::RetransmissionTimer;
void Check(bool v){if(!v)throw std::runtime_error("RTT/protocol property failed");}
template<class F>void Reject(F f){bool yes=false;try{f();}catch(const std::exception&){yes=true;}Check(yes);}
int main(){
  AckRttEstimator e(40,5,160,1);
  e.Sent(0,1000,0);e.Sent(1000,2000,1);
  Check(e.Confirm(1000,10)==10 && e.Samples()==1 && e.Srtt()==10 && e.Variance()==5 && e.BaseRto()==30);
  e.Sent(2000,3000,11);Check(e.Confirm(3000,25)==14 && e.Srtt()==11 && e.Variance()==5 && e.BaseRto()==31);
  Check(!e.Confirm(1000,26) && e.Samples()==2 && e.BaseRto()==31);
  AckRttEstimator k(40,5,160,1);
  k.Sent(0,1,0);k.Confirm(0,1,true);k.Sent(0,1,2);
  Check(k.Confirm(1,3)==0 && k.Samples()==0 && k.DiscardedProbes()==1);
  k.Sent(1,2,4);Check(k.Confirm(2,14)==10 && k.Samples()==1);
  k.Sent(2,3,15);k.Sent(3,4,16);k.Sent(2,3,17);k.Sent(4,5,18);
  Check(k.Confirm(3,20)==0 && k.Confirm(4,21)==0 && k.Confirm(5,22)==0 && k.Samples()==1);
  k.Sent(5,6,23);Check(k.Confirm(6,33)==10 && k.Samples()==2);
  AckRttEstimator floor(100,50,120,1);floor.Sent(0,1,0);floor.Confirm(1,2);Check(floor.BaseRto()==50);
  floor.Sent(1,2,3);floor.Confirm(2,203);Check(floor.BaseRto()==120);
  SourceRecovery flow(6,40,160,2,5,1);
  flow.Sent(0,2,0);flow.Sent(2,4,1);flow.Confirm(2,10);
  Check(flow.Timer().Rto()==30 && flow.Timer().Deadline()==40);
  Check(flow.Expire(40)==RetransmissionTimer::Expiry::Retry && flow.Timer().Rto()==60);
  flow.Sent(2,4,40);flow.Sent(4,6,41);flow.Confirm(4,52);flow.Confirm(6,53);
  Check(flow.Timer().Complete() && flow.Timer().Deadline()==-1 && flow.Rtt()->Samples()==1);
  SourceRecovery bootstrap(1,2,8,2,1,1);bootstrap.Sent(0,1,0);bootstrap.Expire(2);bootstrap.Confirm(1,5);
  Check(bootstrap.Timer().Complete() && bootstrap.Rtt()->Samples()==0);
  Reject([]{AckRttEstimator bad(1,2,3,1);});Reject([]{AckRttEstimator bad(2,1,3,0);});
  Reject([]{AckRttEstimator bad(2,1,3,4);});
  AckRttEstimator invalid(2,1,8,1);Reject([&]{invalid.Confirm(1,0);});
  invalid.Sent(0,1,1);Reject([&]{invalid.Confirm(1,0);});Reject([&]{invalid.Sent(2,3,2);});
}
