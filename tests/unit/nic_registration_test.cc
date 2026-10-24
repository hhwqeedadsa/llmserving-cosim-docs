#include "rdma/remote_memory.hh"
#include <iostream>
using namespace moe_rdma;
static unsigned checks=0;
static void Check(bool ok){++checks;if(!ok)throw std::runtime_error("NIC registration assertion failed");}
template<class F>static void Reject(F f){bool rejected=false;try{f();}catch(const std::invalid_argument&){rejected=true;}Check(rejected);}
int main() {
  RemoteMemory m;m.Register({4,9,8192,true,std::vector<uint8_t>(32,0),0,{}});m.ConfigureRegistrations();
  m.RegisterAccess({0,4,1,21,9,8192,16,false});m.RegisterAccess({1,4,2,21,9,8200,16,true});
  m.RegisterAccess({2,4,2,22,9,8192,8,true});
  for(unsigned mode=0;mode<4;++mode) {
    auto r=RemoteMemory::Registration{3,4,3,23,9,8192,8,true};
    if(mode==0)r.id=0;
    if(mode==1){r.nic=1;r.key=21;}
    if(mode==2)r.address=8218;
    if(mode==3)r.window_key=10;
    Reject([&]{m.RegisterAccess(r);});Check(m.Registrations().size()==3);
  }
  MemoryFrame put{MemoryFrame::Put,21,0,8192,{1,2,3,4,5,6,7,8}};
  Reject([&]{m.Apply(0,4,put);});Reject([&]{m.ApplyOnNic(0,4,3,put);});
  Reject([&]{m.ApplyOnNic(0,4,2,put);}); // same rkey, different allowed subrange
  Check(m.ReplayStates().empty());
  m.ApplyOnNic(0,4,1,put);Check(m.ReadBytes(4,9,8192,8)==put.body);
  auto add=MemoryFrame{MemoryFrame::FetchAdd,22,0,8192,MemoryInteger(1)};
  const auto old=m.ApplyOnNic(1,4,2,add).old_value;
  Check(old==0x0807060504030201ULL);Check(m.Load64(4,9,8192)==old+1);
  Check(m.ApplyOnNic(1,4,2,add).duplicate);Check(m.Load64(4,9,8192)==old+1);
  auto changed=put;changed.key=22;Reject([&]{m.ApplyOnNic(0,4,2,changed);});
  auto denied=add;denied.key=21;Reject([&]{m.ApplyOnNic(2,4,1,denied);});
  m.AcquireRead(8,{4,9,8192,8});auto next=put;next.sequence=1;
  Reject([&]{m.ApplyOnNic(0,4,1,next);});Reject([&]{m.ApplyOnNic(2,4,2,add);});
  Check(m.Load64(4,9,8192)==old+1);m.ReleaseRead(8);
  m.AcquireWrite(9,{4,9,8192,8});Reject([&]{m.ApplyOnNic(0,4,1,next);});
  m.CommitWrite(9,std::vector<uint8_t>(8,42));m.ReleaseWrite(9);
  Check(m.ApplyOnNic(3,4,2,add).old_value==0x2a2a2a2a2a2a2a2aULL);
  m.ApplyOnNic(0,4,1,next);Check(m.ReadBytes(4,9,8192,8)==put.body);
  Check(m.Windows().size()==1 && m.Windows()[0].bytes.size()==32);
  Reject([&]{m.RegisterAccess({4,4,3,23,9,8192,8,true});});
  std::cout<<checks<<" independent NIC/storage assertions passed\n";
}
