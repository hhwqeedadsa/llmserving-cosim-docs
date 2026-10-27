#include "fabric.hh"
#include "rc_wire.hh"
#include "ns3/custom-header.h"
#include "ns3/error-model.h"
#include "ns3/udp-header.h"
#include <fstream>
#include <iostream>
#include <deque>
#include <link.h>

namespace moe_rdma {
using namespace ns3;
using rc::Frame;using rc::Bytes;
static int64_t Now() {return Simulator::Now().GetNanoSeconds();}
static CustomHeader Header(Ptr<const Packet> p) {
  CustomHeader h(CustomHeader::L2_Header|CustomHeader::L3_Header|CustomHeader::L4_Header);p->PeekHeader(h);return h;
}
class RcLoss final: public ErrorModel {
 public:
  static TypeId GetTypeId() {static TypeId id=TypeId("moe_rdma::RcLoss").SetParent<ErrorModel>().AddConstructor<RcLoss>();return id;}
  std::function<bool(Ptr<Packet>)> corrupt;
 private:
  bool DoCorrupt(Ptr<Packet> p) override {return corrupt(p);}
  void DoReset() override {}
};
// Deliberately one in-flight WQE. Native QP counters are scheduler packet
// tickets, NOT RC PSNs, byte windows, measured NIC credit or application ACKs.
class RcPacketProbe {
 public:
  RcPacketProbe(Fabric& fabric,const Json& input):f_(fabric),c_(input) {
    Require(CustomHeader::RoceReferenceEnabled(),"RC parsing must be explicitly enabled");
    Require(!f_.persistent_stream_mode_ && f_.flows_.empty() && f_.fault_rules_.empty() && !f_.recovery_ &&
      !f_.config_.contains("drop_once") && !f_.config_.contains("link_failures") && !f_.resource_tracking_,"RC reference excludes legacy recovery/admission/faults");
    f_.persistent_stream_mode_=true;f_.EnableNicBindings();
    src_=c_.at("src");dst_=c_.at("dst");sn_=c_.at("source_nic");dn_=c_.at("destination_nic");
    plane_=c_.at("plane");mtu_=c_.at("mtu");rto_=c_.at("rto_ns");
    Require(src_<f_.gpus_ && dst_<f_.gpus_ && src_!=dst_,"invalid RC endpoints");
    sip_=f_.NicAddress(src_,plane_,sn_);dip_=f_.NicAddress(dst_,plane_,dn_);
    Require(f_.NicConnectionReachable(src_,sn_,dst_,dn_,plane_) && f_.NicConnectionReachable(dst_,dn_,src_,sn_,plane_),"RC NICs unreachable");
    memory_=rc::Unhex(c_.at("memory_hex"));
    for(unsigned n=0;n<f_.drivers_.size();++n)for(auto& nic:f_.drivers_[n]->m_rdma->m_nic)if(nic.dev) {
      Require(f_.drivers_[n]->m_rdma->m_cc_mode==1,"RC probe rejects INT congestion modes");
      nic.dev->m_rdmaReceiveCb=MakeCallback(&RcPacketProbe::Receive,this,n);
      nic.dev->m_rdmaEQ->m_rdmaGetNxtPkt=MakeCallback(&RcPacketProbe::Next,this,n);
      nic.dev->m_rdmaPktSent=MakeCallback(&RcPacketProbe::Sent,this,n);
    }
    auto hw=f_.drivers_[src_]->m_rdma;
    hw->AddQueuePair(src_,dst_,0,0,3,sip_,dip_,12000,4791,0,f_.config_.at("base_rtt_ns"),MakeCallback(&Fabric::Noop),MakeCallback(&Fabric::Noop));
    qp_=hw->GetQp(dip_.Get(),12000,3);
    Require(qp_ && hw->GetNicIdxOfQp(qp_)==sn_,"RC scheduler NIC binding mismatch");
    for(unsigned n=0;n<f_.ports_.size();++n)for(unsigned port=0;port<f_.ports_[n].size();++port) {
      auto dev=f_.ports_[n][port].device;if(!dev)continue;
      Require(dev->TraceConnectWithoutContext("PhyTxBegin",MakeBoundCallback(&RcPacketProbe::Tx,this,n,port)),"RC Tx trace absent");
      Require(dev->TraceConnectWithoutContext("MacRx",MakeBoundCallback(&RcPacketProbe::Rx,this,n,port)),"RC Rx trace absent");
      Require(dev->TraceConnectWithoutContext("PhyRxDrop",MakeBoundCallback(&RcPacketProbe::Drop,this,n,port)),"RC drop trace absent");
      if(n==src_ || n==dst_) {
        auto loss=CreateObject<RcLoss>();loss->corrupt=[this,n,port](Ptr<Packet> p){return Lose(n,port,p);};
        dev->SetAttribute("ReceiveErrorModel",PointerValue(loss));
      }
    }
  }
  ~RcPacketProbe() {Simulator::Cancel(timer_);}
  void Start() {Issue();}
  Json Report() const {
    return Json{{"policy","atomic_return_required/1"},{"operations",completed_},{"target_effects",effects_},
      {"endpoint_events",endpoint_},{"physical_packets",physical_},{"memory_hex",rc::Hex(memory_)},
      {"target_next_psn",expected_},{"drops",drops_},{"timeouts",timeouts_},{"drained",finished_},
      {"scope","one QP, one in-flight WQE; fixed-rate native scheduler, no RC NIC window or congestion feedback calibration"},
      {"wire_scope","PPP/IPv4/UDP/BTH/extensions/pad/4B zero ICRC placeholder; no task IDs; no reverse response ACK"},
      {"scheduler_counter_unit","packet_tickets_not_RC_PSN_or_bytes"},{"hardware_calibrated",false}};
  }
  bool Passed() const {return finished_ && index_==c_.at("operations").size() && !inflight_ && pending_.empty();}
 private:
  Ptr<Packet> PacketFor(const Frame& frame,bool feedback) {
    auto bytes=rc::Encode(frame);auto p=Create<Packet>(bytes.data(),bytes.size());
    UdpHeader udp;udp.SetSourcePort(feedback?13000:12000);udp.SetDestinationPort(4791);p->AddHeader(udp);
    Ipv4Header ip;ip.SetSource(feedback?dip_:sip_);ip.SetDestination(feedback?sip_:dip_);
    ip.SetProtocol(17);ip.SetTtl(64);ip.SetTos(2);ip.SetPayloadSize(p->GetSize());p->AddHeader(ip);
    f_.drivers_[feedback?dst_:src_]->m_rdma->AddHeader(p,0x800);return p;
  }
  static Frame Payload(Ptr<const Packet> p) {
    auto h=Header(p);Require(h.roce.present,"non-RC packet at RC endpoint");
    // Native header projection excludes data/padding/ICRC but includes BTH/extensions.
    const auto offset=2+20+8;Require(p->GetSize()>=offset+16,"short RC packet");
    Bytes bytes(p->GetSize()-offset);p->CreateFragment(offset,bytes.size())->CopyData(bytes.data(),bytes.size());
    auto frame=rc::Decode(bytes);
    Require(frame.opcode==h.roce.opcode && frame.psn==h.roce.psn && frame.dqpn==h.roce.dqpn,"native RC projection differs from wire codec");return frame;
  }
  void Log(const char* kind,const Frame& p,Ptr<const Packet> packet) {
    Require(endpoint_.size()<10000,"RC endpoint record limit");
    endpoint_.push_back({{"time_ns",Now()},{"kind",kind},{"packet_uid",packet->GetUid()},
      {"body_hex",rc::Hex(rc::Encode(p))},{"expected_psn",expected_},{"completed_operations",index_}});
  }
  void Issue() {
    if(index_==c_.at("operations").size()) {CheckDrain();return;}
    const auto& op=c_.at("operations").at(index_);current_.clear();retries_=0;
    const bool atomic=op.at("kind")=="fetch_add";auto data=atomic?Bytes{}:rc::Unhex(op.at("data_hex"));
    const unsigned count=atomic?1:(data.size()+mtu_-1)/mtu_;first_=next_psn_;
    for(unsigned i=0;i<count;++i) {
      Frame p;p.psn=next_psn_++;p.dqpn=2;
      p.ack=atomic || i==count-1 || noack_>64;noack_=p.ack?0:noack_+1;
      p.opcode=atomic?0x14:count==1?0x0a:i==0?0x06:i==count-1?0x08:0x07;
      if(i==0) {p.address=op.at("address");p.key=1;p.length=data.size();}
      if(atomic)p.addend=op.at("addend");
      else p.data=Bytes(data.begin()+i*mtu_,data.begin()+std::min<std::size_t>(data.size(),(i+1)*mtu_));
      current_.push_back(p);
    }
    last_=next_psn_-1;QueueCurrent();
  }
  void QueueCurrent() {
    Require(pending_.empty(),"RC retry before request queue drained");
    for(const auto& p:current_)pending_.push_back(p);
    qp_->m_size+=current_.size();auto& nic=f_.drivers_[src_]->m_rdma->m_nic[sn_];
    if(std::find(nic.qpGrp->m_qps.begin(),nic.qpGrp->m_qps.end(),qp_)==nic.qpGrp->m_qps.end())nic.qpGrp->AddQp(qp_);
    nic.dev->TriggerTransmit();
  }
  Ptr<Packet> Next(unsigned node,Ptr<RdmaQueuePair> qp) {
    Require(node==src_ && qp==qp_ && !pending_.empty(),"foreign/empty RC scheduler");
    auto frame=pending_.front();pending_.pop_front();++qp->snd_nxt;qp->snd_una=qp->snd_nxt;
    return PacketFor(frame,false);
  }
  void Sent(unsigned node,Ptr<RdmaQueuePair> qp,Ptr<Packet> p,Time gap) {
    Require(node==src_ && qp==qp_,"foreign RC transmission");f_.drivers_[node]->m_rdma->PktSent(qp,p,gap);
    auto frame=Payload(p);Log("request_sent",frame,p);
    // Arm only when a physical source transmission occurs, not at post time.
    if(pending_.empty() && !timer_.IsRunning())timer_=Simulator::Schedule(NanoSeconds(rto_),&RcPacketProbe::Timeout,this);
  }
  void Timeout() {
    Require(index_<c_.at("operations").size() && ++retries_<=3,"RC retry budget exhausted");
    ++timeouts_;QueueCurrent();
  }
  Frame Reply(const Frame&,uint8_t opcode,uint32_t psn) {
    Frame r;r.opcode=opcode;r.psn=psn;r.dqpn=1;r.msn=msn_;return r;
  }
  void SendReply(const Frame& r) {
    auto p=PacketFor(r,true);Log("response_enqueued",r,p);
    auto dev=f_.drivers_[dst_]->m_rdma->m_nic[dn_].dev;dev->RdmaEnqueueHighPrioQ(p);dev->TriggerTransmit();
  }
  void Apply(const Frame& p) {
    Require(p.dqpn==2 && !rc::Feedback(p),"wrong request QPN/opcode");
    if(p.psn>expected_) {if(!sent_nak_) {auto r=Reply(p,0x11,expected_);r.syndrome=0x60;SendReply(r);sent_nak_=true;}return;}
    if(p.psn<expected_) {
      if(p.opcode==0x14) {if(cache_valid_ && cache_.psn==p.psn)SendReply(cache_);}
      else if(expected_)SendReply(Reply(p,0x11,expected_-1));
      return;
    }
    if(p.opcode==0x14) {
      Require(!write_remaining_ && p.address%8==0 && p.key==1 && p.address<=memory_.size()-8,"invalid atomic registered range");
      uint64_t old=0;for(unsigned i=0;i<8;++i)old|=uint64_t(memory_[p.address+i])<<(8*i);
      auto value=old+p.addend;for(unsigned i=0;i<8;++i)memory_[p.address+i]=(value>>(8*i))&255;
      effects_.push_back({{"kind","fetch_add"},{"psn",p.psn},{"old",old}});++msn_;
      cache_=Reply(p,0x12,p.psn);cache_.old=old;cache_valid_=true;SendReply(cache_);
    } else {
      const bool first=p.opcode==0x06 || p.opcode==0x0a,last=p.opcode==0x08 || p.opcode==0x0a;
      if(first) {
        Require(!write_remaining_ && p.key==1 && p.length>0 && p.address<=memory_.size() && p.length<=memory_.size()-p.address,"invalid WRITE registered range");
        write_cursor_=p.address;write_remaining_=p.length;
      }
      const auto n=p.data.size();Require(write_remaining_ && (last?(n==write_remaining_ && n<=mtu_):(n==mtu_ && write_remaining_>n)),"WRITE segmentation mismatch");
      std::copy(p.data.begin(),p.data.end(),memory_.begin()+write_cursor_);
      effects_.push_back({{"kind","write"},{"psn",p.psn},{"address",write_cursor_},{"bytes",n}});
      write_cursor_+=n;write_remaining_-=n;if(last)++msn_;
      if(p.ack)SendReply(Reply(p,0x11,p.psn));
    }
    ++expected_;sent_nak_=false;
  }
  int Receive(unsigned node,Ptr<Packet> packet,CustomHeader& h) {
    Require(h.roce.present,"unsupported packet at RC endpoint");auto p=Payload(packet);
    const bool feedback=rc::Feedback(p);
    Require(node==(feedback?src_:dst_) && h.sip==(feedback?dip_:sip_).Get() && h.dip==(feedback?sip_:dip_).Get(),"RC address direction mismatch");
    Log(feedback?"response_received":"request_received",p,packet);
    if(!feedback) {Apply(p);CheckDrain();return 0;}
    Require(p.dqpn==1 && p.psn<next_psn_,"unknown RC response QPN/PSN");
    if(p.syndrome || p.psn<first_ || index_==c_.at("operations").size()) {CheckDrain();return 0;}
    Require(p.psn<=last_,"RC response crosses current WQE");
    const bool atomic=c_.at("operations").at(index_).at("kind")=="fetch_add";
    if(p.psn<last_)return 0;
    // Plain ACK never manufactures an atomic result: hold source until retry.
    if(atomic && p.opcode!=0x12)return 0;
    Require(atomic || p.opcode==0x11,"Atomic ACK cannot complete WRITE");
    Json done={{"operation",index_},{"completed_ns",Now()},{"last_psn",last_}};
    if(atomic)done["old_value"]=p.old;
    completed_.push_back(done);++index_;Simulator::Cancel(timer_);
    // Complete only after this request batch physically left the source.
    Require(pending_.empty(),"feedback completed before final request emitted");
    Issue();CheckDrain();return 0;
  }
  bool Lose(unsigned node,unsigned port,Ptr<Packet> packet) {
    if(c_.at("loss").is_null() || drops_)return false;
    auto h=Header(packet);if(!h.roce.present)return false;const auto& rule=c_.at("loss");
    if(rule.at("node")==node && rule.at("opcode")==h.roce.opcode && rule.at("psn")==h.roce.psn) {
      ++drops_;losses_.push_back({{"time_ns",Now()},{"node",node},{"port",port},{"packet_uid",packet->GetUid()}});return true;
    }return false;
  }
  static void Tx(RcPacketProbe* a,unsigned node,unsigned port,Ptr<const Packet> p) {++a->inflight_;a->Observe("tx",node,port,p);}
  static void Rx(RcPacketProbe* a,unsigned node,unsigned port,Ptr<const Packet> p) {Require(a->inflight_>0,"RC RX without TX");--a->inflight_;a->Observe("rx",node,port,p);a->CheckDrain();}
  static void Drop(RcPacketProbe* a,unsigned node,unsigned port,Ptr<const Packet> p) {Require(a->inflight_>0,"RC drop without TX");--a->inflight_;a->Observe("drop",node,port,p);a->CheckDrain();}
  void Observe(const char* kind,unsigned node,unsigned port,Ptr<const Packet> p) {
    Require(physical_.size()<20000,"RC physical packet observation limit");
    Bytes bytes(p->GetSize());p->CopyData(bytes.data(),bytes.size());
    physical_.push_back({{"time_ns",Now()},{"kind",kind},{"node",node},{"port",port},{"packet_uid",p->GetUid()},{"packet_hex",rc::Hex(bytes)}});
  }
  void CheckDrain() {
    if(index_!=c_.at("operations").size() || finished_ || drain_pending_)return;
    drain_pending_=true;Simulator::ScheduleNow([this](){drain_pending_=false;
      if(inflight_ || !pending_.empty())return;
      for(const auto& node:f_.ports_)for(const auto& port:node)if(port.device && port.device->GetQueue()->GetNBytesTotal())return;
      for(const auto& driver:f_.drivers_)for(const auto& nic:driver->m_rdma->m_nic)if(nic.dev && nic.dev->GetRdmaQueue()->m_ackQ->GetNPackets())return;
      f_.drivers_[src_]->m_rdma->DeleteQueuePair(qp_);finished_=true;Simulator::Stop();
    });
  }
  Fabric& f_;Json c_;Bytes memory_;Ptr<RdmaQueuePair> qp_;Ipv4Address sip_,dip_;
  unsigned src_=0,dst_=0,sn_=0,dn_=0,plane_=0,mtu_=256,index_=0,retries_=0,noack_=0,drops_=0,timeouts_=0;
  uint32_t next_psn_=0,expected_=0,first_=0,last_=0,msn_=0;uint64_t write_cursor_=0,write_remaining_=0;
  int64_t rto_=0,inflight_=0;EventId timer_;std::vector<Frame> current_;std::deque<Frame> pending_;
  Frame cache_;bool cache_valid_=false,sent_nak_=false,finished_=false,drain_pending_=false;
  Json completed_=Json::array(),effects_=Json::array(),endpoint_=Json::array(),physical_=Json::array(),losses_=Json::array();
};
static int Libraries(dl_phdr_info* info,std::size_t,void* data) {
  if(info->dlpi_name && info->dlpi_name[0])static_cast<Json*>(data)->push_back(info->dlpi_name);return 0;
}
static void ValidateInput(const Json& c) {
  Require(c.is_object() && c.size()==11 && c.at("schema_version")=="moe-rc-packet-probe/1","invalid RC input fields/schema");
  auto number=[](const Json& v,uint64_t high) {
    Require(v.is_number_unsigned() || (v.is_number_integer() && v>=0),"RC integer required");
    Require(v.get<uint64_t>()<=high,"RC integer bound");return v.get<uint64_t>();
  };
  for(auto key:{"src","dst","source_nic","destination_nic","plane"})number(c.at(key),65535);
  const auto mtu=number(c.at("mtu"),4096);Require(mtu==256 || mtu==512 || mtu==1024 || mtu==2048 || mtu==4096,"invalid RC MTU");
  Require(number(c.at("rto_ns"),1000000000)>0,"positive RC RTO required");
  Require(c.at("memory_hex").is_string() && c.at("memory_hex").get_ref<const std::string&>().size()<=131072,"RC memory bound");
  const auto memory=rc::Unhex(c.at("memory_hex"));Require(memory.size()>=8,"RC registered region too short");
  const auto& ops=c.at("operations");Require(ops.is_array() && !ops.empty() && ops.size()<=64,"RC operation count");
  uint64_t bytes=0;
  for(const auto& op:ops) {
    const auto kind=op.at("kind").get<std::string>();Require(op.size()==3 && (kind=="write" || kind=="fetch_add"),"invalid RC operation fields");
    const auto address=number(op.at("address"),memory.size()-1);
    if(kind=="write") {
      Require(op.at("data_hex").is_string() && op.at("data_hex").get_ref<const std::string&>().size()<=131072,"RC write size bound");
      const auto data=rc::Unhex(op.at("data_hex"));bytes+=data.size();
      Require(!data.empty() && bytes<=65536 && data.size()<=memory.size()-address,"RC write range/byte bound");
    } else {
      number(op.at("addend"),UINT64_MAX);Require(address%8==0 && address<=memory.size()-8,"RC atomic range/alignment");
    }
  }
  Require(c.at("loss").is_null() || (c.at("loss").is_object() && c.at("loss").size()==3),"invalid RC loss fields");
  if(!c.at("loss").is_null()) {
    number(c.at("loss").at("node"),65535);number(c.at("loss").at("opcode"),255);number(c.at("loss").at("psn"),0xfffffe);
  }
}
}
int main(int argc,char** argv) {
  using namespace moe_rdma;
  try {
    Require(argc==5,"usage: moe-rdma-rc-probe TOPOLOGY.json RDMA.json WORK.json NEW_RESULT.json");
    std::ifstream exists(argv[4]);Require(!exists.good(),"RC output already exists");
    ns3::Time::SetResolution(ns3::Time::NS);auto config=ReadJson(argv[2]),input=ReadJson(argv[3]);
    ValidateInput(input);
    ns3::RngSeedManager::SetSeed(config.at("seed"));ns3::CustomHeader::SetRoceReference(true);
    Fabric fabric(ReadJson(argv[1]),config);RcPacketProbe probe(fabric,input);
    ns3::Simulator::ScheduleNow(&RcPacketProbe::Start,&probe);
    ns3::Simulator::Stop(ns3::NanoSeconds(fabric.MaxSimNs()));ns3::Simulator::Run();
    auto result=fabric.Report();result["rc_reference"]=probe.Report();result["passed"]=probe.Passed() && fabric.Drained();
    result["loaded_libraries"]=Json::array();dl_iterate_phdr(Libraries,&result["loaded_libraries"]);
    std::ofstream out(argv[4]);out<<result.dump()<<'\n';out.close();Require(bool(out),"cannot save RC result");
    ns3::Simulator::Destroy();ns3::CustomHeader::SetRoceReference(false);
    Require(result.at("passed"),"incomplete RC probe; inspect result");return 0;
  }catch(const std::exception& e) {
    std::cerr<<e.what()<<'\n';ns3::Simulator::Destroy();ns3::CustomHeader::SetRoceReference(false);return 1;
  }
}
