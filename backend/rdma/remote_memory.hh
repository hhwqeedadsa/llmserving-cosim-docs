#pragma once
#include <algorithm>
#include <cstdint>
#include <functional>
#include <map>
#include <stdexcept>
#include <vector>

namespace moe_rdma {
// MRT1 is an EXPLICIT FUNCTIONAL framing protocol over the native byte carrier,
// not a RoCE wire encoding. Never use these frame sizes as GIN timing evidence.
// Network-byte-order header: magic32, verb32, sequence64, address64, key32,
// body_length32. Put has raw bytes; FetchAdd/Reply have one BE64 integer.
struct MemoryFrame {
  enum Verb : uint32_t { Put=1, FetchAdd=2, Reply=3 };
  uint32_t verb=0, key=0;
  uint64_t sequence=0, address=0;
  std::vector<uint8_t> body;
};
inline void MemoryCheck(bool ok,const char* why) {if(!ok)throw std::invalid_argument(why);}
inline uint64_t MemoryRead(const uint8_t* p,unsigned count) {
  uint64_t value=0;for(unsigned i=0;i<count;++i)value=(value<<8)|p[i];return value;
}
inline void MemoryWrite(std::vector<uint8_t>& out,uint64_t value,unsigned count) {
  for(unsigned i=count;i;--i)out.push_back(value>>((i-1)*8));
}
inline std::vector<uint8_t> MemoryInteger(uint64_t value) {
  std::vector<uint8_t> out;MemoryWrite(out,value,8);return out;
}
inline std::vector<uint8_t> EncodeMemoryFrame(const MemoryFrame& frame) {
  MemoryCheck(frame.verb>=1 && frame.verb<=3 && frame.body.size()>0 && frame.body.size()<=32768,
              "invalid memory frame verb/length");
  MemoryCheck(frame.verb==MemoryFrame::Put || frame.body.size()==8,"atomic frame requires 8-byte value");
  MemoryCheck(frame.verb!=MemoryFrame::Reply || (!frame.address && !frame.key),"reply has no remote address/key");
  std::vector<uint8_t> out;out.reserve(32+frame.body.size());
  MemoryWrite(out,0x4d525431,4);MemoryWrite(out,frame.verb,4);MemoryWrite(out,frame.sequence,8);
  MemoryWrite(out,frame.address,8);MemoryWrite(out,frame.key,4);MemoryWrite(out,frame.body.size(),4);
  out.insert(out.end(),frame.body.begin(),frame.body.end());return out;
}
// Packet segmentation is independent of transaction framing. There is no source
// operation table here. Only bytes actually delivered by the carrier are parsed.
class MemoryFrameDecoder {
 public:
  void Feed(const std::vector<uint8_t>& bytes,const std::function<void(const MemoryFrame&)>& consume) {
    MemoryCheck(buffer_.size()+bytes.size()<=1024*1024,"memory decoder input budget exceeded");
    buffer_.insert(buffer_.end(),bytes.begin(),bytes.end());
    std::size_t offset=0;
    while(buffer_.size()-offset>=32) {
      const auto* p=buffer_.data()+offset;
      MemoryCheck(MemoryRead(p,4)==0x4d525431,"invalid MRT1 frame magic");
      const auto length=MemoryRead(p+28,4);
      MemoryCheck(length>0 && length<=32768,"memory frame body length exceeded");
      if(buffer_.size()-offset<32+length)break;
      MemoryFrame frame;frame.verb=MemoryRead(p+4,4);frame.sequence=MemoryRead(p+8,8);
      frame.address=MemoryRead(p+16,8);frame.key=MemoryRead(p+24,4);
      frame.body.assign(p+32,p+32+length);
      EncodeMemoryFrame(frame); // canonical field/verb checks, before applying.
      consume(frame);offset+=32+length;
    }
    buffer_.erase(buffer_.begin(),buffer_.begin()+offset);
  }
  bool Empty() const {return buffer_.empty();}
 private:
  std::vector<uint8_t> buffer_;
};
class RemoteMemory {
 public:
  struct Window {
    unsigned owner;uint32_t key;uint64_t address;bool atomic;std::vector<uint8_t> bytes;
    uint64_t logical_bytes=0;
    std::map<uint64_t,std::vector<uint8_t>> pages;
  };
  static constexpr uint64_t PageBytes=4096,MaxPages=256,MaxLogicalBytes=64*1024*1024;
  static uint64_t Size(const Window& w) {return w.logical_bytes?w.logical_bytes:w.bytes.size();}
  void ConfigureRequestLimits(uint64_t contexts,uint64_t requests,uint64_t readers=48) {
    MemoryCheck(contexts_.empty() && leases_.empty() && writers_.empty() &&
                contexts>0 && contexts<=64 && requests>0 && requests<=1024 && readers>0 && readers<=1280,
                "invalid/live remote request limits");
    max_contexts_=contexts;max_requests_=requests;max_readers_=readers;
  }
  void RegisterSparse(unsigned owner,uint32_t key,uint64_t address,bool atomic,uint64_t length,
                      const std::vector<std::pair<uint64_t,std::vector<uint8_t>>>& segments) {
    MemoryCheck(length>0 && length<=MaxLogicalBytes && address<=UINT64_MAX-length && segments.size()<=32,
                "invalid sparse window extent/segments");
    Window window{owner,key,address,atomic,{},length,{}};
    uint64_t initialized=0;std::vector<std::pair<uint64_t,uint64_t>> used;
    for(const auto& [offset,bytes]:segments) {
      MemoryCheck(!bytes.empty() && offset<=length && bytes.size()<=length-offset,
                  "invalid sparse initialization range");
      initialized+=bytes.size();MemoryCheck(initialized<=65536,"sparse initialization byte budget exceeded");
      for(const auto& [start,end]:used)MemoryCheck(offset+bytes.size()<=start || end<=offset,"overlapping sparse initialization");
      used.emplace_back(offset,offset+bytes.size());Write(window,offset,bytes);
    }
    Register(std::move(window));
  }
  struct Result {bool duplicate;bool atomic;uint64_t old_value;};
  struct ReadLease {unsigned owner;uint32_t key;uint64_t address,bytes;};
  // Static functional registration: no extra backing bytes and no provider DMA.
  struct Registration {
    uint64_t id;unsigned owner,nic;uint32_t key,window_key;uint64_t address,bytes;bool atomic;
  };
  void ConfigureRegistrations() {
    MemoryCheck(!nic_scoped_ && contexts_.empty() && registrations_.empty(),"registration model configured after use/repeated");
    nic_scoped_=true;
  }
  void RegisterAccess(Registration r) {
    MemoryCheck(nic_scoped_ && contexts_.empty() && registrations_.size()<32 && r.key && r.bytes,
                "invalid registration mode/count/key/extent");
    const auto& w=Range(r.owner,r.window_key,r.address,r.bytes);
    MemoryCheck(!r.atomic || w.atomic,"registration cannot grant storage atomic permission");
    for(const auto& p:registrations_)
      MemoryCheck(p.id!=r.id && !(p.owner==r.owner && p.nic==r.nic && p.key==r.key),"repeated registration id or owner/NIC/key");
    registrations_.push_back(r);
  }
  const std::vector<Registration>& Registrations() const {return registrations_;}
  bool NicScoped() const {return nic_scoped_;}
  // Only the current monotonic PUT carrier can opt out of application replay.
  // This does not release windows/leases or invalidate already-in-flight bytes.
  void ConfigureCarrierOncePut() {
    MemoryCheck(contexts_.empty() && !cache_capacity_ && !carrier_once_put_,"target delivery configured after use/repeated");
    carrier_once_put_=true;
  }
  void ConfigureCacheBudget(uint64_t bytes) {
    MemoryCheck(contexts_.empty() && !carrier_once_put_ && !cache_capacity_ && bytes>=33 && bytes<=2*1024*1024,"invalid target cache capacity");
    cache_capacity_=bytes;
  }
  const std::map<unsigned,uint64_t>& CacheBytes() const {return cache_bytes_;}
  struct ReplayState {uint64_t context,next,requests,encoded_bytes;};
  std::vector<ReplayState> ReplayStates() const {
    std::vector<ReplayState> out;
    for(const auto& [id,state]:contexts_) {
      uint64_t bytes=0;for(const auto& [sequence,saved]:state.saved)bytes+=saved.bytes.size();
      out.push_back({id,state.next,state.saved.size(),bytes});
    }
    return out;
  }
  void Register(Window window) {
    MemoryCheck(windows_.size()<16 && window.key && Size(window)>0 &&
                (window.logical_bytes?(window.bytes.empty() && window.logical_bytes<=MaxLogicalBytes && window.pages.size()<=MaxPages):
                 (window.bytes.size()<=65536 && window.pages.empty())) &&
                window.address<=UINT64_MAX-Size(window),"invalid remote memory window");
    for(const auto& [page,bytes]:window.pages)
      MemoryCheck(page<=(Size(window)-1)/PageBytes && bytes.size()==PageBytes,"invalid sparse resident page");
    for(const auto& w:windows_)if(w.owner==window.owner) {
      MemoryCheck(w.key!=window.key,"remote key repeated for owner");
      MemoryCheck(window.address+Size(window)<=w.address || w.address+Size(w)<=window.address,
                  "overlapping remote memory windows");
    }
    windows_.push_back(std::move(window));
  }
  // Functional model: serial application on the ns-3 event clock; LE64 memory
  // atomics; retained request/result cache for this bounded connection lifetime.
  // It does NOT implement PSNs, retry timers, GPU coherence or remote NIC delay.
  Result Apply(uint64_t context,unsigned owner,const MemoryFrame& frame) {
    MemoryCheck(!nic_scoped_,"NIC-scoped target requires actual ingress NIC");
    return ApplyImpl(context,owner,frame,nullptr);
  }
  Result ApplyOnNic(uint64_t context,unsigned owner,unsigned nic,const MemoryFrame& frame) {
    if(!nic_scoped_)return Apply(context,owner,frame);
    const auto it=std::find_if(registrations_.begin(),registrations_.end(),[&](const auto& r){
      return r.owner==owner && r.nic==nic && r.key==frame.key;
    });
    MemoryCheck(it!=registrations_.end(),"unknown remote owner/NIC/key registration");
    const uint64_t length=frame.verb==MemoryFrame::Put?frame.body.size():8;
    MemoryCheck(length>0 && frame.address>=it->address && frame.address-it->address<=it->bytes &&
                length<=it->bytes-(frame.address-it->address),"remote registration range exceeded");
    MemoryCheck(frame.verb!=MemoryFrame::FetchAdd || it->atomic,"registration atomic denied");
    const auto binding=std::pair{owner,nic};
    MemoryCheck(!context_bindings_.count(context) || context_bindings_.at(context)==binding,"memory context changed ingress owner/NIC");
    auto result=ApplyImpl(context,owner,frame,&*it);
    context_bindings_.emplace(context,binding);return result;
  }
 private:
  Result ApplyImpl(uint64_t context,unsigned owner,const MemoryFrame& frame,const Registration* registration) {
    MemoryCheck(frame.verb==MemoryFrame::Put || frame.verb==MemoryFrame::FetchAdd,"target received non-request");
    MemoryCheck(!carrier_once_put_ || frame.verb==MemoryFrame::Put,"carrier-once target supports put only");
    const auto encoded=EncodeMemoryFrame(frame);
    MemoryCheck(contexts_.count(context) || contexts_.size()<max_contexts_,"remote memory context budget exceeded");
    auto& state=contexts_[context];
    if(frame.sequence<state.next) {
      MemoryCheck(!carrier_once_put_,"application replay violates carrier-once put contract");
      const auto& saved=state.saved.at(frame.sequence);
      MemoryCheck(saved.owner==owner && saved.bytes==encoded,"conflicting duplicate memory request");
      return {true,saved.result.atomic,saved.result.old_value};
    }
    MemoryCheck(frame.sequence==state.next && state.next<max_requests_,"memory request sequence gap/budget exceeded");
    const auto storage_key=registration?registration->window_key:frame.key;
    auto it=std::find_if(windows_.begin(),windows_.end(),[&](const Window& w){return w.owner==owner && w.key==storage_key;});
    MemoryCheck(it!=windows_.end(),"unknown remote memory key/owner");
    const auto length=frame.verb==MemoryFrame::Put?frame.body.size():8;
    MemoryCheck(frame.address>=it->address && frame.address-it->address<=Size(*it) &&
                length<=Size(*it)-(frame.address-it->address),"remote memory address out of bounds");
    const auto offset=frame.address-it->address;
    if(frame.verb==MemoryFrame::FetchAdd)MemoryCheck(it->atomic && frame.address%8==0,"remote atomic denied/unaligned");
    CheckWritable(owner,storage_key,frame.address,length);
    if(cache_capacity_)MemoryCheck(encoded.size()<=cache_capacity_-cache_bytes_[owner],"target request cache capacity exceeded");
    Result result{false,frame.verb==MemoryFrame::FetchAdd,0};
    if(result.atomic) {
      MemoryCheck(it->atomic && frame.address%8==0,"remote atomic denied/unaligned");
      for(unsigned i=0;i<8;++i)result.old_value|=uint64_t(Byte(*it,offset+i))<<(8*i);
      const uint64_t updated=result.old_value+MemoryRead(frame.body.data(),8);
      std::vector<uint8_t> bytes(8);for(unsigned i=0;i<8;++i)bytes[i]=updated>>(8*i);
      Write(*it,offset,bytes);
    } else Write(*it,offset,frame.body);
    if(!carrier_once_put_)state.saved.emplace(frame.sequence,Saved{owner,encoded,result});
    ++state.next;
    if(cache_capacity_)cache_bytes_[owner]+=encoded.size();
    return result;
  }
 public:
  const std::vector<Window>& Windows() const {return windows_;}
  std::vector<uint8_t> ReadBytes(unsigned owner,uint32_t key,uint64_t address,uint64_t bytes) const {
    CheckReadable(owner,key,address,bytes);
    const auto& w=Range(owner,key,address,bytes);
    MemoryCheck(bytes<=32768,"source read length exceeded");
    std::vector<uint8_t> out(bytes);for(uint64_t i=0;i<bytes;++i)out[i]=Byte(w,address-w.address+i);return out;
  }
  void AcquireRead(uint64_t identity,ReadLease lease) {
    MemoryCheck(!leases_.count(identity) && leases_.size()<max_readers_,"invalid source read lease identity/budget");
    CheckRead(lease);
    leases_.emplace(identity,lease);
  }
  void CheckRead(ReadLease lease) const {
    CheckRange(lease);
    CheckReadable(lease.owner,lease.key,lease.address,lease.bytes);
  }
  void CheckRange(ReadLease lease) const {
    Range(lease.owner,lease.key,lease.address,lease.bytes);
    MemoryCheck(lease.bytes<=32768,"source read lease length exceeded");
  }
  void ReleaseRead(uint64_t identity) {
    MemoryCheck(leases_.erase(identity)==1,"unknown source read lease release");
  }
  const std::map<uint64_t,ReadLease>& ReadLeases() const {return leases_;}
  void CheckReadable(unsigned owner,uint32_t key,uint64_t address,uint64_t bytes) const {
    Range(owner,key,address,bytes);
    for(const auto& item:writers_) {
      const auto& w=item.second;
      if(w.owner==owner)MemoryCheck(address+bytes<=w.address || w.address+w.bytes<=address,
                                   "read overlaps active local output lease");
    }
  }
  void AcquireWrite(uint64_t identity,ReadLease lease) {
    MemoryCheck(!writers_.count(identity) && writers_.size()<16,"invalid local output lease identity/budget");
    MemoryCheck(lease.bytes<=32768,"local output length exceeded");
    CheckWritable(lease.owner,lease.key,lease.address,lease.bytes);
    writers_.emplace(identity,lease);
  }
  void CommitWrite(uint64_t identity,const std::vector<uint8_t>& bytes) {
    MemoryCheck(writers_.count(identity),"unknown local output lease commit");
    const auto& w=writers_.at(identity);
    MemoryCheck(bytes.size()==w.bytes,"local output size mismatch");
    // AcquireWrite excludes readers and other writers. Keep the reservation
    // until the caller records the commit and explicitly releases it.
    auto& window=const_cast<Window&>(Range(w.owner,w.key,w.address,w.bytes));
    Write(window,w.address-window.address,bytes);
  }
  void ReleaseWrite(uint64_t identity) {
    MemoryCheck(writers_.erase(identity)==1,"unknown local output lease release");
  }
  const std::map<uint64_t,ReadLease>& WriteLeases() const {return writers_;}
  void CheckWritable(unsigned owner,uint32_t key,uint64_t address,uint64_t bytes) const {
    Range(owner,key,address,bytes);
    for(const auto& item:leases_) {
      const auto& r=item.second;
      if(r.owner==owner)MemoryCheck(address+bytes<=r.address || r.address+r.bytes<=address,
                                   "write overlaps active source read lease");
    }
    for(const auto& item:writers_) {
      const auto& w=item.second;
      if(w.owner==owner)MemoryCheck(address+bytes<=w.address || w.address+w.bytes<=address,
                                   "write overlaps active local output lease");
    }
  }
  // Target-local functional accesses; no GPU coherence or polling delay.
  uint64_t Load64(unsigned owner,uint32_t key,uint64_t address) const {
    CheckReadable(owner,key,address,8);
    const auto& w=LocalWindow(owner,key,address);
    uint64_t value=0;
    for(unsigned i=0;i<8;++i)value|=uint64_t(Byte(w,address-w.address+i))<<(8*i);
    return value;
  }
  void Store64(unsigned owner,uint32_t key,uint64_t address,uint64_t value) {
    const auto& found=LocalWindow(owner,key,address);
    CheckWritable(owner,key,address,8);
    auto& w=windows_.at(static_cast<std::size_t>(&found-windows_.data()));
    std::vector<uint8_t> bytes(8);for(unsigned i=0;i<8;++i)bytes[i]=value>>(8*i);
    Write(w,address-w.address,bytes);
  }
 private:
  static uint8_t Byte(const Window& w,uint64_t offset) {
    if(!w.logical_bytes)return w.bytes.at(offset);
    const auto page=w.pages.find(offset/PageBytes);
    return page==w.pages.end()?0:page->second.at(offset%PageBytes);
  }
  static void Write(Window& w,uint64_t offset,const std::vector<uint8_t>& bytes) {
    MemoryCheck(!bytes.empty() && offset<=Size(w) && bytes.size()<=Size(w)-offset,"invalid memory write range");
    if(!w.logical_bytes) {std::copy(bytes.begin(),bytes.end(),w.bytes.begin()+offset);return;}
    const auto first=offset/PageBytes,last=(offset+bytes.size()-1)/PageBytes;
    uint64_t missing=0;for(auto p=first;p<=last;++p)missing+=!w.pages.count(p);
    MemoryCheck(w.pages.size()+missing<=MaxPages,"sparse resident page budget exceeded");
    // Range/page admission precedes ANY write, including an existing first page.
    for(auto p=first;p<=last;++p)if(!w.pages.count(p))w.pages.emplace(p,std::vector<uint8_t>(PageBytes,0));
    for(uint64_t i=0;i<bytes.size();++i)w.pages.at((offset+i)/PageBytes).at((offset+i)%PageBytes)=bytes[i];
  }
  const Window& Range(unsigned owner,uint32_t key,uint64_t address,uint64_t bytes) const {
    const auto it=std::find_if(windows_.begin(),windows_.end(),[&](const Window& w){return w.owner==owner && w.key==key;});
    MemoryCheck(it!=windows_.end() && bytes>0 && address>=it->address && address-it->address<=Size(*it) &&
                bytes<=Size(*it)-(address-it->address),"invalid source memory range");
    return *it;
  }
  const Window& LocalWindow(unsigned owner,uint32_t key,uint64_t address) const {
    const auto it=std::find_if(windows_.begin(),windows_.end(),[&](const Window& w){return w.owner==owner && w.key==key;});
    MemoryCheck(it!=windows_.end() && address%8==0 && address>=it->address &&
                address-it->address<=Size(*it) && 8<=Size(*it)-(address-it->address),
                "invalid local 64-bit memory access");
    return *it;
  }
  struct Saved {unsigned owner;std::vector<uint8_t> bytes;Result result;};
  struct Context {uint64_t next=0;std::map<uint64_t,Saved> saved;};
  std::vector<Window> windows_;
  std::vector<Registration> registrations_;
  std::map<uint64_t,std::pair<unsigned,unsigned>> context_bindings_;
  bool nic_scoped_=false;
  std::map<uint64_t,ReadLease> leases_;
  std::map<uint64_t,ReadLease> writers_;
  std::map<uint64_t,Context> contexts_;
  uint64_t max_contexts_=8,max_requests_=32,max_readers_=48;
  uint64_t cache_capacity_=0;
  bool carrier_once_put_=false;
  std::map<unsigned,uint64_t> cache_bytes_;
};
}
