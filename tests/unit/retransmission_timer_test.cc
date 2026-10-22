#include "retransmission_timer.hh"
#include <stdexcept>
using Timer=moe_cosim::RetransmissionTimer;
void Check(bool x){if(!x)throw std::runtime_error("Recovery timer invariant failed");}
template<class F>void Reject(F f){bool caught=false;try{f();}catch(const std::exception&){caught=true;}Check(caught);}
int main(){
  Timer t(4000,40,160,2);Check(t.Deadline()==-1);
  t.Sent(1000,10);Check(t.Deadline()==50);t.Sent(4000,11);Check(t.Deadline()==50);
  Check(t.Confirm(1000,20) && t.Deadline()==60);
  Check(!t.Confirm(1000,30) && t.Deadline()==60);
  Check(t.Expire(60)==Timer::Expiry::Retry && t.Rto()==80 && t.Deadline()==140);
  t.Sent(2000,61);Check(t.High()==4000 && t.Deadline()==140);
  Check(t.Confirm(3000,62) && t.Retries()==0 && t.Rto()==40 && t.Deadline()==102);
  Check(t.Confirm(4000,63) && t.Complete() && t.Deadline()==-1 && t.TotalRetries()==1);
  Reject([&]{t.Expire(102);});Reject([&]{t.Confirm(5000,103);});
  Timer failed(1000,10,20,2);failed.Sent(1000,0);
  Check(failed.Expire(10)==Timer::Expiry::Retry && failed.Deadline()==30);
  Check(failed.Expire(30)==Timer::Expiry::Retry && failed.Deadline()==50);
  Check(failed.Expire(50)==Timer::Expiry::Exhausted && failed.Failed() && !failed.Complete());
  Check(failed.TotalRetries()==2 && failed.Expiries()==3 && failed.Deadline()==-1);
  Reject([&]{failed.Confirm(1000,51);});
  Timer invalid(1000,10,20,1);Reject([&]{invalid.Confirm(1,0);});
  invalid.Sent(1000,2);Reject([&]{invalid.Expire(3);});Reject([&]{invalid.Sent(1000,1);});
  Reject([]{Timer bad(1000,0,20,1);});Reject([]{Timer bad(1000,20,10,1);});
}
