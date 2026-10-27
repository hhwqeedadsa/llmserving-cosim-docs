#pragma once
// Bounded RC reference wire codec, not a NIC-compatible CRC implementation.
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace moe_rdma::rc {
using Bytes = std::vector<uint8_t>;
inline void Check(bool ok,const char* message) { if(!ok)throw std::runtime_error(message); }
inline uint64_t Read(const Bytes& b,std::size_t& p,unsigned n) {
  Check(n<=8 && p<=b.size() && n<=b.size()-p,"truncated RC field");
  uint64_t v=0;while(n--)v=(v<<8)|b[p++];return v;
}
inline void Put(Bytes& b,uint64_t v,unsigned n) {
  for(unsigned i=n;i;--i)b.push_back((v>>((i-1)*8))&255);
}
inline Bytes Unhex(const std::string& s) {
  Check(s.size()%2==0,"odd hex length");Bytes b;
  auto digit=[](char c)->unsigned {
    if(c>='0' && c<='9')return c-'0';
    if(c>='a' && c<='f')return c-'a'+10;
    throw std::runtime_error("expected lowercase hex");
  };
  for(std::size_t i=0;i<s.size();i+=2)b.push_back((digit(s[i])<<4)|digit(s[i+1]));return b;
}
inline std::string Hex(const Bytes& b) {
  const char* d="0123456789abcdef";std::string s;s.reserve(2*b.size());
  for(auto v:b){s+=d[v>>4];s+=d[v&15];}return s;
}
struct Frame {
  uint8_t opcode=0,syndrome=0;
  bool ack=false;
  uint32_t psn=0,dqpn=0,key=0,length=0,msn=0;
  uint64_t address=0,addend=0,old=0;
  Bytes data;
};
inline bool Feedback(const Frame& f) {return f.opcode==0x11 || f.opcode==0x12;}
inline Bytes Encode(const Frame& f) {
  Check(f.psn<0xffffff && f.dqpn>0 && f.dqpn<=0xffffff && f.msn<=0xffffff,"RC sequence/QPN bound");
  const auto pad=(4-f.data.size()%4)%4;Bytes b;
  Put(b,f.opcode,1);Put(b,pad<<4,1);Put(b,0xffff,2);Put(b,f.dqpn,4);
  Put(b,(uint64_t(f.ack)<<31)|f.psn,4);
  switch(f.opcode) {
    case 0x06: case 0x0a: Put(b,f.address,8);Put(b,f.key,4);Put(b,f.length,4);break;
    case 0x07: case 0x08: break;
    case 0x14: Put(b,f.address,8);Put(b,f.key,4);Put(b,f.addend,8);Put(b,0,8);break;
    case 0x11: case 0x12:
      Put(b,(uint64_t(f.syndrome)<<24)|f.msn,4);
      if(f.opcode==0x12)Put(b,f.old,8);break;
    default: throw std::runtime_error("unsupported RC operation");
  }
  Check((f.opcode!=0x14 && !Feedback(f)) || f.data.empty(),"control/atomic must have no data payload");
  b.insert(b.end(),f.data.begin(),f.data.end());b.resize(b.size()+pad+4,0); // ICRC placeholder
  return b;
}
inline Frame Decode(const Bytes& b) {
  Check(b.size()>=16 && b.size()%4==0,"invalid RC body size");std::size_t p=0;Frame f;
  f.opcode=Read(b,p,1);const auto flags=Read(b,p,1);const auto pad=(flags>>4)&3;
  Check((flags&0xcf)==0 && Read(b,p,2)==0xffff,"unsupported BTH flags/P_Key");
  auto word=Read(b,p,4);Check(word>0 && word<=0xffffff,"unsupported DQPN/reserved bits");f.dqpn=word;
  word=Read(b,p,4);Check(!(word&0x7f000000) && (word&0xffffff)<0xffffff,"unsupported PSN/reserved bits");
  f.ack=word>>31;f.psn=word&0xffffff;
  switch(f.opcode) {
    case 0x06: case 0x0a: f.address=Read(b,p,8);f.key=Read(b,p,4);f.length=Read(b,p,4);break;
    case 0x07: case 0x08: break;
    case 0x14: f.address=Read(b,p,8);f.key=Read(b,p,4);f.addend=Read(b,p,8);Check(Read(b,p,8)==0,"unsupported compare operand");break;
    case 0x11: case 0x12:
      word=Read(b,p,4);f.syndrome=word>>24;f.msn=word&0xffffff;
      Check(f.syndrome==0 || (f.opcode==0x11 && f.syndrome==0x60),"unsupported AETH syndrome");
      if(f.opcode==0x12)f.old=Read(b,p,8);break;
    default: throw std::runtime_error("unsupported RC opcode");
  }
  Check(p+pad+4<=b.size(),"truncated RC extension/padding/ICRC");
  const auto end=b.size()-pad-4;
  f.data=Bytes(b.begin()+p,b.begin()+end);
  for(std::size_t i=end;i<b.size();++i)Check(b[i]==0,"nonzero reference padding/ICRC");
  Check((f.opcode!=0x14 && !Feedback(f)) || f.data.empty(),"control/atomic payload unsupported");
  Check(!Feedback(f) || (!pad && !f.ack),"invalid feedback flags");return f;
}
}
