#include "communication_admission.hh"
#include <iostream>

using Queue=moe_cosim::CommunicationAdmission;
static void Check(bool value) {if(!value)throw std::runtime_error("Admission invariant failed");}
template<class F> static void Reject(F call) {
  bool rejected=false;try{call();}catch(const std::exception&){rejected=true;}Check(rejected);
}
int main() {
  Queue fifo("fifo",2,20);
  fifo.Request({8,3,1000,0},0);fifo.Request({2,1,1,0},0);fifo.Request({9,2,9000,1},1);
  Check(fifo.Take(1)->id==8);Check(fifo.Take(1)->id==2);Check(!fifo.Take(1));
  Check(fifo.Active()==std::set<uint64_t>({2,8}));Check(fifo.Pending()==1);
  Reject([&]{fifo.Complete(9,2);});Reject([&]{fifo.Request({8,1,1,0},2);});
  fifo.Complete(8,10);Check(fifo.Take(10)->id==9);Check(fifo.Active()==std::set<uint64_t>({2,9}));
  Reject([&]{fifo.Complete(8,10);});Reject([&]{fifo.Complete(2,9);});
  fifo.Complete(2,11);fifo.Complete(9,11);Check(fifo.Active().empty() && !fifo.Pending());
  Queue rr("destination_round_robin",1,20);
  // Multiple pending tasks to peer1 cannot hide peer2/peer3. Future work
  // is rejected; equal-time FIFO ties follow insertion, never identity order.
  for(const auto task:std::vector<Queue::Ready>{{5,1,9,0},{4,1,8,0},{3,1,7,0},{9,2,6,0},{1,3,5,0}})rr.Request(task,0);
  Reject([&]{rr.Request({22,2,1,5},0);});
  for(uint64_t id:{5,9,1,4,3}) {auto next=rr.Take(0);Check(next && next->id==id);rr.Complete(id,0);}
  Check(!rr.Pending() && rr.Active().empty());
  Queue pass("passthrough",0,3);
  for(uint64_t id:{9,5,2}) {pass.Request({id,1,1,0},0);Check(pass.Take(0)->id==id);}
  Check(pass.Active().size()==3);Reject([&]{pass.Request({1,1,1,0},0);});
  Reject([]{Queue bad("fifo",0,1);});Reject([]{Queue bad("passthrough",1,1);});
  // Equal count limits do not imply equal logical payload exposure. Strict FIFO
  // does not bypass its head; round-robin may serve another eligible peer head.
  for(const auto policy:{"fifo","destination_round_robin"}) {
    Queue bytes(policy,3,20,5000);
    bytes.Request({1,1,4000,0},0);Check(bytes.Take(0)->id==1);
    bytes.Request({2,1,2000,0},0);bytes.Request({3,2,1000,0},0);
    Check(bytes.Available() && bytes.ActiveBytes()==4000 && bytes.PendingBytes()==3000);
    if(std::string(policy)=="fifo")Check(!bytes.Eligible() && !bytes.Take(0));
    else {Check(bytes.Eligible() && bytes.Take(0)->id==3);Check(bytes.ActiveBytes()==5000);}
    bytes.Complete(1,10);Check(bytes.Take(10)->id==2);
    if(std::string(policy)=="fifo")Check(bytes.Take(10)->id==3);
    bytes.Complete(2,11);bytes.Complete(3,11);Check(!bytes.ActiveBytes() && !bytes.PendingBytes());
    Reject([&]{bytes.Request({4,2,5001,11},11);});
    bytes.Request({4,2,5000,11},11);Check(bytes.Take(11)->id==4);
  }
  Reject([]{Queue bad("passthrough",0,1,1);});
  // One bad destination cannot take the second slot/byte share; its FIFO head
  // remains pending while another destination runs. No fault-owned release.
  Queue protected_rr("destination_round_robin",3,20,8192,1,4096);
  protected_rr.Request({1,4,4096,0},0);Check(protected_rr.Take(0)->id==1);
  protected_rr.Request({2,4,4096,0},0);protected_rr.Request({3,6,4096,0},0);
  Check(protected_rr.Take(0)->id==3);Check(!protected_rr.Take(0));
  Check(protected_rr.ActiveBytes()==8192 && protected_rr.PendingIds()==std::vector<uint64_t>({2}));
  protected_rr.Complete(3,1);Check(!protected_rr.Eligible()); // Peer4 still owns its share.
  protected_rr.Complete(1,2);Check(protected_rr.Take(2)->id==2);protected_rr.Complete(2,3);
  Check(!protected_rr.ActiveBytes() && protected_rr.Active().empty());
  Reject([&]{protected_rr.Request({4,4,4097,3},3);});
  for(const auto policy:{"fifo","passthrough"})Reject([&]{Queue bad(policy,3,20,8192,1,4096);});
  Reject([]{Queue bad("destination_round_robin",3,20,8192,3,4096);});
  Reject([]{Queue bad("destination_round_robin",3,20,8192,1,8192);});
  Reject([]{Queue bad("destination_round_robin",3,20,8192,0,4096);});
  // Byte share can bind before peer slots, including heterogeneous sizes.
  Queue peer_bytes("destination_round_robin",4,20,100,2,60);
  for(auto task:std::vector<Queue::Ready>{{1,1,40,0},{2,1,30,0},{3,2,40,0}})peer_bytes.Request(task,0);
  Check(peer_bytes.Take(0)->id==1);Check(peer_bytes.Take(0)->id==3);Check(!peer_bytes.Take(0));
  peer_bytes.Complete(1,1);Check(peer_bytes.Take(1)->id==2);
  std::cout<<"FIFO/round-robin capacity, ownership, ordering and causal input checks passed\n";
}
