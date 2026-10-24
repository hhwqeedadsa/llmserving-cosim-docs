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
  RemoteMemory memory;memory.Register({4,7,4096,true,std::vector<uint8_t>(64,0),0,{}});
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
  RemoteMemory denied;denied.Register({4,7,4096,false,std::vector<uint8_t>(16,0),0,{}});
  Reject([&]{denied.Apply(0,4,add);});
  // Local LE64 accesses do not grant any other GPU authority. Local application
  // stores are distinct from remote atomic permission and protocol sequences.
  Check(memory.Load64(4,7,4096)==1);
  memory.Store64(4,7,4096,0xfedcba9876543210ULL);
  Check(memory.Load64(4,7,4096)==0xfedcba9876543210ULL);
  Check(memory.Windows()[0].bytes[0]==0x10 && memory.Windows()[0].bytes[7]==0xfe);
  denied.Store64(4,7,4096,9);Check(denied.Load64(4,7,4096)==9);
  for(unsigned mode=0;mode<5;++mode) {
    const auto address=mode==2?4097ULL:mode==3?4153ULL:mode==4?UINT64_MAX:4096ULL;
    const auto owner=mode==0?5u:4u,key=mode==1?8u:7u;
    Reject([&]{memory.Load64(owner,key,address);});
    Reject([&]{memory.Store64(owner,key,address,0);});
    Check(memory.Load64(4,7,4096)==0xfedcba9876543210ULL);
  }
  RemoteMemory consumed;consumed.Register({4,7,4096,true,std::vector<uint8_t>(8,0),0,{}});
  auto signal=add;signal.body=MemoryInteger(1);
  Check(consumed.Apply(0,4,signal).old_value==0);
  Check(consumed.Load64(4,7,4096)==1);consumed.Store64(4,7,4096,0);
  const auto replay=consumed.Apply(0,4,signal);
  Check(replay.duplicate && replay.old_value==0 && consumed.Load64(4,7,4096)==0);
  signal.sequence=1;
  Check(!consumed.Apply(0,4,signal).duplicate && consumed.Load64(4,7,4096)==1);
  Reject([&]{memory.Register({4,8,4104,true,std::vector<uint8_t>(16,0),0,{}});});
  Reject([&]{memory.Register({4,7,8192,true,std::vector<uint8_t>(16,0),0,{}});});
  for(unsigned mode=0;mode<4;++mode){
    auto bad=expected;
    if(mode==0)bad[0]=0;
    if(mode==1)bad[7]=4;
    if(mode==2)bad[31]=0;
    if(mode==3)bad[28]=1;
    unsigned calls=0;
    MemoryFrameDecoder decoder;Reject([&]{decoder.Feed(bad,[&](const MemoryFrame&){++calls;});});Check(calls==0);
  }
  // Enumerate every nonempty subrange against one read lease. Expected overlap
  // is an independent byte-index set intersection, including boundary touches.
  RemoteMemory guarded;guarded.Register({0,5,8192,true,std::vector<uint8_t>(64,7),0,{}});
  guarded.Register({1,5,8192,true,std::vector<uint8_t>(64,9),0,{}});
  guarded.AcquireRead(1,{0,5,8200,16});guarded.AcquireRead(2,{0,5,8200,16});
  for(unsigned begin=0;begin<64;++begin)for(unsigned length=1;length<=64-begin;++length) {
    bool overlap=false;for(unsigned i=begin;i<begin+length;++i)overlap=overlap || (i>=8 && i<24);
    bool rejected=false;try{guarded.CheckWritable(0,5,8192+begin,length);}catch(const std::exception&){rejected=true;}
    Check(rejected==overlap);guarded.CheckWritable(1,5,8192+begin,length);
  }
  Check(guarded.ReadBytes(0,5,8201,3)==std::vector<uint8_t>(3,7));
  for(unsigned mode=0;mode<5;++mode) {
    Reject([&]{guarded.AcquireRead(3,{mode==0?2u:0u,mode==1?6u:5u,mode==2?8191ULL:mode==3?UINT64_MAX:8192ULL,mode==4?0ULL:8ULL});});
    Check(guarded.ReadLeases().size()==2);
  }
  Reject([&]{guarded.AcquireRead(1,{0,5,8192,8});});
  auto before=guarded.Windows()[0].bytes;
  MemoryFrame overwrite;overwrite.verb=MemoryFrame::Put;overwrite.key=5;overwrite.address=8200;overwrite.body={1,2,3};
  Reject([&]{guarded.Apply(0,0,overwrite);});Check(guarded.Windows()[0].bytes==before);
  auto atomic=overwrite;atomic.verb=MemoryFrame::FetchAdd;atomic.body=MemoryInteger(1);
  Reject([&]{guarded.Apply(1,0,atomic);});Check(guarded.Windows()[0].bytes==before);
  Reject([&]{guarded.Store64(0,5,8200,0);});Check(guarded.Windows()[0].bytes==before);
  guarded.ReleaseRead(1);Reject([&]{guarded.Store64(0,5,8200,0);});
  guarded.ReleaseRead(2);guarded.Store64(0,5,8200,0);Check(guarded.Load64(0,5,8200)==0);
  Check(!guarded.Apply(1,0,atomic).duplicate && guarded.Load64(0,5,8200)==1);
  guarded.AcquireRead(4,{0,5,8200,8});
  Check(guarded.Apply(1,0,atomic).duplicate && guarded.Load64(0,5,8200)==1);
  guarded.ReleaseRead(4);Reject([&]{guarded.ReleaseRead(4);});
  RemoteMemory outputs;outputs.Register({0,5,8192,true,std::vector<uint8_t>(64,7),0,{}});
  outputs.Register({1,5,8192,true,std::vector<uint8_t>(64,9),0,{}});
  outputs.AcquireWrite(1,{0,5,8200,16});
  for(unsigned begin=0;begin<64;++begin)for(unsigned length=1;length<=64-begin;++length) {
    bool overlap=false;for(unsigned i=begin;i<begin+length;++i)overlap=overlap || (i>=8 && i<24);
    bool read=false,write=false;
    try{outputs.CheckReadable(0,5,8192+begin,length);}catch(const std::exception&){read=true;}
    try{outputs.CheckWritable(0,5,8192+begin,length);}catch(const std::exception&){write=true;}
    Check(read==overlap);Check(write==overlap);outputs.CheckReadable(1,5,8192+begin,length);
  }
  Reject([&]{outputs.AcquireRead(2,{0,5,8200,8});});
  Reject([&]{outputs.AcquireWrite(2,{0,5,8200,8});});
  Reject([&]{outputs.Load64(0,5,8200);});Reject([&]{outputs.ReadBytes(0,5,8200,8);});
  Reject([&]{outputs.Store64(0,5,8200,1);});
  overwrite.sequence=0;Reject([&]{outputs.Apply(0,0,overwrite);});
  Reject([&]{outputs.CommitWrite(9,std::vector<uint8_t>(16,3));});
  Reject([&]{outputs.CommitWrite(1,std::vector<uint8_t>(15,3));});
  Check(outputs.Windows()[0].bytes==std::vector<uint8_t>(64,7));
  outputs.CommitWrite(1,std::vector<uint8_t>(16,3));
  Reject([&]{outputs.ReadBytes(0,5,8200,8);}); // commit is not release
  outputs.ReleaseWrite(1);Check(outputs.ReadBytes(0,5,8200,16)==std::vector<uint8_t>(16,3));
  Reject([&]{outputs.ReleaseWrite(1);});
  Check(!outputs.Apply(0,0,overwrite).duplicate);
  outputs.AcquireWrite(2,{0,5,8200,16});Check(outputs.Apply(0,0,overwrite).duplicate);
  outputs.ReleaseWrite(2);outputs.AcquireRead(3,{0,5,8200,16});
  Reject([&]{outputs.AcquireWrite(4,{0,5,8200,16});});Check(outputs.WriteLeases().empty());
  outputs.ReleaseRead(3);outputs.AcquireWrite(4,{0,5,8200,16});outputs.ReleaseWrite(4);
  // Monotonic carrier PUT does not need a growing application replay cache.
  RemoteMemory once;once.ConfigureCarrierOncePut();
  once.Register({4,7,4096,true,std::vector<uint8_t>(16,0),0,{}});
  Reject([&]{once.ConfigureCarrierOncePut();});Reject([&]{once.ConfigureCacheBudget(40);});
  MemoryFrame monotonic;monotonic.verb=MemoryFrame::Put;monotonic.key=7;monotonic.address=4096;
  for(unsigned sequence=0;sequence<32;++sequence)for(unsigned context=0;context<2;++context) {
    monotonic.sequence=sequence;monotonic.body=std::vector<uint8_t>(8,sequence+1);monotonic.address=4096+context*8;
    Check(!once.Apply(context,4,monotonic).duplicate);
    const auto snapshot=once.Windows()[0].bytes;Reject([&]{once.Apply(context,4,monotonic);});
    Check(once.Windows()[0].bytes==snapshot && once.CacheBytes().empty());
    for(const auto& state:once.ReplayStates())Check(state.requests==0 && state.encoded_bytes==0 && state.next<=sequence+1);
  }
  Check(once.ReplayStates().size()==2 && once.ReplayStates()[0].next==32 && once.ReplayStates()[1].next==32);
  const auto once_bytes=once.Windows()[0].bytes;
  monotonic.sequence=32;Reject([&]{once.Apply(0,4,monotonic);});
  monotonic.sequence=1;Reject([&]{once.Apply(2,4,monotonic);}); // gap before any successful work
  monotonic.sequence=0;monotonic.verb=MemoryFrame::FetchAdd;monotonic.body=MemoryInteger(1);
  Reject([&]{once.Apply(3,4,monotonic);});
  Check(once.Windows()[0].bytes==once_bytes && once.CacheBytes().empty());
  RemoteMemory configured;configured.ConfigureCacheBudget(40);Reject([&]{configured.ConfigureCarrierOncePut();});
  std::cout<<"{\"passed\":true,\"checks\":"<<checks<<",\"network_execution\":false}\n";
}
