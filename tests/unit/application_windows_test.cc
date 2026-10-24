#include "rdma/application_windows.hh"
#include <iostream>
using namespace moe_rdma;
static unsigned checks=0;
static void Check(bool ok){++checks;if(!ok)throw std::runtime_error("application view check failed");}
template<class F>static void Reject(F f){bool bad=false;try{f();}catch(const std::invalid_argument&){bad=true;}Check(bad);}
int main() {
  // Independent set oracle across every subset of eight shared references.
  for(unsigned mask=0;mask<256;++mask) {
    ApplicationWindows w;unsigned remaining=8;
    for(unsigned id=0;id<8;++id)w.Add({id,4,9,8192+id,16,false,{}});
    for(unsigned id=0;id<8;++id)if(mask&(1<<id)){w.Close(id,4);--remaining;}
    Check(w.References(4,9)==remaining);Check(w.References(0,9)==0);
    for(unsigned id=0;id<8;++id) {
      if(mask&(1<<id))Reject([&]{w.Acquire(20,4,{id});});
      else {
        w.Acquire(20,4,{id});Check(!w.Drained());
        Reject([&]{w.Close(id,4);});Check(w.References(4,9)==remaining);
        Check(w.Release(20)==std::vector<uint64_t>{id});Check(w.Drained());
      }
    }
  }
  ApplicationWindows w;w.Add({1,0,7,4096,64,false,{}});w.Add({2,4,9,8192,32,false,{}});
  Reject([&]{w.Acquire(0,0,{1,2});});Check(w.Drained() && w.Get(1).users.empty());
  Reject([&]{w.Acquire(0,0,{1,1});});Check(w.Drained());
  w.Acquire(0,0,{1});Reject([&]{w.Acquire(0,4,{2});});Check(w.Get(2).users.empty());
  Reject([&]{w.Close(1,4);});Reject([&]{w.Close(99,0);});
  w.Release(0);w.Close(1,0);Check(w.References(0,7)==0);
  Reject([&]{w.Close(1,0);});Reject([&]{w.Acquire(1,0,{1});});
  Check(w.References(4,9)==1); // no provider/remote permission state is changed
  std::cout<<checks<<" local view/reference assertions passed\n";
}
