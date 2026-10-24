#include "selective_repeat.hh"
#include <algorithm>
#include <array>
#include <cassert>
#include <iostream>
#include <set>
#include <vector>
using namespace moe_cosim;
static unsigned checks=0;
static void Check(bool value) {++checks;if(!value)throw std::runtime_error("independent selective oracle mismatch");}
int main() {
  // A byte-set oracle has no range/merge implementation in common with the adapter.
  std::array<unsigned,6> order={0,1,2,3,4,5};
  do {
    for(unsigned capacity=1;capacity<=6;++capacity) {
      SelectiveReceiver receiver(capacity);SelectiveSender sender;
      std::set<unsigned> held,known;unsigned prefix=0;
      std::vector<unsigned> arrivals(order.begin(),order.end());
      arrivals.insert(arrivals.end(),order.begin(),order.end());
      for(unsigned byte:arrivals) {
        bool accepted=true;
        if(byte==prefix) {++prefix;while(held.erase(prefix))++prefix;}
        else if(byte>prefix && !held.count(byte)) {
          if(held.size()==capacity)accepted=false;else held.insert(byte);
        }
        auto ack=receiver.Receive(byte,byte+1);
        Check(ack.received==accepted);Check(receiver.Prefix()==prefix);Check(receiver.Used()==held.size());
        auto wire=ack.Encode();auto parsed=SelectiveAck::Decode(wire);
        Check(parsed.received==accepted && parsed.begin==ack.begin && parsed.end==ack.end);
        sender.Feedback(prefix,parsed,6);
        for(unsigned b=0;b<prefix;++b)known.insert(b);
        if(accepted)known.insert(byte);
        for(unsigned cursor=0;cursor<=6;++cursor) {
          unsigned next=std::max(cursor,prefix);while(known.count(next))++next;
          Check(sender.Skip(cursor)==next);
        }
      }
      Check(receiver.Peak()<=capacity);
    }
  } while(std::next_permutation(order.begin(),order.end()));
  SelectiveSender sender;
  Check(sender.FastRetry(0));Check(!sender.FastRetry(0));
  sender.Feedback(3,SelectiveAck{1,2,true},6);Check(sender.FastRetry(3));Check(!sender.FastRetry(0));
  unsigned rejected=0;
  for(auto ack:std::vector<SelectiveAck>{{0,8,true},{3,4,true},{0,4,true}}) {
    try {sender.Feedback(3,ack,6);}catch(const std::invalid_argument&){++rejected;}
  }
  Check(rejected==3);
  for(unsigned which=0;which<4;++which) {
    auto bytes=SelectiveAck{1,2,true}.Encode();
    if(which==0)bytes[0]=0;
    if(which==1)bytes[15]=2;
    if(which==2)bytes[11]=1;
    if(which==3)bytes[15]=0;
    bool failed=false;try{SelectiveAck::Decode(bytes);}catch(const std::invalid_argument&){failed=true;}
    Check(failed);
  }
  SelectiveReceiver r(8);r.Receive(4,8);bool failed=false;
  try{r.Receive(5,7);}catch(const std::invalid_argument&){failed=true;}Check(failed);
  SelectiveReceiver bounded(8192);
  for(unsigned i=1;i<=4096;++i)Check(bounded.Receive(i,i+1).received);
  Check(!bounded.Receive(4097,4098).received);Check(bounded.Used()==4096);
  Check(bounded.Receive(0,1).received);Check(bounded.Prefix()==4097 && bounded.Used()==0);
  Check(bounded.Receive(4097,4098).received);
  std::cout<<"{\"checks\":"<<checks<<",\"permutations\":720,\"capacities\":6,\"passed\":true}\n";
}
