// Native ns-3 event-order unit checks. Synthetic source feedback, no Fabric,
// packets, or claim of an integrated PersistentStreams same-ns wire execution.
#include "nack_grace.hh"
#include "source_recovery.hh"
#include "ns3/simulator.h"
#include "ns3/nstime.h"
#include <iostream>
#include <stdexcept>

using namespace ns3;
using namespace moe_cosim;
struct Source {
  NackGrace grace{7};
  SourceRecovery recovery;
  EventId grace_event,rto_event;
  unsigned retries=0,rto_calls=0,grace_calls=0,yields=0;
  explicit Source(int64_t rto):recovery(100,rto,100,3){recovery.Sent(0,100,0);}
  int64_t Now(){return Simulator::Now().GetNanoSeconds();}
  void ArmRto(){rto_event=Simulator::Schedule(NanoSeconds(recovery.Timer().Deadline()-Now()),&Source::Rto,this);}
  void Nack(){
    grace.Feedback(Now(),0,true);
    if(grace.Deadline()>=0 && !grace_event.IsRunning())
      grace_event=Simulator::Schedule(NanoSeconds(grace.Deadline()-Now()),&Source::Grace,this);
  }
  void Grace(){
    ++grace_calls;
    if(recovery.Timer().Deadline()>=0 && recovery.Timer().Deadline()<=Now()){
      grace.Rto();++yields;return;
    }
    if(!grace.Expire(Now()))throw std::runtime_error("unexpected grace callback");
    ++retries;
  }
  void Rto(){
    if(recovery.Expire(Now())!=RetransmissionTimer::Expiry::Retry)throw std::runtime_error("unexpected RTO");
    ++rto_calls;Simulator::Cancel(grace_event);grace.Rto();
  }
  void Ack(){
    recovery.Confirm(100,Now());grace.Feedback(Now(),100,false);
    Simulator::Cancel(grace_event);Simulator::Cancel(rto_event);
  }
  void Stop(){Simulator::Cancel(grace_event);Simulator::Cancel(rto_event);grace.Cancel();}
};
int main(){
  Time::SetResolution(Time::NS);unsigned checks=0;
  auto check=[&](bool v){++checks;if(!v)throw std::runtime_error("native event ordering invariant");};
  {
    Source x(10);x.ArmRto();Simulator::Schedule(NanoSeconds(3),&Source::Nack,&x);
    Simulator::Run();check(x.rto_calls==1 && x.grace_calls==0 && !x.retries && x.grace.Deadline()==-1);
    Simulator::Destroy();
  }
  {
    Source x(10);Simulator::Schedule(NanoSeconds(3),[&](){x.Nack();x.ArmRto();});
    Simulator::Run();check(x.grace_calls==1 && x.yields==1 && x.rto_calls==1 && !x.retries);
    Simulator::Destroy();
  }
  {
    Source x(10);Simulator::Schedule(NanoSeconds(10),&Source::Ack,&x);x.ArmRto();
    Simulator::Schedule(NanoSeconds(3),&Source::Nack,&x);Simulator::Run();
    check(!x.grace_calls && !x.rto_calls && !x.retries && x.grace.Prefix()==100 && x.recovery.Timer().Complete());
    Simulator::Destroy();
  }
  {
    Source x(10);Simulator::Schedule(NanoSeconds(3),[&](){x.Nack();
      Simulator::Schedule(NanoSeconds(7),&Source::Ack,&x);x.ArmRto();});
    Simulator::Run();check(x.grace_calls==1 && x.yields==1 && !x.rto_calls && !x.retries && x.grace.Prefix()==100);
    Simulator::Destroy();
  }
  {
    Source x(50);x.ArmRto();Simulator::Schedule(NanoSeconds(3),&Source::Nack,&x);
    Simulator::Schedule(NanoSeconds(9),&Source::Ack,&x);Simulator::Run();
    check(!x.grace_calls && !x.rto_calls && x.grace.Deadline()==-1);
    Simulator::Destroy();
  }
  {
    Source x(50);x.ArmRto();Simulator::Schedule(NanoSeconds(3),&Source::Nack,&x);
    Simulator::Schedule(NanoSeconds(11),&Source::Nack,&x);Simulator::Stop(NanoSeconds(12));Simulator::Run();
    check(x.grace_calls==1 && x.retries==1 && !x.rto_calls && x.grace.Serviced());x.Stop();Simulator::Destroy();
  }
  {
    Source x(10);x.ArmRto();Simulator::Schedule(NanoSeconds(3),&Source::Nack,&x);
    Simulator::Schedule(NanoSeconds(9),&Source::Stop,&x);Simulator::Run();
    check(!x.grace_calls && !x.rto_calls && x.grace.Deadline()==-1 && !x.recovery.Timer().Complete());
    Simulator::Destroy();
  }
  std::cout<<checks<<" native ns-3 source event-order cases; no packets or Fabric\n";
}
