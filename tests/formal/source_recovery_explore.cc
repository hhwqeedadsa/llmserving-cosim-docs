// Bounded exploration of the production pure protocol, not a replacement model.
// Domain: 2 byte messages, integer clocks 0..8, RTO 2..8, one retry;
// all source ranges, ACK/NACK prefixes, duplicate feedback and timer/ACK ties.
#include "source_recovery.hh"
#include <deque>
#include <iostream>
#include <sstream>
#include <unordered_set>
#include <stdexcept>

using moe_cosim::SourceRecovery;
struct Node {SourceRecovery protocol;int64_t now=0;};
void Check(bool v){if(!v)throw std::runtime_error("Protocol invariant counterexample");}
std::string Key(const Node& n) {
  const auto& t=n.protocol.Timer();std::ostringstream o;
  o<<n.now<<','<<t.Ack()<<','<<t.High()<<','<<t.Retries()<<','<<t.Rto()<<','<<t.Deadline()<<','<<t.Expiries()<<','<<t.TotalRetries()<<','<<t.Failed();
  if(n.protocol.Rtt()) {
    const auto& e=*n.protocol.Rtt();
    o<<','<<e.Samples()<<','<<e.DiscardedProbes()<<','<<e.Srtt()<<','<<e.Variance()<<','<<e.BaseRto()<<','<<e.Barrier()<<','<<e.ProbeEnd()<<','<<e.ProbeTime();
  }
  return o.str();
}
int main() {
  uint64_t all_states=0,all_edges=0,complete=0,failed=0,sampled=0,discarded=0;
  for(bool adaptive:{false,true}) {
    std::deque<Node> queue;std::unordered_set<std::string> seen;
    Node initial{SourceRecovery(2,2,8,1,adaptive?1:0,adaptive?1:0)};
    queue.push_back(initial);seen.insert(Key(initial));
    auto accept=[&](Node next,const Node& previous) {
      const auto& t=next.protocol.Timer();const auto& old=previous.protocol.Timer();
      Check(old.Ack()<=t.Ack() && t.Ack()<=t.High() && old.High()<=t.High() && t.High()<=2);
      Check(t.Retries()<=1 && t.Rto()>=(adaptive?1:2) && t.Rto()<=8);
      Check(t.Expiries()==t.TotalRetries()+t.Failed());
      Check(!(t.Complete() && t.Failed()));
      if(t.Complete() || t.Failed())Check(t.Deadline()==-1);
      else Check((t.High()>t.Ack())==(t.Deadline()>=0));
      if(next.protocol.Rtt()) {
        const auto& e=*next.protocol.Rtt();Check(e.Barrier()<=t.High() && e.ProbeEnd()<=t.High());
        if(e.ProbeTime()>=0)Check(e.ProbeEnd()>t.Ack() && e.ProbeTime()<=next.now && t.Ack()>=e.Barrier());
        sampled+=e.Samples()>0;discarded+=e.DiscardedProbes()>0;
      }
      ++all_edges;
      if(seen.insert(Key(next)).second)queue.push_back(std::move(next));
      if(seen.size()>200000 || all_edges>4000000)throw std::runtime_error("Fixed exploration budget exceeded");
    };
    while(!queue.empty()) {
      const auto current=queue.front();queue.pop_front();const auto& t=current.protocol.Timer();
      if(t.Complete()){++complete;continue;}if(t.Failed()){++failed;continue;}
      const auto stop=t.Deadline()<0?8:std::min<int64_t>(8,t.Deadline());
      for(auto now=current.now;now<=stop;++now) {
        for(uint64_t start=0;start<=t.High();++start)for(uint64_t end=start+1;end<=2;++end) {
          auto next=current;next.now=now;next.protocol.Sent(start,end,now);accept(std::move(next),current);
        }
        for(uint64_t ack=0;ack<=t.High();++ack)for(bool nack:{false,true}) {
          // Positive propagation excludes an RTT probe acknowledged at send time.
          if(!nack && current.protocol.Rtt() && current.protocol.Rtt()->ProbeTime()==now &&
             ack>=current.protocol.Rtt()->ProbeEnd() && ack>t.Ack())continue;
          auto next=current;next.now=now;next.protocol.Confirm(ack,now,nack);
          if(ack<=t.Ack())Check(next.protocol.Timer().Deadline()==t.Deadline() && next.protocol.Timer().Rto()==t.Rto());
          accept(std::move(next),current);
        }
        if(now==t.Deadline()) {
          auto next=current;next.now=now;next.protocol.Expire(now);
          Check(next.protocol.Timer().Ack()==t.Ack());accept(std::move(next),current);
        }
      }
    }
    all_states+=seen.size();
  }
  Check(complete>0 && failed>0 && sampled>0 && discarded>0);
  std::cout<<"{\"passed\":true,\"states\":"<<all_states<<",\"transitions\":"<<all_edges
    <<",\"terminal_complete_states\":"<<complete<<",\"terminal_failed_states\":"<<failed
    <<",\"scope\":\"bounded production-core exploration, not unbounded proof or native NIC verification\"}\n";
}
