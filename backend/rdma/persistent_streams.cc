#include "persistent_streams.hh"
#include "ns3/custom-header.h"
#include "ns3/cn-header.h"
#include "ns3/ppp-header.h"
#include <algorithm>

namespace moe_rdma {
using namespace ns3;
static int64_t StreamNow() { return Simulator::Now().GetNanoSeconds(); }
static CustomHeader StreamHeader(Ptr<const Packet> packet) {
  CustomHeader h(CustomHeader::L2_Header|CustomHeader::L3_Header|CustomHeader::L4_Header);
  h.getInt=1;packet->PeekHeader(h);return h;
}
PersistentStreams::PersistentStreams(Fabric& fabric):PersistentStreams(fabric,Limits{}) {}
PersistentStreams::PersistentStreams(Fabric& fabric,Limits limits):PersistentStreams(fabric,limits,Json()) {}
PersistentStreams::PersistentStreams(Fabric& fabric,Limits limits,const Json& recovery):fabric_(fabric),limits_(limits) {
  Require(limits.streams>0 && limits.streams<=128 && limits.work>0 && limits.work<=8192,
          "persistent stream count limits out of supported range");
  Require(!fabric_.persistent_stream_mode_ && fabric_.flows_.empty() && !fabric_.resource_tracking_ && !fabric_.recovery_ &&
          !fabric_.config_.contains("drop_once") &&
          (fabric_.fault_rules_.empty() || !recovery.is_null()),"stream faults require explicit persistent recovery; legacy sends/admission/recovery excluded");
  if(!recovery.is_null()) {
    const auto& c=recovery;
    Require(c.is_object() && c.size()==(c.contains("rtt_estimator")?6u:5u) && c.at("mode")=="go_back_n/1",
            "invalid persistent recovery mode/fields");
    auto integer=[&](const char* key,int64_t low,int64_t high) {
      const auto& v=c.at(key);Require(v.is_number_integer() && v>=low && v<=high,"invalid persistent recovery integer");
      return v.get<int64_t>();
    };
    const auto initial=integer("initial_rto_ns",1,fabric_.MaxSimNs());
    const auto maximum=integer("max_rto_ns",initial,1000000000);
    integer("max_retries",1,16);integer("max_events",0,100000);
    if(c.contains("rtt_estimator")) {
      const auto& r=c.at("rtt_estimator");
      Require(r.is_object() && r.size()==3 && r.at("kind")=="karn" &&
        r.at("min_rto_ns").is_number_integer() && r.at("min_rto_ns")>=1 && r.at("min_rto_ns")<=initial &&
        r.at("granularity_ns").is_number_integer() && r.at("granularity_ns")>=1 && r.at("granularity_ns")<=maximum,
        "invalid persistent Karn estimator");
    }
    recovery_config_=c;recovery_records_=std::make_unique<RecoveryRecords>(c.at("max_events"),c.contains("rtt_estimator"));
  }
  Require(!fabric_.topology_.contains("intra_host") || !fabric_.topology_.at("intra_host").at("enabled").get<bool>(),
          "stream probe requires physical RDMA paths; intra-host bypass unsupported");
  fabric_.persistent_stream_mode_=true;
  for(unsigned node=0;node<fabric_.drivers_.size();++node) {
    auto hw=fabric_.drivers_[node]->m_rdma;
    Require(hw->m_cc_mode==1 && hw->m_ack_interval==1 && !hw->m_backto0,"stream adapter requires DCQCN/ACK-each-packet/no-backtozero");
    for(auto& nic:hw->m_nic) if(nic.dev) {
      nic.dev->m_rdmaReceiveCb=MakeCallback(&PersistentStreams::Receive,this,node);
      nic.dev->m_rdmaEQ->m_rdmaGetNxtPkt=MakeCallback(&PersistentStreams::Next,this,node);
      if(recovery_records_)nic.dev->m_rdmaPktSent=MakeCallback(&PersistentStreams::PacketSent,this,node);
    }
  }
  for(unsigned node=0;node<fabric_.ports_.size();++node)
    for(unsigned port=0;port<fabric_.ports_[node].size();++port) {
      auto device=fabric_.ports_[node][port].device;if(!device)continue;
      Require(device->TraceConnectWithoutContext("PhyTxBegin",MakeBoundCallback(&PersistentStreams::Tx,this,node,port)),"missing stream Tx hook");
      Require(device->TraceConnectWithoutContext("MacRx",MakeBoundCallback(&PersistentStreams::Rx,this,node,port)),"missing stream Rx hook");
      if(recovery_records_)Require(device->TraceConnectWithoutContext("PhyRxDrop",MakeBoundCallback(&PersistentStreams::Drop,this,node,port)),"missing stream drop hook");
    }
}
PersistentStreams::~PersistentStreams() {
  for(auto& item:streams_)Simulator::Cancel(item.second.recovery_event);
}
void PersistentStreams::ValidateFaultBindings() const {
  for(const auto& r:fabric_.fault_rules_)
    Require(streams_.count(r.at("message_id").get<uint64_t>()) && r.at("sequence")<=64*1024*1024,
            "fault names an unopened persistent stream or exceeds sequence budget");
}
void PersistentStreams::ConfigureRateCapture(const Json& config) {
  Require(streams_.empty() && !rate_limit_ && config.is_object() && config.size()==3,
          "source_rate_capture requires start_ns/end_ns/max_events before streams open");
  for(const auto* key:{"start_ns","end_ns","max_events"})
    Require(config.at(key).is_number_integer(),"source_rate_capture fields must be integers");
  Require(config.at("start_ns")>=0 && config.at("end_ns")>config.at("start_ns") &&
          config.at("end_ns")<=fabric_.MaxSimNs() && config.at("max_events")>=1 && config.at("max_events")<=100000,
          "invalid source_rate_capture window or event budget");
  rate_start_=config.at("start_ns");rate_end_=config.at("end_ns");rate_limit_=config.at("max_events");
}
void PersistentStreams::ConfigureDcqcnReference(const Json& c) {
  Require(streams_.empty() && !reference_limit_ && c.is_object() && c.size()==12 &&
          c.at("mode")=="figure7_reference/1","invalid dcqcn_reference mode/fields or late configuration");
  for(const auto* key:{"np_interval_ns","alpha_interval_ns","recovery_interval_ns","recovery_bytes",
      "min_rate_bps","additive_bps","hyper_bps","fast_recovery_rounds","cnp_model_bytes","max_events"})
    Require(c.at(key).is_number_integer() && c.at(key)>0,"dcqcn_reference fields must be positive integers");
  for(const auto* key:{"np_interval_ns","alpha_interval_ns","recovery_interval_ns"})
    Require(c.at(key)<=fabric_.MaxSimNs(),"reference interval exceeds simulation horizon");
  Require(c.at("alpha_interval_ns")>c.at("np_interval_ns") &&
          c.at("recovery_bytes")>=512 && c.at("recovery_bytes")<=1073741824 &&
          c.at("fast_recovery_rounds")<=100 && c.at("cnp_model_bytes")>=30 && c.at("cnp_model_bytes")<=512 &&
          c.at("max_events")<=100000,"reference parameters out of supported range");
  for(const auto* key:{"min_rate_bps","additive_bps","hyper_bps"})
    Require(c.at(key)<=1000000000000000ull,"reference rate parameter exceeds supported range");
  const auto& gain=c.at("gain");
  Require(gain.is_number() && !gain.is_boolean() && gain>0 && gain<=1,"invalid reference gain");
  reference_config_=c;reference_limit_=c.at("max_events");
  for(unsigned node=0;node<fabric_.drivers_.size();++node)
    for(auto& nic:fabric_.drivers_[node]->m_rdma->m_nic)if(nic.dev)
      nic.dev->m_rdmaPktSent=MakeCallback(&PersistentStreams::PacketSent,this,node);
}
void PersistentStreams::ReferenceLog(Stream& s,const char* kind,Ptr<const Packet> packet) {
  Require(reference_events_.size()<reference_limit_,"reference feedback event budget exceeded");
  const auto& r=*s.reference;
  reference_events_.push_back({{"time_ns",StreamNow()},{"stream",s.qp->GetTag()},{"kind",kind},
    {"packet_uid",packet?static_cast<int64_t>(packet->GetUid()):-1},{"model_bytes",packet?packet->GetSize():0},
    {"rate_bps",r.rp.Rate()},{"target_bps",r.rp.Target()},{"alpha",r.rp.AlphaValue()},
    {"timer_rounds",r.rp.TimerRounds()},{"byte_rounds",r.rp.ByteRounds()},{"remaining_bytes",r.rp.Remaining()},
    {"np_pending",r.np.Pending()},{"np_due_ns",r.np.Deadline()},
    {"alpha_due_ns",r.rp.AlphaDue()},{"recovery_due_ns",r.rp.RecoveryDue()},
    {"snd_una",s.qp->snd_una},{"published_bytes",s.qp->m_size}});
}
void PersistentStreams::ScheduleNp(Stream& s) {
  auto& r=*s.reference;Simulator::Cancel(r.np_event);
  if(r.np.Deadline()>=0)r.np_event=Simulator::Schedule(NanoSeconds(r.np.Deadline()-StreamNow()),
    &PersistentStreams::ReferenceNpTimer,this,s.qp->GetTag());
}
void PersistentStreams::SendCnp(Stream& s) {
  auto& r=*s.reference;
  CnHeader notice(s.wire_port,3,3,0,0);
  auto p=Create<Packet>(reference_config_.at("cnp_model_bytes").get<unsigned>()-30);
  p->AddHeader(notice);
  Ipv4Header ip;ip.SetSource(s.qp->dip);ip.SetDestination(s.qp->sip);ip.SetProtocol(0xff);
  ip.SetTtl(64);ip.SetIdentification(r.ipid++);ip.SetPayloadSize(p->GetSize());p->AddHeader(ip);
  PppHeader link;link.SetProtocol(0x0021);p->AddHeader(link);
  Require(p->GetSize()==reference_config_.at("cnp_model_bytes"),"CNP model frame size mismatch");
  ReferenceLog(s,"cnp_generated",p);
  auto device=fabric_.drivers_[s.dst]->m_rdma->m_nic[s.destination_nic].dev;
  device->RdmaEnqueueHighPrioQ(p);device->TriggerTransmit();
}
void PersistentStreams::ReferenceMark(Stream& s,Ptr<const Packet> packet) {
  ReferenceLog(s,"np_mark",packet);
  if(s.reference->np.Mark(StreamNow()))SendCnp(s);
  ScheduleNp(s);
}
void PersistentStreams::ReferenceNpTimer(uint64_t id) {
  auto& s=streams_.at(id);
  if(s.reference->np.Expire(StreamNow()))SendCnp(s);
  ScheduleNp(s);Notify();
}
void PersistentStreams::ApplyReferenceRate(Stream& s) {
  const auto& r=s.reference->rp;
  auto hw=fabric_.drivers_[s.src]->m_rdma;
  hw->ChangeRate(s.qp,DataRate(r.Rate()));
  s.qp->mlx.m_targetRate=DataRate(r.Target());s.qp->mlx.m_alpha=r.AlphaValue();
  s.qp->mlx.m_rpTimeStage=std::max(r.TimerRounds(),r.ByteRounds());
  s.qp->mlx.m_first_cnp=!r.Active();
}
void PersistentStreams::ScheduleRp(Stream& s) {
  auto& r=*s.reference;Simulator::Cancel(r.alpha_event);Simulator::Cancel(r.recovery_event);
  r.alpha_event=Simulator::Schedule(NanoSeconds(r.rp.AlphaDue()-StreamNow()),&PersistentStreams::ReferenceAlpha,this,s.qp->GetTag());
  r.recovery_event=Simulator::Schedule(NanoSeconds(r.rp.RecoveryDue()-StreamNow()),&PersistentStreams::ReferenceRecovery,this,s.qp->GetTag());
}
void PersistentStreams::ReferenceAlpha(uint64_t id) {
  auto& s=streams_.at(id);auto& r=*s.reference;
  r.rp.Alpha(StreamNow());ApplyReferenceRate(s);ReferenceLog(s,"rp_alpha");
  r.alpha_event=Simulator::Schedule(NanoSeconds(r.rp.AlphaDue()-StreamNow()),&PersistentStreams::ReferenceAlpha,this,id);
}
void PersistentStreams::ReferenceRecovery(uint64_t id) {
  auto& s=streams_.at(id);auto& r=*s.reference;
  r.rp.Timer(StreamNow());ApplyReferenceRate(s);ReferenceLog(s,"rp_timer");
  r.recovery_event=Simulator::Schedule(NanoSeconds(r.rp.RecoveryDue()-StreamNow()),&PersistentStreams::ReferenceRecovery,this,id);
}
void PersistentStreams::PacketSent(unsigned node,Ptr<RdmaQueuePair> qp,Ptr<Packet> packet,Time gap) {
  fabric_.drivers_[node]->m_rdma->PktSent(qp,packet,gap);
  auto& s=streams_.at(qp->GetTag());Require(s.src==node && s.qp==qp,"foreign persistent TX");
  if(s.reference) {
    const auto count=s.reference->rp.Sent(packet->GetSize());
    if(count)ApplyReferenceRate(s);
    ReferenceLog(s,"source_sent",packet);
    if(count)ReferenceLog(s,"rp_bytes");
  }
  if(s.recovery) {
    const auto h=StreamHeader(packet);const auto end=h.udp.seq+packet->GetSize()-h.GetSerializedSize();
    const auto previous=s.recovery->Timer().Deadline();
    s.recovery->Sent(h.udp.seq,end,StreamNow());
    recovery_records_->Rtt(StreamNow(),0,qp->GetTag(),h.udp.seq,end);
    if(previous<0){RecoveryRecord(s,0);ArmRecovery(s);}
  }
}
void PersistentStreams::RecoveryRecord(Stream& s,unsigned code) {
  recovery_records_->Source(StreamNow(),code,s.src,s.qp->GetTag(),s.recovery->Timer());
}
void PersistentStreams::ArmRecovery(Stream& s) {
  Simulator::Cancel(s.recovery_event);
  const auto deadline=s.recovery->Timer().Deadline();
  if(deadline>=0 && failed_stream_<0 && !closed_)
    s.recovery_event=Simulator::Schedule(NanoSeconds(deadline-StreamNow()),&PersistentStreams::RecoveryTimeout,this,s.qp->GetTag());
}
void PersistentStreams::RecoveryTimeout(uint64_t id) {
  auto& s=streams_.at(id);const auto expiry=s.recovery->Expire(StreamNow());
  recovery_records_->Rtt(StreamNow(),3,id,0,0);
  if(expiry==moe_cosim::RetransmissionTimer::Expiry::Exhausted) {
    failed_stream_=id;failure_ns_=StreamNow();RecoveryRecord(s,4);
    for(auto& item:streams_)Simulator::Cancel(item.second.recovery_event);
    Simulator::Stop();return;
  }
  RecoveryRecord(s,2);
  auto hw=fabric_.drivers_[s.src]->m_rdma;hw->RecoverQueue(s.qp);
  ArmRecovery(s);Pump(s);Notify();
}
void PersistentStreams::DuplicateAck(Stream& s,CustomHeader& h,Ptr<RdmaRxQueuePair> rx) {
  qbbHeader ack;ack.SetSeq(rx->ReceiverNextExpectedSeq);ack.SetPG(h.udp.pg);
  ack.SetSport(h.udp.dport);ack.SetDport(h.udp.sport);ack.SetIntHeader(h.udp.ih);
  if(!s.reference && h.GetIpv4EcnBits())ack.SetCnp();
  auto packet=Create<Packet>(std::max(60-14-20-int(ack.GetSerializedSize()),0));packet->AddHeader(ack);
  Ipv4Header ip;ip.SetDestination(Ipv4Address(h.sip));ip.SetSource(Ipv4Address(h.dip));ip.SetProtocol(0xfc);
  ip.SetTtl(64);ip.SetPayloadSize(packet->GetSize());ip.SetIdentification(rx->m_ipid++);
  packet->AddHeader(ip);fabric_.drivers_[s.dst]->m_rdma->AddHeader(packet,0x800);
  ++duplicate_acks_;
  recovery_records_->Receiver({StreamNow(),s.dst,h.sip,h.dip,h.udp.sport,h.udp.dport,rx->ReceiverNextExpectedSeq,h.udp.seq});
  auto dev=fabric_.drivers_[s.dst]->m_rdma->m_nic[s.destination_nic].dev;
  dev->RdmaEnqueueHighPrioQ(packet);dev->TriggerTransmit();
}
PersistentStreams::RateState PersistentStreams::ReadRate(const Stream& s) {
  const auto& q=*s.qp;
  return {q.m_rate.GetBitRate(),q.mlx.m_targetRate.GetBitRate(),q.m_max_rate.GetBitRate(),
    q.snd_nxt,q.snd_una,q.m_size,q.m_nextAvail.GetNanoSeconds(),q.mlx.m_alpha,q.mlx.m_rpTimeStage,
    q.mlx.m_first_cnp,q.mlx.m_alpha_cnp_arrived,q.mlx.m_decrease_cnp_arrived};
}
Json PersistentStreams::RateValues(const RateState& s) {
  return Json::array({s.rate,s.target,s.maximum,s.snd_nxt,s.snd_una,s.published,s.next_avail,
    s.alpha,s.stage,s.first_cnp,s.alpha_cnp,s.decrease_cnp});
}
void PersistentStreams::SampleRate(const Stream& s,unsigned event,Ptr<const Packet> packet,
                                   uint64_t sequence,unsigned bytes,unsigned cnp) {
  if(!rate_limit_ || StreamNow()<rate_start_ || StreamNow()>=rate_end_)return;
  Require(rate_samples_.size()<rate_limit_,"source rate observation budget exceeded");
  rate_samples_.push_back({StreamNow(),static_cast<int64_t>(packet->GetUid()),static_cast<int64_t>(sequence),
    s.qp->GetTag(),event,bytes,cnp,ReadRate(s)});
}
void PersistentStreams::Open(uint64_t id,unsigned src,unsigned dst,unsigned plane,uint16_t wire_port,
                            unsigned source_nic,unsigned destination_nic,unsigned depth) {
  Require(!closed_ && streams_.size()<limits_.streams && !streams_.count(id) && src<fabric_.gpus_ && dst<fabric_.gpus_ &&
          src!=dst && plane<fabric_.planes_ && wire_port>=10000 && !wire_streams_.count(wire_port),"invalid stream identity/binding");
  auto sip=fabric_.Address(src,plane),dip=fabric_.Address(dst,plane);
  auto shw=fabric_.drivers_[src]->m_rdma,dhw=fabric_.drivers_[dst]->m_rdma;
  // This scope needs explicit one-NIC reachability per address/plane. Do not
  // substitute an ECMP hash for application connection-to-NIC binding.
  Require(shw->m_rtTable[dip.Get()]==std::vector<int>{static_cast<int>(source_nic)} &&
          dhw->m_rtTable[sip.Get()]==std::vector<int>{static_cast<int>(destination_nic)},"stream requires unique declared source and reverse NIC routes");
  Require(source_nic<shw->m_nic.size() && destination_nic<dhw->m_nic.size(),"stream NIC missing");
  GinWorkQueue validate_depth(depth,limits_.work);(void)validate_depth;
  auto [it,inserted]=streams_.try_emplace(id,src,dst,plane,source_nic,destination_nic,wire_port,depth,limits_.work);
  Require(inserted,"stream insertion failed");wire_streams_.emplace(wire_port,id);
  // A zero-byte initial prefix creates NO data and reserves NO future bytes.
  // The native scheduler may remove its idle membership; the object/map/rate
  // state persists, and later append restores only scheduler membership.
  shw->AddQueuePair(src,dst,id,0,3,sip,dip,wire_port,wire_port,0,fabric_.config_.at("base_rtt_ns"),
                    MakeCallback(&Fabric::Noop),MakeCallback(&Fabric::Noop));
  it->second.qp=shw->GetQp(dip.Get(),wire_port,3);
  Require(bool(it->second.qp),"stream QP creation failed");
  if(recovery_records_)fabric_.stream_packet_ids_.emplace(std::make_tuple(sip.Get(),dip.Get(),wire_port),id);
  if(reference_limit_) {
    const auto& c=reference_config_;
    DcqcnRpConfig rc{c.at("alpha_interval_ns"),c.at("recovery_interval_ns"),c.at("recovery_bytes"),
      c.at("min_rate_bps"),c.at("additive_bps"),c.at("hyper_bps"),c.at("fast_recovery_rounds"),c.at("gain")};
    it->second.reference=std::make_unique<Stream::Reference>(c.at("np_interval_ns"),rc,it->second.qp->m_max_rate.GetBitRate());
  }
}
void PersistentStreams::Submit(uint64_t stream,uint64_t work,uint64_t bytes,Done accepted,Done acked) {
  Enqueue(stream,work,bytes,{},std::move(accepted),std::move(acked));
}
void PersistentStreams::SetReceiver(uint64_t stream,std::function<void(Bytes)> receiver) {
  Require(!closed_ && streams_.count(stream) && streams_.at(stream).work.empty() &&
          !streams_.at(stream).receiver && bool(receiver),"invalid stream payload receiver");
  streams_.at(stream).receiver=std::move(receiver);
}
void PersistentStreams::SubmitPayload(uint64_t stream,uint64_t work,Bytes payload,Done acked) {
  const auto bytes=payload.size();
  Require(streams_.count(stream) && bool(streams_.at(stream).receiver),"payload stream has no receiver");
  Enqueue(stream,work,bytes,std::move(payload),{},std::move(acked));
}
void PersistentStreams::Enqueue(uint64_t stream,uint64_t work,uint64_t bytes,Bytes payload,Done accepted,Done acked) {
  Require(!closed_ && streams_.count(stream) && !work_ids_.count(work) && work_ids_.size()<limits_.work &&
          bytes>0 && bytes<=1024*1024,"invalid stream work or work budget");
  auto& s=streams_.at(stream);
  Require(s.submitted_bytes+bytes<=64*1024*1024,"stream byte/sequence budget exceeded");
  const auto ticket=s.queue.Reserve(work,work);
  Work w;w.id=work;w.bytes=bytes;w.ticket=ticket;w.ready=StreamNow();
  w.payload=std::move(payload);
  w.accepted_callback=std::move(accepted);w.acked_callback=std::move(acked);
  s.work.push_back(std::move(w));s.submitted_bytes+=bytes;work_ids_.insert(work);
  Pump(s);Notify();
}
void PersistentStreams::Pump(Stream& s) {
  Require(fabric_.drivers_[s.src]->m_rdma->GetQp(s.qp->dip.Get(),s.wire_port,3)==s.qp,"persistent QP identity changed");
  while(s.published_prefix<s.work.size() && s.queue.CanPrepare(s.work[s.published_prefix].ticket)) {
    auto& w=s.work[s.published_prefix++];
    s.queue.Prepare(w.ticket);s.queue.PublishThrough(w.ticket+1);
    const auto consumed=s.queue.ConsumePublished();Require(consumed && consumed->work_id==w.id,"stream work consumption mismatch");
    w.begin=s.qp->m_size;w.end=w.begin+w.bytes;w.published=StreamNow();
    s.qp->m_size=w.end; // Only actually published work is offered to the NIC.
    if(recovery_records_) {
      if(s.recovery)s.recovery->Extend(w.end);
      else {
        const auto& c=recovery_config_;const bool adaptive=c.contains("rtt_estimator");
        s.recovery=std::make_unique<moe_cosim::SourceRecovery>(w.end,c.at("initial_rto_ns"),c.at("max_rto_ns"),c.at("max_retries"),
          adaptive?c.at("rtt_estimator").at("min_rto_ns").get<int64_t>():0,
          adaptive?c.at("rtt_estimator").at("granularity_ns").get<int64_t>():0);
      }
      RecoveryRecord(s,5);
    }
  }
  auto& nic=fabric_.drivers_[s.src]->m_rdma->m_nic[s.source_nic];
  if(s.qp->GetBytesLeft()) {
    if(std::find(nic.qpGrp->m_qps.begin(),nic.qpGrp->m_qps.end(),s.qp)==nic.qpGrp->m_qps.end()) {
      nic.qpGrp->AddQp(s.qp);++s.scheduler_insertions;
    }
    nic.dev->TriggerTransmit(); // Does not reset nextAvail, seq, rate or alpha.
  }
}
PersistentStreams::Stream* PersistentStreams::ByPort(uint16_t port) {
  auto it=wire_streams_.find(port);
  return it==wire_streams_.end()?nullptr:&streams_.at(it->second);
}
PersistentStreams::Stream* PersistentStreams::Match(unsigned node,const CustomHeader& h,bool source) {
  auto s=ByPort(source?h.ack.dport:h.udp.dport);
  if(!s)return nullptr;
  if(source) {
    if(node==s->src && h.sip==s->qp->dip.Get() && h.dip==s->qp->sip.Get() &&
       h.ack.sport==s->wire_port && h.ack.pg==3)return s;
  } else if(node==s->dst && h.sip==s->qp->sip.Get() && h.dip==s->qp->dip.Get() &&
            h.udp.sport==s->wire_port && h.udp.pg==3)return s;
  return nullptr;
}
int PersistentStreams::Receive(unsigned node,Ptr<Packet> packet,CustomHeader& h) {
  auto hw=fabric_.drivers_[node]->m_rdma;
  if(h.l3Prot==0xff && reference_limit_) {
    auto s=ByPort(h.cnp.fid);
    Require(s && node==s->src && h.sip==s->qp->dip.Get() && h.dip==s->qp->sip.Get() &&
            h.cnp.qIndex==3 && h.cnp.ecnBits==3,"unexpected independent CNP");
    SampleRate(*s,3,packet,0,packet->GetSize(),1);
    s->reference->rp.Cnp(StreamNow());ApplyReferenceRate(*s);ScheduleRp(*s);
    ReferenceLog(*s,"cnp_received",packet);SampleRate(*s,4,packet,0,packet->GetSize(),1);
    hw->m_nic[s->source_nic].dev->TriggerTransmit();Notify();return 0;
  }
  if(h.l3Prot==0xFC || h.l3Prot==0xFD) {
    auto s=Match(node,h,true);Require(s,"unexpected ACK in exclusive stream execution");
    if(s->recovery) {
      const bool progress=s->recovery->Confirm(h.ack.seq,StreamNow(),h.l3Prot==0xfd);
      recovery_records_->Rtt(StreamNow(),h.l3Prot==0xfd?2:1,s->qp->GetTag(),h.ack.seq,0);
      if(progress)RecoveryRecord(*s,s->recovery->Timer().Complete()?3:1);
      else {++old_feedback_;RecoveryRecord(*s,6);}
      s->qp->snd_nxt=std::max(s->qp->snd_nxt,uint64_t(h.ack.seq));
      ArmRecovery(*s);
    } else Require(h.ack.seq<=s->qp->snd_nxt,"stream ACK covers unsent bytes");
    const auto cnp=(h.ack.flags>>qbbHeader::FLAG_CNP)&1;
    SampleRate(*s,1,packet,h.ack.seq,packet->GetSize(),cnp);
    s->qp->Acknowledge(h.ack.seq);
    // Keep normal native NACK and DCQCN feedback handling, but do NOT invoke
    // native QpComplete: an ACKed published prefix is not connection close.
    if(h.l3Prot==0xFD)hw->RecoverQueue(s->qp);
    if(s->reference)Require(!cnp,"reference ACK must not carry legacy CNP feedback");
    else if(cnp)hw->cnp_received_mlx(s->qp);
    SampleRate(*s,2,packet,h.ack.seq,packet->GetSize(),cnp);
    while(s->acked_prefix<s->published_prefix && s->work[s->acked_prefix].end<=s->qp->snd_una) {
      auto& w=s->work[s->acked_prefix];
      w.acked=StreamNow();++s->acked_prefix;s->queue.CompleteThrough(w.ticket+1);
      if(w.acked_callback)Simulator::ScheduleNow(w.acked_callback);
    }
    Pump(*s);hw->m_nic[s->source_nic].dev->TriggerTransmit();Notify();return 0;
  }
  uint64_t before=0;
  Stream* data_stream=nullptr;
  if(h.l3Prot==0x11) {
    data_stream=Match(node,h,false);Require(data_stream,"unexpected data in exclusive stream execution");
    auto rx=hw->GetRxQp(h.dip,h.sip,h.udp.dport,h.udp.sport,3,false);
    if(rx)before=rx->ReceiverNextExpectedSeq;
  }
  auto accepted_header=h;
  if(data_stream && data_stream->reference)accepted_header.m_tos&=~3u;
  const auto result=hw->Receive(packet,accepted_header);
  if(h.l3Prot==0x11) {
    auto& s=*data_stream;
    auto rx=hw->GetRxQp(h.dip,h.sip,h.udp.dport,h.udp.sport,3,false);
    const auto size=packet->GetSize()-h.GetSerializedSize();
    if(s.recovery && h.udp.seq<before && uint64_t(h.udp.seq)+size<=before)DuplicateAck(s,h,rx);
    if(s.receiver && rx->ReceiverNextExpectedSeq>before) {
      const auto size=packet->GetSize()-h.GetSerializedSize();
      Require(h.udp.seq==before && rx->ReceiverNextExpectedSeq==before+size,"unsupported overlapping payload acceptance");
      Bytes payload(size);packet->CreateFragment(h.GetSerializedSize(),size)->CopyData(payload.data(),size);
      Simulator::ScheduleNow([callback=s.receiver,payload=std::move(payload)]() mutable {callback(std::move(payload));});
    }
    CheckAccepted(s);
    if(s.reference && h.GetIpv4EcnBits()==3)ReferenceMark(s,packet);
  }
  Notify();return result;
}
void PersistentStreams::CheckAccepted(Stream& s) {
  auto rx=fabric_.drivers_[s.dst]->m_rdma->GetRxQp(s.qp->dip.Get(),s.qp->sip.Get(),s.wire_port,s.wire_port,3,false);
  Require(bool(rx),"stream receiver state absent after acceptance");
  while(s.accepted_prefix<s.published_prefix && s.work[s.accepted_prefix].end<=rx->ReceiverNextExpectedSeq) {
    auto& w=s.work[s.accepted_prefix];
    w.received=StreamNow();++s.accepted_prefix;
    if(w.accepted_callback)Simulator::ScheduleNow(w.accepted_callback);
  }
}
Ptr<Packet> PersistentStreams::Next(unsigned node,Ptr<RdmaQueuePair> qp) {
  auto found=streams_.find(qp->GetTag());Require(found!=streams_.end() && found->second.qp==qp,"foreign QP in stream packet generator");
  auto& s=found->second;
  // Sequence lookup also handles native NACK rewind; a forward-only TX cursor
  // would silently select the wrong work after RecoverQueue.
  const auto end=s.work.begin()+s.published_prefix;
  auto it=std::upper_bound(s.work.begin(),end,qp->snd_nxt,
    [](uint64_t sequence,const Work& w){return sequence<w.end;});
  Require(it!=end && it->begin<=qp->snd_nxt,"stream packet covers unpublished bytes");
  auto hw=fabric_.drivers_[node]->m_rdma;
  // Native header and packet generation, capped at this work boundary. No
  // operation ID is encoded and neighbouring writes are never coalesced.
  const auto mtu=hw->m_mtu;hw->m_mtu=std::min<uint64_t>(mtu,it->end-qp->snd_nxt);
  try {
    const auto begin=qp->snd_nxt;
    auto packet=hw->GetNxtPacket(qp);hw->m_mtu=mtu;
    if(!it->payload.empty()) {
      const auto h=StreamHeader(packet);const auto size=packet->GetSize()-h.GetSerializedSize();
      auto framed=packet->CreateFragment(0,h.GetSerializedSize());
      framed->AddAtEnd(Create<Packet>(it->payload.data()+begin-it->begin,size));
      return framed;
    }
    return packet;
  }
  catch(...) { hw->m_mtu=mtu;throw; }
}
void PersistentStreams::Tx(PersistentStreams* self,unsigned node,unsigned port,Ptr<const Packet> packet) {
  ++self->inflight_;Require(++self->observations_<=200000,"stream packet observation budget exceeded");
  auto h=StreamHeader(packet);
  if(h.l3Prot==0xff && self->reference_limit_ && node<self->fabric_.gpus_) {
    auto s=self->ByPort(h.cnp.fid);
    Require(s && node==s->dst && port==s->destination_nic,"CNP transmitted on wrong bound NIC");
    self->ReferenceLog(*s,"cnp_tx",packet);
  }
  if(h.l3Prot==0x11)if(auto stream=self->ByPort(h.udp.sport)) {
    auto& s=*stream;
    if(node==s.src && h.sip==s.qp->sip.Get() && h.dip==s.qp->dip.Get() && h.udp.sport==s.wire_port) {
      Require(port==s.source_nic,"stream transmitted on wrong bound NIC");
      self->SampleRate(s,0,packet,h.udp.seq,packet->GetSize(),0);
      ++s.packets;s.tx_payload+=packet->GetSize()-h.GetSerializedSize();
      self->source_packets_.push_back({StreamNow(),static_cast<int64_t>(s.qp->GetTag()),node,s.dst,port,
        h.udp.seq,static_cast<int64_t>(packet->GetSize()-h.GetSerializedSize()),static_cast<int64_t>(packet->GetUid())});
      if(s.receiver) {
        const auto size=packet->GetSize()-h.GetSerializedSize();self->captured_payload_bytes_+=size;
        Require(self->captured_payload_bytes_<=2*1024*1024,"payload capture exceeds 2MiB");
        Bytes bytes(size);packet->CreateFragment(h.GetSerializedSize(),size)->CopyData(bytes.data(),size);
        static const char digits[]="0123456789abcdef";std::string hex;hex.reserve(size*2);
        for(auto byte:bytes){hex+=digits[byte>>4];hex+=digits[byte&15];}
        self->payload_packets_.push_back({{"time_ns",StreamNow()},{"stream",s.qp->GetTag()},{"sequence",h.udp.seq},{"hex",hex}});
      }
    }
  }
}
void PersistentStreams::Rx(PersistentStreams* self,unsigned node,unsigned port,Ptr<const Packet> packet) {
  Require(self->inflight_>0,"stream receive without in-flight packet");--self->inflight_;self->Notify();
  auto h=StreamHeader(packet);
  if(h.l3Prot==0x11)if(auto s=self->Match(node,h,false))Require(port==s->destination_nic,"stream received on wrong bound NIC");
}
void PersistentStreams::Drop(PersistentStreams* self,unsigned,unsigned,Ptr<const Packet>) {
  Require(self->inflight_>0,"stream drop without in-flight packet");--self->inflight_;self->Notify();
}
void PersistentStreams::Notify() {
  if(notification_pending_)return;
  notification_pending_=true;
  Simulator::ScheduleNow([this](){notification_pending_=false;if(changed)changed();});
}
bool PersistentStreams::Drained() const {
  if(failed_stream_>=0)return false;
  if(inflight_)return false;
  for(const auto& item:streams_)if(!item.second.queue.Drained() || item.second.qp->snd_una!=item.second.qp->m_size ||
      (item.second.reference && item.second.reference->np.Pending()))return false;
  for(const auto& ports:fabric_.ports_)for(const auto& p:ports)if(p.device && p.device->GetQueue()->GetNBytesTotal())return false;
  for(const auto& driver:fabric_.drivers_)for(const auto& nic:driver->m_rdma->m_nic)
    if(nic.dev && nic.dev->GetRdmaQueue()->m_ackQ->GetNPackets())return false;
  return true;
}
void PersistentStreams::Close() {
  Require(!closed_ && Drained(),"stream close before physical drain or repeated close");
  for(auto& item:streams_) {
    auto& s=item.second;s.queue.Close();
    Simulator::Cancel(s.recovery_event);
    if(s.reference) {
      Simulator::Cancel(s.reference->np_event);Simulator::Cancel(s.reference->alpha_event);Simulator::Cancel(s.reference->recovery_event);
      ReferenceLog(s,"qp_close");
    }
    Simulator::Cancel(s.qp->mlx.m_eventUpdateAlpha);Simulator::Cancel(s.qp->mlx.m_eventDecreaseRate);Simulator::Cancel(s.qp->mlx.m_rpTimer);
    auto hw=fabric_.drivers_[s.src]->m_rdma;hw->DeleteQueuePair(s.qp);
    auto& group=hw->m_nic[s.source_nic].qpGrp->m_qps;
    group.erase(std::remove(group.begin(),group.end(),s.qp),group.end());
  }
  closed_=true;
}
Json PersistentStreams::Report() const {
  Json result={{"scope","persistent native RC byte writes; ACK-based queue credit; not GIN, GPU visibility or calibrated timing"},
    {"streams",Json::array()},{"work",Json::array()},{"inflight_packets",inflight_},{"physical_drained",Drained()},
    {"closed",closed_},{"atomic_operations_supported",false},{"gpu_visibility_modeled",false},{"hardware_calibrated",false}};
  result["source_packets"]=source_packets_;
  if(recovery_records_) {
    auto r=recovery_records_->Report();r["event_codes"]["prefix_published"]=5;
    r["config"]=recovery_config_;r["failed"]=failed_stream_>=0;
    r["duplicate_ack_responses"]=duplicate_acks_;r["old_feedback_packets"]=old_feedback_;
    r["states"]=Json::array();
    for(const auto& item:streams_)if(item.second.recovery) {
      const auto& p=*item.second.recovery;const auto& t=p.Timer();
      Json row={{"stream",item.first},{"published_bytes",item.second.qp->m_size},{"acknowledged_bytes",t.Ack()},
        {"highest_sent_bytes",t.High()},{"deadline_ns",t.Deadline()},{"rto_ns",t.Rto()},{"timeout_retransmissions",t.TotalRetries()},
        {"timeout_expiries",t.Expiries()},{"retries_since_progress",t.Retries()},{"prefix_complete",t.Complete()},{"failed",t.Failed()}};
      if(p.Rtt())row["rtt"]={{"samples",p.Rtt()->Samples()},{"srtt_ns",p.Rtt()->Srtt()},
        {"rttvar_ns",p.Rtt()->Variance()},{"base_rto_ns",p.Rtt()->BaseRto()},{"discarded_probes",p.Rtt()->DiscardedProbes()}};
      r["states"].push_back(row);
    }
    r["identity_scope"]="message_id in records and fault rules means persistent stream ID; sequences cover the whole connection";
    r["complete_scope"]="complete denotes ACKed published prefix; connection persists until explicit Close";
    if(failed_stream_>=0)r["failure"]={{"reason","retry_exhausted"},{"stream",failed_stream_},{"time_ns",failure_ns_}};
    result["recovery"]=r;
  }
  if(reference_limit_) {
    result["dcqcn_reference"]={{"config",reference_config_},{"events",reference_events_},
      {"cnp_route","native ECMP over CNP IP source/destination and fid repeated as two 16-bit ports; local metadata projection only"},
      {"wire_format","native 8-byte CnHeader + IPv4 + PPP + declared padding; not a production RoCE frame"},
      {"np_clock","period starts at generation/enqueue, not physical TX; pending notifications drain before QP close"},
      {"rp_clock","CNP arrival updates rate; timers and actual source model-frame bytes recover it; same-ns RP events follow ns-3 FIFO event order"},
      {"scope","functional Figure6/7 reference with declared fixed hyper step and integer rate rounding; not hardware calibration or exact QCN implementation"}};
  }
  if(!payload_packets_.empty())result["source_payload_packets"]=payload_packets_;
  result["source_packet_columns"]={"time_ns","stream","src","dst","nic","sequence","payload_bytes","packet_uid"};
  if(rate_limit_) {
    Json samples=Json::array(),final=Json::array();
    for(const auto& s:rate_samples_) {
      Json row=Json::array({s.time,s.stream,s.event,s.uid,s.sequence,s.bytes,s.cnp});
      for(const auto& value:RateValues(s.state))row.push_back(value);
      samples.push_back(std::move(row));
    }
    for(const auto& item:streams_)final.push_back({{"stream",item.first},{"state",RateValues(ReadRate(item.second))}});
    result["source_rate_capture"]={{"start_ns",rate_start_},{"end_ns",rate_end_},{"max_events",rate_limit_},
      {"samples",samples},{"final_time_ns",StreamNow()},{"final_states",final},
      {"columns",{"time_ns","stream","event","packet_uid","sequence","model_bytes","ack_cnp",
        "rate_bps","target_rate_bps","max_rate_bps","snd_nxt","snd_una","published_bytes","next_avail_ns",
        "alpha","recovery_stage","first_cnp","alpha_cnp_arrived","decrease_cnp_arrived"}},
      {"state_columns",{"rate_bps","target_rate_bps","max_rate_bps","snd_nxt","snd_una","published_bytes","next_avail_ns",
        "alpha","recovery_stage","first_cnp","alpha_cnp_arrived","decrease_cnp_arrived"}},
      {"event_codes",{{"source_tx_before_PktSent",0},{"ack_before_transport_processing",1},{"ack_after_feedback_before_Pump",2}}},
      {"scope","passive source snapshots only, not continuous timer transitions; TX next_avail is before this packet's PktSent; ACK event includes native NACK; final states are outside capture window and may include idle rate decay; rates are modeled QP state, not measured throughput"}};
    if(reference_limit_) {
      auto& capture=result["source_rate_capture"];
      capture["event_codes"]["cnp_before_reference_processing"]=3;
      capture["event_codes"]["cnp_after_reference_processing"]=4;
      capture["scope"]="passive snapshots of explicitly configured reference controller; TX before PktSent, ACK changes transport only, independent CNP changes RP; not native MLX timer trace or hardware throughput";
    }
  }
  for(const auto& item:streams_) {
    const auto& s=item.second;
    result["streams"].push_back({{"id",item.first},{"src",s.src},{"dst",s.dst},{"plane",s.plane},{"wire_port",s.wire_port},
      {"source_nic",s.source_nic},{"destination_nic",s.destination_nic},{"qp_creations",1},
      {"scheduler_reinsertions",s.scheduler_insertions},{"submitted_bytes",s.submitted_bytes},{"published_bytes",s.qp->m_size},
      {"snd_nxt",s.qp->snd_nxt},{"snd_una",s.qp->snd_una},{"source_data_packets",s.packets},{"source_payload_bytes",s.tx_payload},
      {"ack_completed_work",s.acked_prefix},{"accepted_work",s.accepted_prefix},{"queue_depth",s.queue.Depth()}});
    for(const auto& w:s.work)result["work"].push_back({{"id",w.id},{"stream",item.first},{"bytes",w.bytes},{"ticket",w.ticket},
      {"begin",w.begin},{"end",w.end},{"ready_ns",w.ready},{"published_ns",w.published},{"accepted_ns",w.received},{"acked_ns",w.acked}});
  }
  return result;
}
}
