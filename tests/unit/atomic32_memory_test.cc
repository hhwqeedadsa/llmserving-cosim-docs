#include "rdma/remote_memory.hh"
#include <iostream>
#include <string>
using namespace moe_rdma;
static unsigned checks=0;
static void Check(bool ok){++checks;if(!ok)throw std::runtime_error("independent atomic32 oracle failed");}
template<class F>static void Reject(F f){bool rejected=false;try{f();}catch(const std::exception&){rejected=true;}Check(rejected);}
int main(){
  MemoryFrame add;add.verb=MemoryFrame::FetchAdd32;add.key=7;add.address=4100;add.body=MemoryInteger(UINT32_MAX,4);
  const std::vector<uint8_t> expected={0x4d,0x52,0x54,0x31,0,0,0,4,0,0,0,0,0,0,0,0,
    0,0,0,0,0,0,0x10,4,0,0,0,7,0,0,0,4,0xff,0xff,0xff,0xff};
  Check(EncodeMemoryFrame(add)==expected);
  for(unsigned split=0;split<=expected.size();++split){
    MemoryFrameDecoder decoder;unsigned count=0;
    auto accept=[&](const MemoryFrame& f){++count;Check(f.verb==4 && f.address==4100 && f.body==std::vector<uint8_t>(4,255));};
    decoder.Feed({expected.begin(),expected.begin()+split},accept);
    Check(count==(split==expected.size()?1u:0u));
    decoder.Feed({expected.begin()+split,expected.end()},accept);Check(count==1 && decoder.Empty());
  }
  auto reply=add;reply.verb=MemoryFrame::Reply32;reply.key=0;reply.address=0;
  auto joined=expected;const auto encoded_reply=EncodeMemoryFrame(reply);joined.insert(joined.end(),encoded_reply.begin(),encoded_reply.end());
  for(unsigned chunk=1;chunk<=joined.size();++chunk){
    MemoryFrameDecoder decoder;unsigned count=0;
    for(unsigned i=0;i<joined.size();i+=chunk)decoder.Feed({joined.begin()+i,joined.begin()+std::min<std::size_t>(i+chunk,joined.size())},
      [&](const MemoryFrame& f){Check(f.verb==(count?5u:4u) && f.body.size()==4);++count;});
    Check(count==2 && decoder.Empty());
  }
  const std::vector<uint32_t> values={0,1,7,0x7fffffff,0x80000000,0xfffffffe,0xffffffff};
  unsigned cases=0;
  for(const auto initial:values)for(const auto delta:values)for(unsigned offset=0;offset<16;offset+=4){
    RemoteMemory memory;memory.Register({2,7,4096,true,std::vector<uint8_t>(16,0x5a),0,{}});
    memory.Store32(2,7,4096+offset,initial);const auto before=memory.Windows()[0].bytes;
    auto request=add;request.address=4096+offset;request.body=MemoryInteger(delta,4);
    const uint64_t mathematical=uint64_t(initial)+delta;
    const uint32_t result=static_cast<uint32_t>(mathematical%4294967296ULL);
    const auto applied=memory.Apply(0,2,request);
    Check(applied.atomic && !applied.duplicate && applied.old_value==initial);
    Check(memory.Load32(2,7,request.address)==result);
    for(unsigned i=0;i<16;++i)Check(memory.Windows()[0].bytes[i]==
      (i>=offset && i<offset+4?static_cast<uint8_t>(result>>(8*(i-offset))):before[i]));
    memory.Store32(2,7,request.address,0);
    const auto replay=memory.Apply(0,2,request);
    Check(replay.duplicate && replay.old_value==initial && memory.Load32(2,7,request.address)==0);
    auto conflict=request;conflict.body=MemoryInteger(uint64_t(delta)^1u,4);Reject([&]{memory.Apply(0,2,conflict);});
    ++cases;
  }
  RemoteMemory narrow;narrow.Register({2,7,4100,true,std::vector<uint8_t>(4,0),0,{}});
  Check(narrow.Apply(0,2,add).old_value==0 && narrow.Load32(2,7,4100)==UINT32_MAX);
  Reject([&]{narrow.Load64(2,7,4100);});
  narrow.Store32(2,7,4100,123);Check(narrow.Load32(2,7,4100)==123);
  for(unsigned mode=0;mode<7;++mode){
    auto invalid=add;invalid.sequence=1;
    if(mode==0)invalid.address=4101;
    if(mode==1)invalid.address=4104;
    if(mode==2)invalid.address=4096;
    if(mode==3)invalid.key=8;
    if(mode==4)invalid.verb=MemoryFrame::Reply32;
    if(mode==5)invalid.body=MemoryInteger(1);
    if(mode==6)invalid.address=UINT64_MAX-2;
    Reject([&]{narrow.Apply(0,2,invalid);});Check(narrow.Load32(2,7,4100)==123);
  }
  Reject([&]{MemoryInteger(uint64_t(UINT32_MAX)+1,4);});
  for(const auto verb:{MemoryFrame::FetchAdd,MemoryFrame::Reply,MemoryFrame::FetchAdd32,MemoryFrame::Reply32}){
    auto wrong=reply;wrong.verb=verb;wrong.body=MemoryInteger(1,MemoryValueBytes(verb)==4?8:4);
    Reject([&]{EncodeMemoryFrame(wrong);});
  }
  RemoteMemory guarded;guarded.Register({2,7,4096,true,std::vector<uint8_t>(16,0),0,{}});
  guarded.AcquireRead(1,{2,7,4104,4});
  Check(guarded.Apply(0,2,add).old_value==0); // adjacent lease is untouched
  auto overlapping=add;overlapping.address=4104;
  Reject([&]{guarded.Apply(1,2,overlapping);});
  guarded.ReleaseRead(1);Check(guarded.Apply(1,2,overlapping).old_value==0);
  RemoteMemory scoped;scoped.Register({2,7,4096,true,std::vector<uint8_t>(16,0),0,{}});
  scoped.ConfigureRegistrations();scoped.RegisterAccess({0,2,1,70,7,4100,4,true});
  auto alias=add;alias.key=70;
  Check(scoped.ApplyOnNic(0,2,1,alias).old_value==0);
  Reject([&]{scoped.ApplyOnNic(1,2,2,alias);});
  auto wide=alias;wide.verb=MemoryFrame::FetchAdd;wide.body=MemoryInteger(1);
  Reject([&]{scoped.ApplyOnNic(1,2,1,wide);});
  RemoteMemory denied;denied.Register({2,7,4096,false,std::vector<uint8_t>(16,0),0,{}});
  Reject([&]{denied.Apply(0,2,add);});
  RemoteMemory sparse;sparse.RegisterSparse(2,7,0,true,8192,{});
  auto boundary=add;boundary.address=4092;
  Check(sparse.Apply(0,2,boundary).old_value==0 && sparse.Load32(2,7,4092)==UINT32_MAX);
  Check(sparse.Load32(2,7,4096)==0 && sparse.Windows()[0].pages.size()==1);
  std::cout<<"{\"passed\":true,\"checks\":"<<checks<<",\"independent_arithmetic_cases\":"<<cases
           <<",\"network_execution\":false}\n";
}
