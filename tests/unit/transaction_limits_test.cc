#include "rdma/remote_memory.hh"
#include <iostream>
using namespace moe_rdma;
static unsigned checks=0;
static void Check(bool value) {++checks;if(!value)throw std::runtime_error("request-limit oracle failed");}
template<class F>static void Reject(F f) {bool bad=false;try{f();}catch(const std::exception&){bad=true;}Check(bad);}
static void Register(RemoteMemory& m) {m.Register({1,7,4096,true,std::vector<uint8_t>(8,0),0,{}});}
int main() {
  MemoryFrame f;f.verb=MemoryFrame::FetchAdd;f.key=7;f.address=4096;f.body=MemoryInteger(1);
  RemoteMemory defaults;Register(defaults);
  for(unsigned c=0;c<8;++c)for(unsigned s=0;s<32;++s) {
    f.sequence=s;auto r=defaults.Apply(c,1,f);Check(!r.duplicate && r.old_value==32*c+s);
  }
  f.sequence=32;Reject([&]{defaults.Apply(0,1,f);});
  f.sequence=0;Reject([&]{defaults.Apply(8,1,f);});Check(defaults.Load64(1,7,4096)==256);
  for(auto [contexts,requests]:{std::pair{0u,1u},{65u,1u},{1u,0u},{1u,1025u}}) {
    RemoteMemory m;Register(m);Reject([&]{m.ConfigureRequestLimits(contexts,requests);});
    for(unsigned s=0;s<32;++s) {f.sequence=s;Check(m.Apply(0,1,f).old_value==s);}
    f.sequence=32;Reject([&]{m.Apply(0,1,f);});Check(m.Load64(1,7,4096)==32);
  }
  for(unsigned readers:{0u,1281u}) {
    RemoteMemory invalid;Register(invalid);Reject([&]{invalid.ConfigureRequestLimits(64,1024,readers);});
    for(unsigned s=0;s<32;++s) {f.sequence=s;Check(invalid.Apply(0,1,f).old_value==s);}
    f.sequence=32;Reject([&]{invalid.Apply(0,1,f);});
  }
  for(unsigned readers:{48u,1280u}) {
    RemoteMemory leases;Register(leases);
    if(readers==1280)leases.ConfigureRequestLimits(64,1024,readers);
    for(unsigned i=0;i<readers;++i)leases.AcquireRead(i,{1,7,4096,8});
    Reject([&]{leases.AcquireRead(readers,{1,7,4096,8});});
    Reject([&]{leases.ConfigureRequestLimits(64,1024,1280);});
    Reject([&]{leases.Store64(1,7,4096,99);});Check(leases.Load64(1,7,4096)==0);
    for(unsigned i=0;i<readers;++i)leases.ReleaseRead(i);
    leases.Store64(1,7,4096,3);Check(leases.Load64(1,7,4096)==3);
  }
  RemoteMemory m;Register(m);m.ConfigureRequestLimits(64,1024);
  for(unsigned c=0;c<64;++c)for(unsigned s=0;s<1024;++s) {
    f.sequence=s;auto r=m.Apply(c,1,f);const uint64_t old=1024*c+s;
    Check(!r.duplicate && r.atomic && r.old_value==old);
    auto again=m.Apply(c,1,f);Check(again.duplicate && again.old_value==old);
    if(s==32 || s==1023) {
      auto conflicting=f;conflicting.body=MemoryInteger(2);
      Reject([&]{m.Apply(c,1,conflicting);});Check(m.Load64(1,7,4096)==old+1);
    }
  }
  Check(m.Load64(1,7,4096)==65536);
  f.sequence=1024;Reject([&]{m.Apply(0,1,f);});
  f.sequence=0;Reject([&]{m.Apply(64,1,f);});
  Reject([&]{m.ConfigureRequestLimits(64,1024);});
  Check(m.Load64(1,7,4096)==65536);
  // Late duplicates beyond the former 32 boundary retain their original return,
  // even after local consumption changes the current target value.
  m.Store64(1,7,4096,0);
  for(unsigned c=0;c<64;++c)for(unsigned s:{0u,31u,32u,1023u}) {
    f.sequence=s;auto r=m.Apply(c,1,f);
    Check(r.duplicate && r.old_value==1024*c+s && m.Load64(1,7,4096)==0);
  }
  std::cout<<checks<<" independent request-limit assertions\n";
}
