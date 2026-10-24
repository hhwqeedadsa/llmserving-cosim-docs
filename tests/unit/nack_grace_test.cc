#include "nack_grace.hh"
#include <algorithm>
#include <array>
#include <iostream>
#include <stdexcept>
#include <vector>

// Independent event-list oracle: locate the most recent advancing feedback;
// first NACK for its prefix sets the due time unless already serviced. It
// recomputes from history instead of sharing the production state transitions.
struct Event {int kind;int64_t now;uint64_t prefix;bool nack;};
struct State {uint64_t prefix=0;int64_t due=-1;bool serviced=false;};
State Oracle(const std::vector<Event>& history) {
  State x;std::size_t epoch=0;
  for(std::size_t i=0;i<history.size();++i)if(history[i].kind==0 && history[i].prefix>x.prefix) {
    x.prefix=history[i].prefix;epoch=i;
  }
  for(std::size_t i=epoch;i<history.size();++i) {
    const auto& e=history[i];
    if(e.kind==1 && x.due>=0 && e.now>=x.due){x.due=-1;x.serviced=true;}
    if(e.kind==2){x.due=-1;x.serviced=true;}
    if(e.kind==3)x.due=-1;
    if(e.kind==0 && e.nack && e.prefix==x.prefix && !x.serviced && x.due<0)x.due=e.now+7;
  }
  return x;
}
int main() {
  uint64_t checks=0;
  const std::array<Event,9> options={{{0,0,0,true},{0,0,0,false},{0,0,1,true},
    {0,0,1,false},{0,0,2,true},{0,0,2,false},{1,0,0,false},{2,0,0,false},{3,0,0,false}}};
  // Enumerate interleaved stale feedback/progress/expiry/RTO/stop and several
  // spacings, including exact timer deadlines and later callbacks.
  for(int spacing:{1,7,11})for(unsigned code=0;code<531441;++code) {
    moe_cosim::NackGrace g(7);std::vector<Event> history;unsigned value=code;
    for(int i=0;i<6;++i) {
      auto e=options[value%9];value/=9;e.now=i*spacing;history.push_back(e);
      if(e.kind==0)g.Feedback(e.now,e.prefix,e.nack);
      if(e.kind==1) {
        const auto previous=Oracle(std::vector<Event>(history.begin(),history.end()-1));
        const bool wanted=previous.due>=0 && e.now>=previous.due;
        if(g.Expire(e.now)!=wanted)throw std::runtime_error("wrong expiry decision");
      }
      if(e.kind==2)g.Rto();
      if(e.kind==3)g.Cancel();
      const auto x=Oracle(history);
      if(g.Prefix()!=x.prefix || g.Deadline()!=x.due || g.Serviced()!=x.serviced)
        throw std::runtime_error("history oracle mismatch");
      ++checks;
    }
  }
  for(int64_t bad:{int64_t(0),int64_t(-1),int64_t(1000000001)}) {
    bool rejected=false;try {moe_cosim::NackGrace g(bad);}catch(const std::invalid_argument&){rejected=true;}
    if(!rejected)throw std::runtime_error("invalid wait accepted");
    ++checks;
  }
  moe_cosim::NackGrace g(7);
  for(int64_t bad:{int64_t(-1),std::numeric_limits<int64_t>::max()}) {
    bool rejected=false;try {g.Feedback(bad,0,true);}catch(const std::invalid_argument&){rejected=true;}
    if(!rejected)throw std::runtime_error("invalid clock accepted");
    ++checks;
  }
  std::cout<<checks<<" independent NACK grace checks\n";
}
