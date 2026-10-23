#include "rdma/remote_memory.hh"
#include <iostream>
#include <map>
using namespace moe_rdma;
static unsigned checks=0;
static void Check(bool b) {++checks;if(!b)throw std::runtime_error("sparse memory oracle mismatch");}
template<class F>static void Reject(F f) {bool rejected=false;try{f();}catch(const std::exception&){rejected=true;}Check(rejected);}
int main() {
  constexpr uint64_t base=0x100000000ULL,size=18*1024*1024;
  RemoteMemory memory;memory.RegisterSparse(4,7,base,true,size,{{4094,{1,2,3,4}}});
  Check(memory.Windows()[0].bytes.empty() && RemoteMemory::Size(memory.Windows()[0])==size);
  Check(memory.Windows()[0].pages.size()==2);
  Check(memory.ReadBytes(4,7,base+4093,6)==std::vector<uint8_t>({0,1,2,3,4,0}));
  Check(memory.ReadBytes(4,7,base+size-32,32)==std::vector<uint8_t>(32,0));
  Check(memory.Windows()[0].pages.size()==2); // Zero reads allocate no pages.
  std::map<uint64_t,uint8_t> oracle={{4094,1},{4095,2},{4096,3},{4097,4}};
  for(uint64_t i=0;i<24;++i) {
    const uint64_t offset=(i%2?16*1024*1024:0)+4090+193*i;
    MemoryFrame put;put.verb=MemoryFrame::Put;put.key=7;put.address=base+offset;put.sequence=i;
    for(unsigned j=0;j<257;++j)put.body.push_back((i*13+j*7)%256);
    Check(!memory.Apply(0,4,put).duplicate);
    for(unsigned j=0;j<257;++j)oracle[offset+j]=put.body[j];
    Check(memory.Apply(0,4,put).duplicate);
    auto actual=memory.ReadBytes(4,7,base+offset-5,267);
    for(unsigned j=0;j<actual.size();++j)Check(actual[j]==oracle[offset-5+j]);
  }
  // Actual LE64 arithmetic at a high address, overflow and duplicate response.
  memory.Store64(4,7,base+size-8,UINT64_MAX);
  MemoryFrame atom;atom.verb=MemoryFrame::FetchAdd;atom.key=7;atom.address=base+size-8;atom.body=MemoryInteger(3);
  auto result=memory.Apply(1,4,atom);Check(result.old_value==UINT64_MAX && memory.Load64(4,7,atom.address)==2);
  Check(memory.Apply(1,4,atom).duplicate && memory.Load64(4,7,atom.address)==2);
  memory.AcquireRead(1,{4,7,base+size-8,8});
  Reject([&]{memory.Store64(4,7,base+size-8,7);});
  memory.ReleaseRead(1);
  memory.AcquireWrite(2,{4,7,base+8190,8});
  Reject([&]{memory.ReadBytes(4,7,base+8190,8);});
  memory.CommitWrite(2,{9,8,7,6,5,4,3,2});memory.ReleaseWrite(2);
  Check(memory.ReadBytes(4,7,base+8190,8)==std::vector<uint8_t>({9,8,7,6,5,4,3,2}));
  for(auto address:{base-1,base+size,UINT64_MAX})Reject([&]{memory.ReadBytes(4,7,address,8);});
  Reject([&]{memory.ReadBytes(4,7,base+size-7,8);});
  Reject([&]{memory.RegisterSparse(4,9,base+size-1,false,32,{});});
  Reject([&]{memory.RegisterSparse(5,7,UINT64_MAX-7,false,8,{});});
  Reject([&]{memory.RegisterSparse(5,7,0,false,size,{{1,{1,2}},{2,{3}}});});
  Reject([&]{memory.RegisterSparse(5,7,0,false,size,{{size,{1}}});});
  // Fill the exact page budget. A two-page write touches an existing page
  // before needing a missing page: failure must preserve even its first byte.
  RemoteMemory full;full.RegisterSparse(0,1,0,true,64*1024*1024,{});
  for(uint64_t page=0;page<RemoteMemory::MaxPages;++page)full.Store64(0,1,page*4096,19);
  Check(full.Windows()[0].pages.size()==RemoteMemory::MaxPages);
  const auto before=full.Windows()[0].pages;
  MemoryFrame beyond; beyond.verb=MemoryFrame::Put;beyond.key=1;beyond.address=256*4096-1;beyond.body={1,2};
  Reject([&]{full.Apply(0,0,beyond);});Check(full.Windows()[0].pages==before);
  beyond.address=0;beyond.body={3,4};Check(!full.Apply(0,0,beyond).duplicate); // Failed request didn't advance seq.
  MemoryFrame missing;missing.verb=MemoryFrame::FetchAdd;missing.key=1;missing.address=256*4096;missing.body=MemoryInteger(2);
  const auto saved=full.Windows()[0].pages;
  Reject([&]{full.Apply(1,0,missing);});Check(full.Windows()[0].pages==saved);
  missing.address=8;Check(full.Apply(1,0,missing).old_value==0 && full.Load64(0,1,8)==2);
  full.AcquireWrite(9,{0,1,256*4096-1,2});
  const auto prior=full.Windows()[0].pages;
  Reject([&]{full.CommitWrite(9,{7,8});});Check(full.Windows()[0].pages==prior && full.WriteLeases().count(9));
  full.ReleaseWrite(9);
  std::cout<<"sparse memory independent assertions "<<checks<<'\n';
}
