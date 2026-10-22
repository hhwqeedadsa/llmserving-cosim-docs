#include "rdma/remote_memory.hh"
#include <iostream>
#include <string>
using namespace moe_rdma;
static unsigned checks=0;
static void Check(bool ok){++checks;if(!ok)throw std::runtime_error("independent memory assertion failed");}
template<class F>static void Reject(F f){bool rejected=false;try{f();}catch(const std::exception&){rejected=true;}Check(rejected);}
int main(){
  MemoryFrame add;add.verb=MemoryFrame::FetchAdd;add.key=7;add.address=4096;add.body=MemoryInteger(3);
  // Independent canonical wire oracle, without the production decoder.
  const std::vector<uint8_t> expected={0x4d,0x52,0x54,0x31,0,0,0,2,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0x10,0,0,0,0,7,0,0,0,8,0,0,0,0,0,0,0,3};
  Check(EncodeMemoryFrame(add)==expected);
  // Every two-piece split, and every fixed packet size. Neither source Work
  // boundaries nor one-packet transaction assumptions enter the decoder.
  for(unsigned split=0;split<=expected.size();++split){
    MemoryFrameDecoder decoder;unsigned applied=0;
    auto consume=[&](const MemoryFrame& f){++applied;Check(f.verb==2 && f.key==7 && f.address==4096 && f.body.back()==3);};
    decoder.Feed({expected.begin(),expected.begin()+split},consume);
    Check(applied==(split==expected.size()?1u:0u));
    decoder.Feed({expected.begin()+split,expected.end()},consume);Check(applied==1 && decoder.Empty());
  }
  auto doubled=expected;doubled.insert(doubled.end(),expected.begin(),expected.end());
  for(unsigned size=1;size<=80;++size){
    MemoryFrameDecoder decoder;unsigned parsed=0;
    for(unsigned begin=0;begin<doubled.size();begin+=size)
      decoder.Feed({doubled.begin()+begin,doubled.begin()+std::min<std::size_t>(begin+size,doubled.size())},
        [&](const MemoryFrame& f){++parsed;Check(f.body.size()==8);});
    Check(parsed==2 && decoder.Empty());
  }
  RemoteMemory memory;memory.Register({4,7,4096,true,std::vector<uint8_t>(64,0)});
  const auto first=memory.Apply(0,4,add);Check(!first.duplicate && first.atomic && first.old_value==0);
  const auto repeat=memory.Apply(0,4,add);Check(repeat.duplicate && repeat.old_value==0 && memory.Windows()[0].bytes[0]==3);
  auto conflicting=add;conflicting.body=MemoryInteger(9);Reject([&]{memory.Apply(0,4,conflicting);});
  auto gap=add;gap.sequence=2;Reject([&]{memory.Apply(0,4,gap);});
  auto next=add;next.sequence=1;next.body=MemoryInteger(UINT64_MAX);
  Check(memory.Apply(0,4,next).old_value==3 && memory.Windows()[0].bytes[0]==2);
  // A separate context reaches the same window with its own request sequence.
  add.body=MemoryInteger(UINT64_MAX);Check(memory.Apply(1,4,add).old_value==2 && memory.Windows()[0].bytes[0]==1);
  MemoryFrame put;put.verb=MemoryFrame::Put;put.key=7;put.address=4112;put.sequence=2;put.body={1,2,3,4,5};
  Check(!memory.Apply(0,4,put).atomic);
  for(unsigned i=0;i<64;++i)Check(memory.Windows()[0].bytes[i]==(i==0?1u:(i>=16 && i<21?i-15:0u)));
  // Invalid authority, bounds and alignment must leave target memory untouched.
  const auto saved=memory.Windows()[0].bytes;
  for(unsigned mode=0;mode<5;++mode){
    auto invalid=add;invalid.sequence=0;
    if(mode==0)invalid.key=8;
    if(mode==1)invalid.address=4097;
    if(mode==2)invalid.address=4153;
    if(mode==3)invalid.address=UINT64_MAX;
    Reject([&]{memory.Apply(2,mode==4?5:4,invalid);});Check(memory.Windows()[0].bytes==saved);
  }
  RemoteMemory denied;denied.Register({4,7,4096,false,std::vector<uint8_t>(16,0)});
  Reject([&]{denied.Apply(0,4,add);});
  Reject([&]{memory.Register({4,8,4104,true,std::vector<uint8_t>(16,0)});});
  Reject([&]{memory.Register({4,7,8192,true,std::vector<uint8_t>(16,0)});});
  for(unsigned mode=0;mode<4;++mode){
    auto bad=expected;
    if(mode==0)bad[0]=0;
    if(mode==1)bad[7]=4;
    if(mode==2)bad[31]=0;
    if(mode==3)bad[28]=1;
    unsigned calls=0;
    MemoryFrameDecoder decoder;Reject([&]{decoder.Feed(bad,[&](const MemoryFrame&){++calls;});});Check(calls==0);
  }
  std::cout<<"{\"passed\":true,\"checks\":"<<checks<<",\"network_execution\":false}\n";
}
