#include "rdma/remote_memory.hh"
#include <iostream>
using namespace moe_rdma;
static unsigned checks=0;
static void CheckAt(bool value,unsigned line){++checks;if(!value)throw std::runtime_error("scale-out memory assertion failed at line "+std::to_string(line));}
#define Check(value) CheckAt((value),__LINE__)
template<class F>static void Reject(F f){bool bad=false;try{f();}catch(const std::exception&){bad=true;}Check(bad);}
int main() {
  // Explicit selection is one-time and must precede every other kind of use.
  for(unsigned mode=0;mode<4;++mode) {
    RemoteMemory m;
    if(mode==0)m.RegisterSparse(32,1,4096,true,4096,{});
    if(mode==1)m.ConfigureRegistrations();
    if(mode==2)m.ConfigureRequestLimits(1,1);
    if(mode==3)m.ConfigureScaleOutCapacity();
    Reject([&]{m.ConfigureScaleOutCapacity();});
  }
  RemoteMemory m;m.ConfigureScaleOutCapacity();
  for(auto values:{std::vector<uint64_t>{2049,1,1},{1,16385,1},{1,1,17409},{0,1,1}})
    Reject([&]{m.ConfigureRequestLimits(values[0],values[1],values[2]);});
  m.ConfigureRequestLimits(2048,16384,17408);
  for(unsigned i=0;i<128;++i)m.RegisterSparse(32,i+1,4096*(i+1),true,4096,{});
  Reject([&]{m.RegisterSparse(32,129,4096*129,true,4096,{});});
  m.ConfigureRegistrations();
  for(unsigned i=0;i<256;++i)m.RegisterAccess({i,32,1,1000+i,1,4096,4096,true});
  Reject([&]{m.RegisterAccess({256,32,1,1256,1,4096,4096,true});});Check(m.Registrations().size()==256);
  for(unsigned i=0;i<17408;++i)m.AcquireRead(i,{32,1,4096,8});
  Reject([&]{m.AcquireRead(17408,{32,1,4096,8});});Check(m.ReadLeases().size()==17408);
  Reject([&]{m.Store64(32,1,4096,1);});Check(m.Load64(32,1,4096)==0);
  for(unsigned i=0;i<17408;++i)m.ReleaseRead(i);
  for(unsigned i=0;i<128;++i)m.AcquireWrite(i,{32,1,4096+8*i,8});
  Reject([&]{m.AcquireWrite(128,{32,1,4096+8*128,8});});
  Reject([&]{m.AcquireRead(0,{32,1,4096,8});});
  for(unsigned i=0;i<128;++i){std::vector<uint8_t> bytes(8,0);bytes[0]=i;m.CommitWrite(i,bytes);m.ReleaseWrite(i);Check(m.Load64(32,1,4096+8*i)==i);}
  MemoryFrame f{MemoryFrame::FetchAdd32,1000,0,4096,MemoryInteger(1,4)};
  for(unsigned i=0;i<16384;++i) {
    f.sequence=i;const auto r=m.ApplyOnNic(0,32,1,f);Check(!r.duplicate && r.old_value==i);
    const auto again=m.ApplyOnNic(0,32,1,f);Check(again.duplicate && again.old_value==i);
  }
  f.sequence=16384;Reject([&]{m.ApplyOnNic(0,32,1,f);});Check(m.Load32(32,1,4096)==16384);
  f.sequence=0;
  for(unsigned i=1;i<2048;++i)Check(m.ApplyOnNic(i,32,1,f).old_value==16383+i);
  Reject([&]{m.ApplyOnNic(2048,32,1,f);});Check(m.ReplayStates().size()==2048);
  // Old capacities stay old after failed attempts to enlarge them.
  RemoteMemory old;Reject([&]{old.ConfigureRequestLimits(65,1);});
  for(unsigned i=0;i<16;++i)old.RegisterSparse(0,i+1,4096*(i+1),true,4096,{});
  Reject([&]{old.RegisterSparse(0,17,4096*17,true,4096,{});});
  std::cout<<checks<<" scale-out memory assertions\n";
}
