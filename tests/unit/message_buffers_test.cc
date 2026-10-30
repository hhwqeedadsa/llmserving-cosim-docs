#include "rdma/message_buffers.hh"
#include <iostream>
using namespace moe_rdma;
static unsigned checks=0;
static void Check(bool ok){++checks;if(!ok)throw std::runtime_error("message buffer assertion failed");}
template<class F>static void Reject(F f){bool rejected=false;try{f();}catch(const std::invalid_argument&){rejected=true;}Check(rejected);}
int main() {
  MessageBuffers m(64,256,8,80);
  m.Post(1,0,2,16,0);m.Post(2,1,2,16,1);
  Check(m.Get(1).target_address!=m.Get(2).target_address);
  const auto newer=m.Source(2);Check(m.Write(2,newer,2));m.FinishScope(2,3);
  Check(m.Get(2).target_live && m.Get(2).source_live); // application done, ACK missing
  m.Post(3,3,2,16,4);Check(m.Get(3).target_address==32);
  const auto old=m.Source(1);Check(!m.Write(1,std::vector<uint8_t>(old.begin(),old.begin()+8),5));
  Reject([&]{m.FinishScope(1,5);});Check(m.Get(2).target==newer);
  Check(m.Write(1,std::vector<uint8_t>(old.begin()+8,old.end()),6));Check(m.Get(2).target==newer);
  Reject([&]{m.Write(1,old,6);});Check(m.Get(1).received==16);
  m.Ack(2,7);Check(!m.Get(2).source_live && !m.Get(2).target_live && m.Get(2).target_released_ns==7);
  m.Post(4,4,2,16,8);Check(m.Get(4).target_address==16); // reuse only after ACK+scope
  Reject([&]{m.Write(2,newer,9);});Check(m.Get(4).received==0 && m.Get(4).target==std::vector<uint8_t>(16,0));
  m.Ack(1,9);Check(m.Get(1).target_live);m.FinishScope(1,10);
  Check(!m.Get(1).target_live && m.Get(1).target_digest==m.Get(1).source_digest);
  Reject([&]{m.Ack(1,10);});Reject([&]{m.FinishScope(1,10);});Reject([&]{m.Post(1,0,2,16,10);});
  for(auto id:{3,4}) {const auto body=m.Source(id);const auto now=11+3*(id-3);
    Check(m.Write(id,body,now));m.Ack(id,now+1);m.FinishScope(id,now+2);}
  Check(m.Used()==0 && m.Peak()<=256);
  for(const auto& [id,row]:m.Rows())Check(row.source.empty() && row.target.empty() && row.target_digest==row.source_digest);
  const auto count=m.Events().size();Reject([&]{m.Post(5,0,2,16,1);});Check(m.Events().size()==count);
  MessageBuffers tiny(16,32,2,14);tiny.Post(0,0,2,16,0);
  Reject([&]{tiny.Post(1,1,2,16,0);});Check(tiny.Rows().size()==1 && tiny.Used()==32 && tiny.Events().size()==2);
  Reject([&]{tiny.Post(1,1,3,17,0);});Reject([&]{tiny.Write(0,std::vector<uint8_t>(17,0),1);});
  Check(tiny.Get(0).received==0);
  // Source and target are one address namespace, even across opposite directions.
  MessageBuffers mixed(32,128,4,40);mixed.Post(0,0,1,16,0);mixed.Post(1,2,0,16,0);
  Check(mixed.Get(0).source_address==0 && mixed.Get(1).target_address==16);
  Reject([&]{mixed.Post(2,3,0,1,0);});Check(mixed.Rows().size()==2);
  Reject([&]{mixed.Ack(99,0);});Reject([&]{mixed.Post(2,0,0,1,0);});
  // Explicit event budget failure is checked before modifying target contents.
  MessageBuffers events(64,128,1,7);events.Post(0,0,1,4,0);
  for(int i=0;i<3;++i)Check(!events.Write(0,{uint8_t(i)},i));
  Check(events.Write(0,{3},3)); // 7 events now, no room for ACK
  Reject([&]{events.Ack(0,4);});Check(events.Get(0).source_live && events.Get(0).ack_ns<0);
  std::cout<<checks<<" independent byte/range/lifetime assertions passed\n";
}
