#include "source_recovery.hh"
#include <iostream>
#include <stdexcept>
using moe_cosim::SourceRecovery;
static unsigned checks=0;
void check(bool v){++checks;if(!v)throw std::runtime_error("persistent recovery invariant");}
template<class F>void rejects(F f){bool caught=false;try{f();}catch(const std::logic_error&){caught=true;}check(caught);}
int main(){
 SourceRecovery r(100,20,160,2);
 r.Sent(0,100,1);check(r.Timer().Deadline()==21);r.Extend(200);check(r.Timer().Deadline()==21);
 rejects([&]{r.Confirm(150,2);});check(r.Timer().Ack()==0);
 r.Confirm(100,5);check(r.Timer().Deadline()==-1 && !r.Timer().Complete());
 r.Sent(100,200,100);check(r.Timer().Deadline()==120);r.Confirm(200,110);check(r.Timer().Complete());
 r.Extend(200);check(r.Timer().Complete());r.Extend(300);check(!r.Timer().Complete() && r.Timer().Deadline()==-1);
 r.Sent(200,300,1000);check(r.Timer().Deadline()==1020);r.Expire(1020);check(r.Timer().Rto()==40);
 // True ACK after a rewind is bounded by emitted high-water, not NIC cursor.
 r.Confirm(300,1021);check(r.Timer().Complete() && r.Timer().Deadline()==-1);
 r.Confirm(100,1022);check(r.Timer().Ack()==300);rejects([&]{r.Extend(299);});
 r.Extend(400);r.Sent(300,400,1100);r.Confirm(300,1110);check(r.Timer().Deadline()==1120);
 r.Expire(1120);r.Expire(1160);check(r.Expire(1240)==moe_cosim::RetransmissionTimer::Expiry::Exhausted);
 check(r.Timer().Failed());rejects([&]{r.Extend(500);});
 SourceRecovery k(100,80,640,3,20,1);k.Sent(0,100,0);k.Confirm(100,10);check(k.Rtt()->Samples()==1);
 k.Extend(200);k.Sent(100,200,1000);k.Confirm(200,1010);check(k.Rtt()->Samples()==2 && k.Timer().Complete());
 std::cout<<checks<<" persistent prefix and source-only recovery invariants\n";
}
