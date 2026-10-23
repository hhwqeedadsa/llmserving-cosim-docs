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
  struct Window {unsigned owner;uint32_t key;uint64_t address;bool atomic;std::vector<uint8_t> bytes;};
  struct Result {bool duplicate;bool atomic;uint64_t old_value;};
  struct ReadLease {unsigned owner;uint32_t key;uint64_t address,bytes;};
  void Register(Window window) {
    MemoryCheck(windows_.size()<16 && window.key && !window.bytes.empty() && window.bytes.size()<=65536 &&
                window.address<=UINT64_MAX-window.bytes.size(),"invalid remote memory window");
    for(const auto& w:windows_)if(w.owner==window.owner) {
      MemoryCheck(w.key!=window.key,"remote key repeated for owner");
      MemoryCheck(window.address+window.bytes.size()<=w.address || w.address+w.bytes.size()<=window.address,
                  "overlapping remote memory windows");
    }
    windows_.push_back(std::move(window));
  }
  // Functional model: serial application on the ns-3 event clock; LE64 memory
  // atomics; retained request/result cache for this bounded connection lifetime.
  // It does NOT implement PSNs, retry timers, GPU coherence or remote NIC delay.
  Result Apply(uint64_t context,unsigned owner,const MemoryFrame& frame) {
    MemoryCheck(frame.verb==MemoryFrame::Put || frame.verb==MemoryFrame::FetchAdd,"target received non-request");
    const auto encoded=EncodeMemoryFrame(frame);
    MemoryCheck(contexts_.count(context) || contexts_.size()<8,"remote memory context budget exceeded");
    auto& state=contexts_[context];
    if(frame.sequence<state.next) {
      const auto& saved=state.saved.at(frame.sequence);
      MemoryCheck(saved.owner==owner && saved.bytes==encoded,"conflicting duplicate memory request");
      return {true,saved.result.atomic,saved.result.old_value};
    }
    MemoryCheck(frame.sequence==state.next && state.next<32,"memory request sequence gap/budget exceeded");
    auto it=std::find_if(windows_.begin(),windows_.end(),[&](const Window& w){return w.owner==owner && w.key==frame.key;});
    MemoryCheck(it!=windows_.end(),"unknown remote memory key/owner");
    const auto length=frame.verb==MemoryFrame::Put?frame.body.size():8;
    MemoryCheck(frame.address>=it->address && frame.address-it->address<=it->bytes.size() &&
                length<=it->bytes.size()-(frame.address-it->address),"remote memory address out of bounds");
    const auto offset=frame.address-it->address;
    if(frame.verb==MemoryFrame::FetchAdd)MemoryCheck(it->atomic && frame.address%8==0,"remote atomic denied/unaligned");
    CheckWritable(owner,frame.key,frame.address,length);
    Result result{false,frame.verb==MemoryFrame::FetchAdd,0};
    if(result.atomic) {
      MemoryCheck(it->atomic && frame.address%8==0,"remote atomic denied/unaligned");
      for(unsigned i=0;i<8;++i)result.old_value|=uint64_t(it->bytes[offset+i])<<(8*i);
      const uint64_t updated=result.old_value+MemoryRead(frame.body.data(),8);
      for(unsigned i=0;i<8;++i)it->bytes[offset+i]=updated>>(8*i);
    } else std::copy(frame.body.begin(),frame.body.end(),it->bytes.begin()+offset);
    state.saved.emplace(frame.sequence,Saved{owner,encoded,result});++state.next;
    return result;
  }
  const std::vector<Window>& Windows() const {return windows_;}
  std::vector<uint8_t> ReadBytes(unsigned owner,uint32_t key,uint64_t address,uint64_t bytes) const {
    CheckReadable(owner,key,address,bytes);
    const auto& w=Range(owner,key,address,bytes);
    MemoryCheck(bytes<=32768,"source read length exceeded");
    return {w.bytes.begin()+(address-w.address),w.bytes.begin()+(address-w.address)+bytes};
  }
  void AcquireRead(uint64_t identity,ReadLease lease) {
    MemoryCheck(!leases_.count(identity) && leases_.size()<48,"invalid source read lease identity/budget");
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
    std::copy(bytes.begin(),bytes.end(),window.bytes.begin()+(w.address-window.address));
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
    for(unsigned i=0;i<8;++i)value|=uint64_t(w.bytes[address-w.address+i])<<(8*i);
    return value;
  }
  void Store64(unsigned owner,uint32_t key,uint64_t address,uint64_t value) {
    const auto& found=LocalWindow(owner,key,address);
    CheckWritable(owner,key,address,8);
    auto& w=windows_.at(static_cast<std::size_t>(&found-windows_.data()));
    for(unsigned i=0;i<8;++i)w.bytes[address-w.address+i]=value>>(8*i);
  }
 private:
  const Window& Range(unsigned owner,uint32_t key,uint64_t address,uint64_t bytes) const {
    const auto it=std::find_if(windows_.begin(),windows_.end(),[&](const Window& w){return w.owner==owner && w.key==key;});
    MemoryCheck(it!=windows_.end() && bytes>0 && address>=it->address && address-it->address<=it->bytes.size() &&
                bytes<=it->bytes.size()-(address-it->address),"invalid source memory range");
    return *it;
  }
  const Window& LocalWindow(unsigned owner,uint32_t key,uint64_t address) const {
    const auto it=std::find_if(windows_.begin(),windows_.end(),[&](const Window& w){return w.owner==owner && w.key==key;});
    MemoryCheck(it!=windows_.end() && address%8==0 && address>=it->address &&
                address-it->address<=it->bytes.size() && 8<=it->bytes.size()-(address-it->address),
                "invalid local 64-bit memory access");
    return *it;
  }
  struct Saved {unsigned owner;std::vector<uint8_t> bytes;Result result;};
  struct Context {uint64_t next=0;std::map<uint64_t,Saved> saved;};
  std::vector<Window> windows_;
  std::map<uint64_t,ReadLease> leases_;
  std::map<uint64_t,ReadLease> writers_;
  std::map<uint64_t,Context> contexts_;
};
}
