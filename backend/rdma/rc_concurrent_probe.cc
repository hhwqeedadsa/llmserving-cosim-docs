#include "fabric.hh"
#include "rc_wire.hh"
#include "ns3/custom-header.h"
#include "ns3/error-model.h"
#include "ns3/udp-header.h"
#include <deque>
#include <fstream>
#include <iostream>
#include <link.h>

namespace moe_rdma {
using namespace ns3;
using rc::Bytes; using rc::Frame;
static int64_t Now() {return Simulator::Now().GetNanoSeconds();}
static CustomHeader Header(Ptr<const Packet> p) {
  CustomHeader h(CustomHeader::L2_Header|CustomHeader::L3_Header|CustomHeader::L4_Header);p->PeekHeader(h);return h;
}
class RcConcurrentLoss final:public ErrorModel {
 public:
  static TypeId GetTypeId() {static TypeId id=TypeId("moe_rdma::RcConcurrentLoss").SetParent<ErrorModel>().AddConstructor<RcConcurrentLoss>();return id;}
  std::function<bool(Ptr<Packet>)> corrupt;
 private:
  bool DoCorrupt(Ptr<Packet> p) override {return corrupt(p);}
  void DoReset() override {}
};

// Each executable defines its own Fabric-friend RcPacketProbe. This independent
// target does not link the single-QP probe translation unit or change its ABI.
class RcPacketProbe {
  struct Qp {
    Json input;
    unsigned id=0,src=0,dst=0,sn=0,dn=0,plane=0,index=0,retries=0,noack=0,timeouts=0;
    uint32_t next=0,expected=0,first=0,last=0,msn=0;
    uint64_t cursor=0,remaining=0;
    Ipv4Address sip,dip;
    Ptr<RdmaQueuePair> scheduler;
    EventId timer;
    std::vector<Frame> current;
    std::deque<Frame> pending;
    Frame cache;
    bool cached=false,sent_nak=false;
    Json completed=Json::array();
  };
 public:
  RcPacketProbe(Fabric& fabric,const Json& input):f_(fabric),input_(input) {
    Require(CustomHeader::RoceReferenceEnabled(),"RC parser not enabled");
    Require(!f_.persistent_stream_mode_ && f_.flows_.empty() && f_.fault_rules_.empty() && !f_.recovery_ &&
      !f_.config_.contains("drop_once") && !f_.config_.contains("link_failures") && !f_.resource_tracking_,"RC excludes legacy transport controls");
    f_.persistent_stream_mode_=true;f_.EnableNicBindings();
    mtu_=input_.at("mtu");rto_=input_.at("rto_ns");
    for(const auto& m:input_.at("memories")) {
      const unsigned node=m.at("gpu");Require(node<f_.gpus_,"registered GPU outside topology");
      memory_[node]=rc::Unhex(m.at("data_hex"));
    }
    for(unsigned n=0;n<f_.drivers_.size();++n)for(auto& nic:f_.drivers_[n]->m_rdma->m_nic)if(nic.dev) {
      Require(f_.drivers_[n]->m_rdma->m_cc_mode==1,"RC rejects INT congestion modes");
      nic.dev->m_rdmaReceiveCb=MakeCallback(&RcPacketProbe::Receive,this,n);
      nic.dev->m_rdmaEQ->m_rdmaGetNxtPkt=MakeCallback(&RcPacketProbe::Next,this,n);
      nic.dev->m_rdmaPktSent=MakeCallback(&RcPacketProbe::Sent,this,n);
    }
    qps_.reserve(input_.at("connections").size());
    for(const auto& row:input_.at("connections")) {
      Qp q;q.input=row;q.id=row.at("id");q.src=row.at("src");q.dst=row.at("dst");
      q.sn=row.at("source_nic");q.dn=row.at("destination_nic");q.plane=row.at("plane");
      Require(q.src<f_.gpus_ && q.dst<f_.gpus_ && q.src!=q.dst,"invalid RC endpoints");
      Require(memory_.count(q.src) && memory_.count(q.dst),"missing registered endpoint memory");
      q.sip=f_.NicAddress(q.src,q.plane,q.sn);q.dip=f_.NicAddress(q.dst,q.plane,q.dn);
      Require(f_.NicConnectionReachable(q.src,q.sn,q.dst,q.dn,q.plane) &&
        f_.NicConnectionReachable(q.dst,q.dn,q.src,q.sn,q.plane),"RC NICs unreachable");
      auto hw=f_.drivers_[q.src]->m_rdma;
      hw->AddQueuePair(q.src,q.dst,0,0,3,q.sip,q.dip,12000+q.id,4791,0,
        f_.config_.at("base_rtt_ns"),MakeCallback(&Fabric::Noop),MakeCallback(&Fabric::Noop));
      q.scheduler=hw->GetQp(q.dip.Get(),12000+q.id,3);
      Require(q.scheduler && hw->GetNicIdxOfQp(q.scheduler)==q.sn,"RC scheduler NIC binding mismatch");
      const unsigned index=qps_.size();
      Require(routes_.emplace(std::make_pair(q.src,2*q.id+1),std::make_pair(index,true)).second &&
        routes_.emplace(std::make_pair(q.dst,2*q.id+2),std::make_pair(index,false)).second,"duplicate RC endpoint QPN");
      Require(schedulers_.emplace(PeekPointer(q.scheduler),index).second,"duplicate RC scheduler");
      qps_.push_back(std::move(q));
    }
    for(unsigned n=0;n<f_.ports_.size();++n)for(unsigned port=0;port<f_.ports_[n].size();++port) {
      auto dev=f_.ports_[n][port].device;if(!dev)continue;
      Require(dev->TraceConnectWithoutContext("PhyTxBegin",MakeBoundCallback(&RcPacketProbe::Tx,this,n,port)),"RC Tx trace absent");
      Require(dev->TraceConnectWithoutContext("MacRx",MakeBoundCallback(&RcPacketProbe::Rx,this,n,port)),"RC Rx trace absent");
      Require(dev->TraceConnectWithoutContext("PhyRxDrop",MakeBoundCallback(&RcPacketProbe::Drop,this,n,port)),"RC drop trace absent");
      if(memory_.count(n)) {
        auto model=CreateObject<RcConcurrentLoss>();
        model->corrupt=[this,n,port](Ptr<Packet> p){return Lose(n,port,p);};
        dev->SetAttribute("ReceiveErrorModel",PointerValue(model));
      }
    }
  }
  ~RcPacketProbe() {for(auto& q:qps_)Simulator::Cancel(q.timer);}
  void Start() {for(auto& q:qps_)Issue(q);}
  bool Passed() const {return finished_ && AllComplete() && inflight_==0;}
  Json Report() const {
    Json states=Json::array(),memories=Json::array();
    for(const auto& q:qps_)states.push_back({{"id",q.id},{"src",q.src},{"dst",q.dst},
      {"source_qpn",2*q.id+1},{"destination_qpn",2*q.id+2},{"source_port",12000+q.id},
      {"target_next_psn",q.expected},{"timeouts",q.timeouts},{"operations",q.completed}});
    for(const auto& m:memory_)memories.push_back({{"gpu",m.first},{"data_hex",rc::Hex(m.second)}});
    return {{"schema_version","moe-rc-concurrent-result/1"},{"policy","atomic_return_required/1"},
      {"connections",states},{"memories",memories},{"target_effects",effects_},{"endpoint_events",endpoint_},
      {"physical_packets",physical_},{"losses",losses_},{"drops",drops_},{"drained",finished_},
      {"scope","concurrent QPs; one in-flight WQE per QP; shared actual Fabric/NIC/target bytes; fixed rate"},
      {"wire_scope","PPP/IPv4/UDP/BTH/extensions/pad/zero ICRC placeholder; no task IDs or response ACK"},
      {"scheduler_counter_unit","packet_tickets_not_RC_PSN_or_bytes"},{"hardware_calibrated",false}};
  }
 private:
  bool AllComplete() const {for(const auto& q:qps_)if(q.index!=q.input.at("operations").size() || !q.pending.empty())return false;return true;}
  Ptr<Packet> PacketFor(const Qp& q,const Frame& frame,bool feedback) {
    auto bytes=rc::Encode(frame);auto packet=Create<Packet>(bytes.data(),bytes.size());
    UdpHeader udp;udp.SetSourcePort((feedback?13000:12000)+q.id);udp.SetDestinationPort(4791);packet->AddHeader(udp);
    Ipv4Header ip;ip.SetSource(feedback?q.dip:q.sip);ip.SetDestination(feedback?q.sip:q.dip);
    ip.SetProtocol(17);ip.SetTtl(64);ip.SetTos(2);ip.SetPayloadSize(packet->GetSize());packet->AddHeader(ip);
    f_.drivers_[feedback?q.dst:q.src]->m_rdma->AddHeader(packet,0x800);return packet;
  }
  static Frame Payload(Ptr<const Packet> packet) {
    auto h=Header(packet);Require(h.roce.present && packet->GetSize()>=46,"short/non-RC packet");
    Bytes body(packet->GetSize()-30);packet->CreateFragment(30,body.size())->CopyData(body.data(),body.size());
    auto p=rc::Decode(body);
    Require(p.opcode==h.roce.opcode && p.psn==h.roce.psn && p.dqpn==h.roce.dqpn,"native RC projection differs from bytes");return p;
  }
  void Log(const char* kind,const Qp& q,const Frame& p,Ptr<const Packet> packet) {
    Require(endpoint_.size()<20000,"RC endpoint record limit");
    endpoint_.push_back({{"time_ns",Now()},{"kind",kind},{"connection",q.id},{"packet_uid",packet->GetUid()},
      {"body_hex",rc::Hex(rc::Encode(p))},{"expected_psn",q.expected},{"completed_operations",q.index}});
  }
  void Issue(Qp& q) {
    if(q.index==q.input.at("operations").size()){CheckDrain();return;}
    const auto& op=q.input.at("operations").at(q.index);q.current.clear();q.retries=0;
    const bool atomic=op.at("kind")=="fetch_add";const auto data=atomic?Bytes{}:rc::Unhex(op.at("data_hex"));
    const unsigned count=atomic?1:(data.size()+mtu_-1)/mtu_;q.first=q.next;
    for(unsigned i=0;i<count;++i) {
      Frame p;p.psn=q.next++;p.dqpn=2*q.id+2;
      p.ack=atomic || i==count-1 || q.noack>64;q.noack=p.ack?0:q.noack+1;
      p.opcode=atomic?0x14:count==1?0x0a:i==0?0x06:i==count-1?0x08:0x07;
      if(i==0){p.address=op.at("address");p.key=1;p.length=data.size();}
      if(atomic)p.addend=op.at("addend");
      else p.data=Bytes(data.begin()+i*mtu_,data.begin()+std::min<std::size_t>(data.size(),(i+1)*mtu_));
      q.current.push_back(p);
    }
    q.last=q.next-1;Queue(q);
  }
  void Queue(Qp& q) {
    Require(q.pending.empty(),"RC retry before source queue drained");
    for(const auto& p:q.current)q.pending.push_back(p);
    q.scheduler->m_size+=q.current.size();auto& nic=f_.drivers_[q.src]->m_rdma->m_nic[q.sn];
    if(std::find(nic.qpGrp->m_qps.begin(),nic.qpGrp->m_qps.end(),q.scheduler)==nic.qpGrp->m_qps.end())nic.qpGrp->AddQp(q.scheduler);
    nic.dev->TriggerTransmit();
  }
  Qp& Scheduled(unsigned node,Ptr<RdmaQueuePair> qp) {
    auto it=schedulers_.find(PeekPointer(qp));Require(it!=schedulers_.end(),"foreign RC scheduler");
    auto& q=qps_[it->second];Require(node==q.src,"wrong RC source scheduler");return q;
  }
  Ptr<Packet> Next(unsigned node,Ptr<RdmaQueuePair> qp) {
    auto& q=Scheduled(node,qp);Require(!q.pending.empty(),"empty RC scheduler");
    auto p=q.pending.front();q.pending.pop_front();++qp->snd_nxt;qp->snd_una=qp->snd_nxt;
    return PacketFor(q,p,false);
  }
  void Sent(unsigned node,Ptr<RdmaQueuePair> qp,Ptr<Packet> packet,Time gap) {
    auto& q=Scheduled(node,qp);f_.drivers_[node]->m_rdma->PktSent(qp,packet,gap);Log("request_sent",q,Payload(packet),packet);
    if(q.pending.empty() && !q.timer.IsRunning()) {
      const auto index=schedulers_.at(PeekPointer(qp));
      q.timer=Simulator::Schedule(NanoSeconds(rto_),[this,index](){auto& next=qps_[index];
        Require(next.index<next.input.at("operations").size() && ++next.retries<=3,"RC retry budget exhausted");
        ++next.timeouts;Queue(next);});
    }
  }
  Frame Reply(const Qp& q,uint8_t opcode,uint32_t psn) {Frame p;p.opcode=opcode;p.psn=psn;p.dqpn=2*q.id+1;p.msn=q.msn;return p;}
  void SendReply(Qp& q,const Frame& p) {
    auto packet=PacketFor(q,p,true);Log("response_enqueued",q,p,packet);
    auto device=f_.drivers_[q.dst]->m_rdma->m_nic[q.dn].dev;device->RdmaEnqueueHighPrioQ(packet);device->TriggerTransmit();
  }
  void Apply(Qp& q,const Frame& p) {
    Require(p.dqpn==2*q.id+2 && !rc::Feedback(p),"wrong RC request QPN/opcode");
    if(p.psn>q.expected){if(!q.sent_nak){auto r=Reply(q,0x11,q.expected);r.syndrome=0x60;SendReply(q,r);q.sent_nak=true;}return;}
    if(p.psn<q.expected){if(p.opcode==0x14){if(q.cached && q.cache.psn==p.psn)SendReply(q,q.cache);}
      else if(q.expected)SendReply(q,Reply(q,0x11,q.expected-1));return;}
    auto& memory=memory_.at(q.dst);
    if(p.opcode==0x14) {
      Require(!q.remaining && p.address%8==0 && p.key==1 && p.address<=memory.size()-8,"invalid atomic range");
      uint64_t old=0;for(unsigned i=0;i<8;++i)old|=uint64_t(memory[p.address+i])<<(8*i);
      const auto value=old+p.addend;for(unsigned i=0;i<8;++i)memory[p.address+i]=(value>>(8*i))&255;
      effects_.push_back({{"kind","fetch_add"},{"connection",q.id},{"psn",p.psn},{"old",old}});++q.msn;
      q.cache=Reply(q,0x12,p.psn);q.cache.old=old;q.cached=true;SendReply(q,q.cache);
    } else {
      const bool first=p.opcode==0x06 || p.opcode==0x0a,last=p.opcode==0x08 || p.opcode==0x0a;
      if(first){Require(!q.remaining && p.key==1 && p.length>0 && p.address<=memory.size() && p.length<=memory.size()-p.address,"invalid WRITE range");
        q.cursor=p.address;q.remaining=p.length;}
      const auto n=p.data.size();Require(q.remaining && (last?(n==q.remaining && n<=mtu_):(n==mtu_ && q.remaining>n)),"WRITE segmentation mismatch");
      std::copy(p.data.begin(),p.data.end(),memory.begin()+q.cursor);
      effects_.push_back({{"kind","write"},{"connection",q.id},{"psn",p.psn},{"address",q.cursor},{"bytes",n}});
      q.cursor+=n;q.remaining-=n;if(last)++q.msn;if(p.ack)SendReply(q,Reply(q,0x11,p.psn));
    }
    ++q.expected;q.sent_nak=false;
  }
  int Receive(unsigned node,Ptr<Packet> packet,CustomHeader& h) {
    Require(h.roce.present,"non-RC endpoint packet");auto p=Payload(packet);
    auto route=routes_.find({node,p.dqpn});Require(route!=routes_.end(),"unknown endpoint/QPN");
    auto& q=qps_[route->second.first];const bool feedback=route->second.second;
    Require(feedback==rc::Feedback(p) && h.sip==(feedback?q.dip:q.sip).Get() &&
      h.dip==(feedback?q.sip:q.dip).Get(),"RC direction/connection mismatch");
    Log(feedback?"response_received":"request_received",q,p,packet);
    if(!feedback){Apply(q,p);CheckDrain();return 0;}
    Require(p.psn<q.next,"RC response PSN not issued");
    if(p.syndrome || p.psn<q.first || q.index==q.input.at("operations").size()){CheckDrain();return 0;}
    Require(p.psn<=q.last,"RC response crosses current WQE");
    const bool atomic=q.input.at("operations").at(q.index).at("kind")=="fetch_add";
    if(p.psn<q.last || (atomic && p.opcode!=0x12))return 0;
    Require(atomic || p.opcode==0x11,"Atomic ACK cannot complete WRITE");
    Require(q.pending.empty(),"feedback before final request physically emitted");
    Json done={{"operation",q.index},{"completed_ns",Now()},{"last_psn",q.last}};if(atomic)done["old_value"]=p.old;
    q.completed.push_back(done);++q.index;Simulator::Cancel(q.timer);Issue(q);CheckDrain();return 0;
  }
  bool Lose(unsigned node,unsigned port,Ptr<Packet> packet) {
    if(input_.at("loss").is_null() || drops_)return false;
    const auto h=Header(packet);if(!h.roce.present)return false;
    auto route=routes_.find({node,h.roce.dqpn});if(route==routes_.end())return false;
    const auto& q=qps_[route->second.first];const auto& rule=input_.at("loss");
    if(rule.at("connection")==q.id && rule.at("node")==node && rule.at("opcode")==h.roce.opcode && rule.at("psn")==h.roce.psn){
      ++drops_;losses_.push_back({{"time_ns",Now()},{"connection",q.id},{"node",node},{"port",port},{"packet_uid",packet->GetUid()}});return true;}
    return false;
  }
  static void Tx(RcPacketProbe* a,unsigned n,unsigned p,Ptr<const Packet> packet){++a->inflight_;a->Observe("tx",n,p,packet);}
  static void Rx(RcPacketProbe* a,unsigned n,unsigned p,Ptr<const Packet> packet){Require(a->inflight_>0,"RC RX without TX");--a->inflight_;a->Observe("rx",n,p,packet);a->CheckDrain();}
  static void Drop(RcPacketProbe* a,unsigned n,unsigned p,Ptr<const Packet> packet){Require(a->inflight_>0,"RC drop without TX");--a->inflight_;a->Observe("drop",n,p,packet);a->CheckDrain();}
  void Observe(const char* kind,unsigned node,unsigned port,Ptr<const Packet> packet) {
    Require(physical_.size()<80000,"RC physical record limit");Bytes data(packet->GetSize());packet->CopyData(data.data(),data.size());
    physical_.push_back({{"time_ns",Now()},{"kind",kind},{"node",node},{"port",port},{"packet_uid",packet->GetUid()},{"packet_hex",rc::Hex(data)}});
  }
  void CheckDrain() {
    if(!AllComplete() || finished_ || checking_)return;
    checking_=true;Simulator::ScheduleNow([this](){checking_=false;if(inflight_)return;
      for(const auto& node:f_.ports_)for(const auto& port:node)if(port.device && port.device->GetQueue()->GetNBytesTotal())return;
      for(const auto& driver:f_.drivers_)for(const auto& nic:driver->m_rdma->m_nic)if(nic.dev && nic.dev->GetRdmaQueue()->m_ackQ->GetNPackets())return;
      for(auto& q:qps_)f_.drivers_[q.src]->m_rdma->DeleteQueuePair(q.scheduler);
      finished_=true;Simulator::Stop();});
  }
  Fabric& f_;Json input_;std::vector<Qp> qps_;std::map<unsigned,Bytes> memory_;
  std::map<std::pair<unsigned,unsigned>,std::pair<unsigned,bool>> routes_;
  std::map<RdmaQueuePair*,unsigned> schedulers_;
  unsigned mtu_=256,drops_=0;int64_t rto_=0,inflight_=0;bool finished_=false,checking_=false;
  Json endpoint_=Json::array(),effects_=Json::array(),physical_=Json::array(),losses_=Json::array();
};
static uint64_t Number(const Json& v,uint64_t high) {
  Require(v.is_number_unsigned() || (v.is_number_integer() && v>=0),"RC unsigned integer required");
  const auto n=v.get<uint64_t>();Require(n<=high,"RC integer out of bounds");return n;
}
static void Validate(const Json& input) {
  Require(input.is_object() && input.size()==6 && input.at("schema_version")=="moe-rc-concurrent-input/1","invalid RC concurrent schema/fields");
  const auto mtu=Number(input.at("mtu"),4096);Require(mtu==256 || mtu==512 || mtu==1024 || mtu==2048 || mtu==4096,"invalid RC MTU");
  Require(Number(input.at("rto_ns"),1000000000)>0,"positive RC RTO required");
  const auto& memories=input.at("memories");Require(memories.is_array() && memories.size()>=2 && memories.size()<=8,"RC memory count");
  std::map<unsigned,std::size_t> sizes;uint64_t total=0;
  for(const auto& m:memories){Require(m.is_object() && m.size()==2 && m.at("data_hex").is_string(),"invalid RC memory fields");
    const auto gpu=Number(m.at("gpu"),65535);const auto& text=m.at("data_hex").get_ref<const std::string&>();
    Require(text.size()>=16 && text.size()<=131072,"RC memory byte limit");const auto data=rc::Unhex(text);total+=data.size();
    Require(sizes.emplace(gpu,data.size()).second,"duplicate RC memory GPU");}
  Require(total<=262144,"RC aggregate memory limit");
  const auto& connections=input.at("connections");Require(connections.is_array() && connections.size()>=2 && connections.size()<=8,"RC connection count");
  std::set<unsigned> ids;unsigned operations=0;uint64_t bytes=0;
  std::set<std::tuple<unsigned,unsigned,unsigned,unsigned>> packets;
  for(const auto& q:connections){Require(q.is_object() && q.size()==7,"invalid RC connection fields");
    const auto id=Number(q.at("id"),7);Require(ids.insert(id).second,"duplicate RC connection id");
    const auto src=Number(q.at("src"),65535),dst=Number(q.at("dst"),65535);
    Require(src!=dst && sizes.count(src) && sizes.count(dst),"RC endpoint memory missing");
    for(auto key:{"source_nic","destination_nic","plane"})Number(q.at(key),65535);
    const auto& ops=q.at("operations");Require(ops.is_array() && !ops.empty(),"RC operations required");operations+=ops.size();
    unsigned psn=0;
    for(const auto& op:ops){Require(op.is_object() && op.size()==3,"invalid RC operation fields");const auto kind=op.at("kind").get<std::string>();
      const auto address=Number(op.at("address"),sizes.at(dst)-1);
      if(kind=="write"){Require(op.at("data_hex").is_string() && op.at("data_hex").get_ref<const std::string&>().size()<=131072,"RC WRITE size");
        const auto data=rc::Unhex(op.at("data_hex"));bytes+=data.size();Require(!data.empty() && data.size()<=sizes.at(dst)-address,"RC WRITE range");
        const unsigned count=(data.size()+mtu-1)/mtu;
        for(unsigned i=0;i<count;++i)packets.emplace(id,dst,count==1?10:i==0?6:i==count-1?8:7,psn+i);
        packets.emplace(id,src,17,psn+count-1);psn+=count;}
      else {Require(kind=="fetch_add" && address%8==0 && address<=sizes.at(dst)-8,"RC atomic kind/range");Number(op.at("addend"),UINT64_MAX);
        packets.emplace(id,dst,20,psn);packets.emplace(id,src,18,psn);++psn;}}
  }
  Require(operations<=64 && bytes<=65536,"RC aggregate operation/WRITE budget");
  const auto& loss=input.at("loss");Require(loss.is_null() || (loss.is_object() && loss.size()==4),"invalid RC loss fields");
  if(!loss.is_null())Require(packets.count({Number(loss.at("connection"),7),Number(loss.at("node"),65535),
    Number(loss.at("opcode"),255),Number(loss.at("psn"),0xfffffe)}),"RC loss does not match actual connection packet");
}
static int Libraries(dl_phdr_info* info,std::size_t,void* data){if(info->dlpi_name && info->dlpi_name[0])static_cast<Json*>(data)->push_back(info->dlpi_name);return 0;}
}
int main(int argc,char** argv) {
  using namespace moe_rdma;
  try {
    Require(argc==5,"usage: moe-rdma-rc-concurrent TOPOLOGY.json RDMA.json WORK.json NEW_RESULT.json");
    std::ifstream exists(argv[4]);Require(!exists.good(),"RC output already exists");
    auto config=ReadJson(argv[2]),input=ReadJson(argv[3]);Validate(input);
    ns3::Time::SetResolution(ns3::Time::NS);ns3::RngSeedManager::SetSeed(config.at("seed"));ns3::CustomHeader::SetRoceReference(true);
    Require(config.at("payload_bytes")==input.at("mtu"),"RC/protocol MTU mismatch");
    Fabric fabric(ReadJson(argv[1]),config);Require(input.at("rto_ns").get<int64_t>()<=int64_t(fabric.MaxSimNs()/5),"RC RTO exceeds declared simulation budget");
    RcPacketProbe probe(fabric,input);
    ns3::Simulator::ScheduleNow(&RcPacketProbe::Start,&probe);ns3::Simulator::Stop(ns3::NanoSeconds(fabric.MaxSimNs()));ns3::Simulator::Run();
    auto result=fabric.Report();result["rc_concurrent"]=probe.Report();result["passed"]=probe.Passed() && fabric.Drained();
    result["loaded_libraries"]=Json::array();dl_iterate_phdr(Libraries,&result["loaded_libraries"]);
    std::ofstream out(argv[4]);out<<result.dump()<<'\n';out.close();Require(bool(out),"cannot save RC result");
    ns3::Simulator::Destroy();ns3::CustomHeader::SetRoceReference(false);Require(result.at("passed"),"incomplete RC concurrent run; inspect result");return 0;
  }catch(const std::exception& error){std::cerr<<error.what()<<'\n';ns3::Simulator::Destroy();ns3::CustomHeader::SetRoceReference(false);return 1;}
}
