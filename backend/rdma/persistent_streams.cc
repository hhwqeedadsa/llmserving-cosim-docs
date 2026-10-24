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
  Require(limits.streams>0 && limits.streams<=4096 && limits.work>0 && limits.work<=65536 &&
          limits.link_transmissions>0 && limits.link_transmissions<=2000000,
          "persistent stream count limits out of supported range");
  Require(!fabric_.persistent_stream_mode_ && fabric_.flows_.empty() && !fabric_.resource_tracking_ && !fabric_.recovery_ &&
          !fabric_.config_.contains("drop_once") &&
          (fabric_.fault_rules_.empty() || !recovery.is_null()),"stream faults require explicit persistent recovery; legacy sends/admission/recovery excluded");
  if(!recovery.is_null()) {
    const auto& c=recovery;
    ValidateReordering(c,fabric_.MaxSimNs());
    selective_mode_=c.value("mode",std::string())=="selective_repeat/1";
    Require(c.is_object() && c.size()==5u+unsigned(c.contains("rtt_estimator"))+unsigned(c.contains("on_exhausted"))+unsigned(c.contains("reordering"))+2u*unsigned(selective_mode_) &&
            (c.at("mode")=="go_back_n/1" || selective_mode_),
            "invalid persistent recovery mode/fields");
    const auto policy=c.value("on_exhausted",std::string("stop_simulation"));
    Require(policy=="stop_simulation" || policy=="isolate_stream","invalid persistent on_exhausted policy");
    isolate_failures_=policy=="isolate_stream";
    auto integer=[&](const char* key,int64_t low,int64_t high) {
      const auto& v=c.at(key);Require(v.is_number_integer() && v>=low && v<=high,"invalid persistent recovery integer");
      return v.get<int64_t>();
    };
    const auto initial=integer("initial_rto_ns",1,fabric_.MaxSimNs());
    const auto maximum=integer("max_rto_ns",initial,1000000000);
    integer("max_retries",1,16);integer("max_events",0,100000);
    if(selective_mode_) {
      const auto mtu=fabric_.config_.at("payload_bytes").get<uint64_t>();
      selective_capacity_=integer("receive_buffer_bytes",mtu,2*1024*1024);
      selective_window_=integer("inflight_bytes",mtu,2*1024*1024);
      Require(selective_window_+mtu-1<=selective_capacity_,"selective capacity must cover packet window overshoot");
      Require(c.at("max_events")>0,"selective mode requires complete bounded event records");
      if(c.contains("reordering")) {
        grace_wait_=c.at("reordering").at("wait_ns");
        grace_limit_=c.at("reordering").at("max_events");
      }
    }
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
  fabric_.network_state_changed_=[this](){Notify();};
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
      if(recovery_records_ || fabric_.config_.contains("link_failures"))Require(device->TraceConnectWithoutContext("PhyRxDrop",MakeBoundCallback(&PersistentStreams::Drop,this,node,port)),"missing stream drop hook");
    }
}
PersistentStreams::~PersistentStreams() {
  fabric_.network_state_changed_={};
  for(auto& item:streams_) {
    Simulator::Cancel(item.second.recovery_event);
    Simulator::Cancel(item.second.grace_event);
  }
}
void PersistentStreams::ValidateReordering(const Json& c,int64_t max_ns) {
  if(!c.is_object() || !c.contains("reordering"))return;
  Require(c.value("mode",std::string())=="selective_repeat/1","reordering requires selective_repeat/1");
  const auto& r=c.at("reordering");
  Require(r.is_object() && r.size()==3 && r.at("mode")=="nack_grace/1",
          "invalid reordering mode/fields");
  Require(c.at("initial_rto_ns").is_number_integer() && c.at("initial_rto_ns")>=1 &&
          c.at("initial_rto_ns")<=max_ns,"invalid initial RTO for reordering");
  Require(r.at("wait_ns").is_number_integer() && r.at("wait_ns")>=1 &&
          r.at("wait_ns")<=c.at("initial_rto_ns") && r.at("wait_ns")<=1000000000,
          "reordering wait must be positive and no greater than initial RTO");
  Require(r.at("max_events").is_number_integer() && r.at("max_events")>=1 && r.at("max_events")<=100000,
          "invalid reordering event budget");
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
void PersistentStreams::ValidateDcqcnReference(const Json& c,int64_t max_ns) {
  Require(c.is_object() && c.size()==12 && c.at("mode")=="figure7_reference/1","invalid dcqcn_reference mode/fields");
  for(const auto* key:{"np_interval_ns","alpha_interval_ns","recovery_interval_ns","recovery_bytes",
      "min_rate_bps","additive_bps","hyper_bps","fast_recovery_rounds","cnp_model_bytes","max_events"})
    Require(c.at(key).is_number_integer() && c.at(key)>0,"dcqcn_reference fields must be positive integers");
  for(const auto* key:{"np_interval_ns","alpha_interval_ns","recovery_interval_ns"})
    Require(c.at(key)<=max_ns,"reference interval exceeds simulation horizon");
  Require(c.at("alpha_interval_ns")>c.at("np_interval_ns") &&
          c.at("recovery_bytes")>=512 && c.at("recovery_bytes")<=1073741824 &&
          c.at("fast_recovery_rounds")<=100 && c.at("cnp_model_bytes")>=30 && c.at("cnp_model_bytes")<=512 &&
          c.at("max_events")<=100000,"reference parameters out of supported range");
  for(const auto* key:{"min_rate_bps","additive_bps","hyper_bps"})
    Require(c.at(key)<=1000000000000000ull,"reference rate parameter exceeds supported range");
  const auto& gain=c.at("gain");
  Require(gain.is_number() && !gain.is_boolean() && gain>0 && gain<=1,"invalid reference gain");
}
void PersistentStreams::ConfigureDcqcnReference(const Json& c) {
  Require(streams_.empty() && !reference_limit_,"late or duplicate dcqcn_reference configuration");
  ValidateDcqcnReference(c,fabric_.MaxSimNs());
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
  if(deadline>=0 && s.failure_ns<0 && !closed_)
    s.recovery_event=Simulator::Schedule(NanoSeconds(deadline-StreamNow()),&PersistentStreams::RecoveryTimeout,this,s.qp->GetTag());
}
void PersistentStreams::RecoveryTimeout(uint64_t id) {
  auto& s=streams_.at(id);const auto expiry=s.recovery->Expire(StreamNow());
  CancelGrace(s,"rto",true);
  recovery_records_->Rtt(StreamNow(),3,id,0,0);
  if(expiry==moe_cosim::RetransmissionTimer::Expiry::Exhausted) {
    s.failure_ns=StreamNow();
    if(failed_stream_<0){failed_stream_=id;failure_ns_=StreamNow();}
    RecoveryRecord(s,4);
    if(isolate_failures_) {
      // Stop only this local producer. Keep the QP map and all true sequence
      // state; removal does not retract already transmitted remote writes.
      auto& nic=fabric_.drivers_[s.src]->m_rdma->m_nic[s.source_nic];
      auto& group=nic.qpGrp->m_qps;
      group.erase(std::remove(group.begin(),group.end(),s.qp),group.end());
      Simulator::Cancel(s.qp->mlx.m_eventUpdateAlpha);Simulator::Cancel(s.qp->mlx.m_eventDecreaseRate);
      Simulator::Cancel(s.qp->mlx.m_rpTimer);
      if(s.reference) {
        Simulator::Cancel(s.reference->alpha_event);Simulator::Cancel(s.reference->recovery_event);
        // Receiver NP may still send real CNP for already-in-flight bytes.
      }
      // Next() is no longer reachable for this producer. Native Packet's byte
      // constructor copied each already-built payload; do not cancel/drop those
      // packets or synthesize ACKs, queue credits, or application lease release.
      if(release_payload_on_isolation_)for(auto& w:s.work)if(!w.payload.empty()) {
        const auto bytes=w.payload.size();auto& used=payload_used_.at(s.src);
        Require(used>=bytes && w.acked<0 && w.payload_discarded<0,"isolated payload release mismatch");
        used-=bytes;Bytes().swap(w.payload);w.payload_discarded=StreamNow();
        payload_events_.push_back({{"kind","discarded_on_source_isolation"},{"time_ns",StreamNow()},
          {"source",s.src},{"stream",id},{"work",w.id},{"bytes",bytes},{"used_bytes",used}});
      }
      nic.dev->TriggerTransmit();Notify();return;
    }
    for(auto& item:streams_) {
      Simulator::Cancel(item.second.recovery_event);
      CancelGrace(item.second,"stop_simulation");
    }
    Simulator::Stop();return;
  }
  RecoveryRecord(s,2);
  auto hw=fabric_.drivers_[s.src]->m_rdma;hw->RecoverQueue(s.qp);
  if(s.selective_sender)s.qp->snd_nxt=s.selective_sender->Skip(s.qp->snd_nxt);
  ArmRecovery(s);Pump(s);Notify();
}
Json PersistentStreams::GraceState(const Stream& s) const {
  return {{"prefix",s.nack_grace->Prefix()},{"deadline_ns",s.nack_grace->Deadline()},
    {"serviced",s.nack_grace->Serviced()},{"trigger_feedback_uid",s.grace_trigger_uid}};
}
void PersistentStreams::GraceLog(Stream& s,const char* kind,const Json& before,int64_t uid,int64_t cumulative,bool nack) {
  Require(grace_events_.size()<grace_limit_,"NACK grace event budget exceeded");
  grace_events_.push_back({{"time_ns",StreamNow()},{"stream",s.qp->GetTag()},{"kind",kind},
    {"feedback_uid",uid},{"cumulative_ack",cumulative},{"nack",nack},
    {"before",before},{"after",GraceState(s)},{"snd_una",s.qp->snd_una},
    {"highest_sent_bytes",s.recovery?s.recovery->Timer().High():0},
    {"rto_deadline_ns",s.recovery?s.recovery->Timer().Deadline():-1}});
}
void PersistentStreams::GraceFeedback(Stream& s,Ptr<const Packet> packet,uint64_t cumulative,bool nack) {
  auto& g=*s.nack_grace;const auto before=GraceState(s);const auto old=g.Deadline();
  g.Feedback(StreamNow(),cumulative,nack);
  if(g.Deadline()!=old || g.Prefix()!=before.at("prefix").get<uint64_t>()) {
    Simulator::Cancel(s.grace_event);
    s.grace_trigger_uid=g.Deadline()<0?-1:static_cast<int64_t>(packet->GetUid());
    if(g.Deadline()>=0)
      s.grace_event=Simulator::Schedule(NanoSeconds(g.Deadline()-StreamNow()),&PersistentStreams::GraceTimeout,this,s.qp->GetTag());
  }
  GraceLog(s,"feedback",before,packet->GetUid(),cumulative,nack);
}
void PersistentStreams::CancelGrace(Stream& s,const char* reason,bool rto) {
  if(!s.nack_grace)return;
  const auto before=GraceState(s);
  Simulator::Cancel(s.grace_event);
  if(rto)s.nack_grace->Rto();else s.nack_grace->Cancel();
  s.grace_trigger_uid=-1;
  GraceLog(s,reason,before);
}
void PersistentStreams::GraceTimeout(uint64_t id) {
  auto& s=streams_.at(id);auto& g=*s.nack_grace;
  const auto before=GraceState(s);
  Require(!closed_ && s.failure_ns<0 && g.Deadline()==StreamNow(),"stale NACK grace timer");
  const auto rto=s.recovery->Timer().Deadline();
  if(rto>=0 && rto<=StreamNow()) {CancelGrace(s,"rto_due",true);return;}
  Require(g.Prefix()==s.qp->snd_una && s.qp->snd_una<s.recovery->Timer().High(),"NACK grace without actual source hole");
  Require(g.Expire(StreamNow()),"NACK grace expired early");
  s.grace_trigger_uid=-1;GraceLog(s,"retry",before);
  fabric_.drivers_[s.src]->m_rdma->RecoverQueue(s.qp);
  s.qp->snd_nxt=s.selective_sender->Skip(s.qp->snd_nxt);
  Pump(s);fabric_.drivers_[s.src]->m_rdma->m_nic[s.source_nic].dev->TriggerTransmit();Notify();
}
void PersistentStreams::TerminalFeedback(Stream& s,Ptr<const Packet> packet,unsigned protocol,uint64_t sequence) {
  ++terminal_feedback_count_;
  const auto maximum=recovery_config_.at("max_events").get<uint64_t>();
  if(terminal_feedback_.size()<maximum)
    terminal_feedback_.push_back({{"time_ns",StreamNow()},{"stream",s.qp->GetTag()},
      {"source",s.src},{"packet_uid",packet->GetUid()},{"ip_protocol",protocol},{"sequence",sequence}});
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
void PersistentStreams::SelectiveLog(Stream& s,const char* kind,Ptr<const Packet> packet,const moe_cosim::SelectiveAck& ack) {
  Require(selective_events_.size()<recovery_config_.at("max_events").get<uint64_t>(),"selective event budget exceeded");
  const auto h=StreamHeader(packet);
  Json row={{"time_ns",StreamNow()},{"kind",kind},{"stream",s.qp->GetTag()},
    {"packet_uid",packet->GetUid()},{"ip_protocol",h.l3Prot},{"model_bytes",packet->GetSize()},
    {"sack_begin",ack.begin},{"sack_end",ack.end},{"sack_valid",ack.received}};
  if(h.l3Prot==0xfc || h.l3Prot==0xfd) {
    row["cumulative_ack"]=h.ack.seq;
    std::array<uint8_t,16> bytes{};
    packet->CreateFragment(h.GetSerializedSize(),16)->CopyData(bytes.data(),bytes.size());
    static const char digits[]="0123456789abcdef";std::string hex;
    for(auto b:bytes){hex+=digits[b>>4];hex+=digits[b&15];}row["extension_hex"]=hex;
  } else {
    row["sequence"]=h.udp.seq;row["receiver_prefix"]=s.selective_receiver->Prefix();
    row["reorder_bytes"]=s.selective_receiver->Used();
  }
  selective_events_.push_back(std::move(row));
}
int PersistentStreams::SelectiveReceive(Stream& s,Ptr<Packet> packet,CustomHeader& h) {
  auto hw=fabric_.drivers_[s.dst]->m_rdma;
  auto rx=hw->GetRxQp(h.dip,h.sip,h.udp.dport,h.udp.sport,3,true);
  const auto before=s.selective_receiver->Prefix();
  const auto size=packet->GetSize()-h.GetSerializedSize();
  Require(size>0 && size<=fabric_.config_.at("payload_bytes").get<uint64_t>() &&
          uint64_t(h.udp.seq)+size<=64*1024*1024,"invalid selective data range");
  const auto ack=s.selective_receiver->Receive(h.udp.seq,h.udp.seq+size);
  if(ack.received && s.selective_receiver->Held().count(h.udp.seq))s.reorder_packets.emplace(h.udp.seq,packet);
  // Only the newly contiguous range is delivered. Read bytes from the actual
  // accepted Packet(s), BEFORE removing ranges drained by SelectiveReceiver.
  // Neither source Work payloads nor application metadata may supply RX bytes.
  std::vector<std::pair<Ptr<Packet>,Bytes>> delivered;
  if(s.receiver)for(uint64_t cursor=before;cursor<s.selective_receiver->Prefix();) {
    auto held=cursor==h.udp.seq?packet:s.reorder_packets.at(cursor);
    const auto header=StreamHeader(held);const auto length=held->GetSize()-header.GetSerializedSize();
    Require(header.udp.seq==cursor && length>0 && cursor+length<=s.selective_receiver->Prefix(),
            "selective payload prefix lacks an actual complete packet");
    Bytes bytes(length);held->CreateFragment(header.GetSerializedSize(),length)->CopyData(bytes.data(),length);
    delivered.emplace_back(held,std::move(bytes));cursor+=length;
  }
  for(auto it=s.reorder_packets.begin();it!=s.reorder_packets.end();) {
    if(!s.selective_receiver->Held().count(it->first))it=s.reorder_packets.erase(it);else ++it;
  }
  Require(s.reorder_packets.size()==s.selective_receiver->Held().size(),"selective retained packet/range mismatch");
  rx->ReceiverNextExpectedSeq=s.selective_receiver->Prefix();
  SelectiveLog(s,"data_received",packet,ack);
  if(!delivered.empty())Simulator::ScheduleNow([this,id=s.qp->GetTag(),delivered=std::move(delivered)]() mutable {
    auto& stream=streams_.at(id);
    for(auto& [actual,bytes]:delivered) {
      const auto header=StreamHeader(actual);
      SelectiveLog(stream,"payload_delivered",actual,{header.udp.seq,header.udp.seq+static_cast<uint32_t>(bytes.size()),true});
      static const char digits[]="0123456789abcdef";std::string hex;hex.reserve(bytes.size()*2);
      for(auto b:bytes){hex+=digits[b>>4];hex+=digits[b&15];}
      selective_events_.back()["hex"]=hex;
      stream.receiver(std::move(bytes));
    }
  });
  qbbHeader feedback;feedback.SetSeq(rx->ReceiverNextExpectedSeq);feedback.SetPG(h.udp.pg);
  feedback.SetSport(h.udp.dport);feedback.SetDport(h.udp.sport);feedback.SetIntHeader(h.udp.ih);
  const auto bytes=ack.Encode();auto response=Create<Packet>(bytes.data(),bytes.size());response->AddHeader(feedback);
  Ipv4Header ip;ip.SetDestination(Ipv4Address(h.sip));ip.SetSource(Ipv4Address(h.dip));
  ip.SetProtocol(h.udp.seq>before?0xfd:0xfc);ip.SetTtl(64);ip.SetPayloadSize(response->GetSize());ip.SetIdentification(rx->m_ipid++);
  response->AddHeader(ip);hw->AddHeader(response,0x800);
  SelectiveLog(s,"feedback_generated",response,ack);
  auto dev=hw->m_nic[s.destination_nic].dev;dev->RdmaEnqueueHighPrioQ(response);dev->TriggerTransmit();
  CheckAccepted(s);
  if(h.GetIpv4EcnBits()==3)ReferenceMark(s,packet);
  Notify();return 0;
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
  Require(!selective_mode_ || reference_limit_,"selective reference requires independent DCQCN reference");
  auto [it,inserted]=streams_.try_emplace(id,src,dst,plane,source_nic,destination_nic,wire_port,depth,limits_.work);
  Require(inserted,"stream insertion failed");wire_streams_.emplace(wire_port,id);
  // A zero-byte initial prefix creates NO data and reserves NO future bytes.
  // The native scheduler may remove its idle membership; the object/map/rate
  // state persists, and later append restores only scheduler membership.
  shw->AddQueuePair(src,dst,id,0,3,sip,dip,wire_port,wire_port,0,fabric_.config_.at("base_rtt_ns"),
                    MakeCallback(&Fabric::Noop),MakeCallback(&Fabric::Noop));
  it->second.qp=shw->GetQp(dip.Get(),wire_port,3);
  Require(bool(it->second.qp),"stream QP creation failed");
  if(selective_mode_) {
    auto& s=it->second;
    s.selective_sender=std::make_unique<moe_cosim::SelectiveSender>();
    if(grace_wait_)s.nack_grace=std::make_unique<moe_cosim::NackGrace>(grace_wait_);
    s.selective_receiver=std::make_unique<moe_cosim::SelectiveReceiver>(selective_capacity_);
    s.qp->SetWin(selective_window_);s.qp->SetVarWin(false);
  }
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
void PersistentStreams::ConfigurePayloadPool(uint64_t bytes,bool release_on_isolation) {
  Require(!selective_mode_ && streams_.empty() && !payload_capacity_ && bytes>=33 && bytes<=2*1024*1024,
          "invalid payload pool configuration");
  Require(!release_on_isolation || isolate_failures_,"payload release requires source isolation");
  payload_capacity_=bytes;release_payload_on_isolation_=release_on_isolation;
}
bool PersistentStreams::CanSubmitPayload(uint64_t stream,uint64_t bytes) const {
  if(!payload_capacity_)return true;
  const auto source=streams_.at(stream).src;
  const auto it=payload_used_.find(source);const auto used=it==payload_used_.end()?0:it->second;
  Require(bytes>0 && bytes<=payload_capacity_,"one payload exceeds source pool capacity");
  if(release_payload_on_isolation_ && streams_.at(stream).failure_ns>=0)return false;
  return bytes<=payload_capacity_-used;
}
void PersistentStreams::Enqueue(uint64_t stream,uint64_t work,uint64_t bytes,Bytes payload,Done accepted,Done acked) {
  Require(!closed_ && streams_.count(stream) && !work_ids_.count(work) && work_ids_.size()<limits_.work &&
          bytes>0 && bytes<=1024*1024,"invalid stream work or work budget");
  auto& s=streams_.at(stream);
  Require(!payload_capacity_ || (!payload.empty() && CanSubmitPayload(stream,bytes)),
          "payload submitted without source pool capacity");
  // Application work may become ready after a local source failure (including
  // replies triggered by real in-flight requests). Retain it within the same
  // finite queue budget; Pump keeps the failed producer frozen. Rejecting here
  // would throw out of a remote receive callback and stop unrelated streams.
  Require(s.submitted_bytes+bytes<=64*1024*1024,"stream byte/sequence budget exceeded");
  const auto ticket=s.queue.Reserve(work,work);
  Work w;w.id=work;w.bytes=bytes;w.ticket=ticket;w.ready=StreamNow();
  w.payload=std::move(payload);
  if(payload_capacity_) {
    auto& used=payload_used_[s.src];used+=bytes;payload_peak_[s.src]=std::max(payload_peak_[s.src],used);
    payload_events_.push_back({{"kind","acquired"},{"time_ns",StreamNow()},{"source",s.src},
      {"stream",stream},{"work",work},{"bytes",bytes},{"used_bytes",used}});
  }
  w.accepted_callback=std::move(accepted);w.acked_callback=std::move(acked);
  s.work.push_back(std::move(w));s.submitted_bytes+=bytes;work_ids_.insert(work);
  Pump(s);Notify();
}
void PersistentStreams::Pump(Stream& s) {
  if(s.failure_ns>=0)return;
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
  if(s.selective_sender)s.qp->snd_nxt=s.selective_sender->Skip(s.qp->snd_nxt);
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
    if(s->failure_ns>=0) {TerminalFeedback(*s,packet,h.l3Prot,0);Notify();return 0;}
    SampleRate(*s,3,packet,0,packet->GetSize(),1);
    s->reference->rp.Cnp(StreamNow());ApplyReferenceRate(*s);ScheduleRp(*s);
    ReferenceLog(*s,"cnp_received",packet);SampleRate(*s,4,packet,0,packet->GetSize(),1);
    hw->m_nic[s->source_nic].dev->TriggerTransmit();Notify();return 0;
  }
  if(h.l3Prot==0xFC || h.l3Prot==0xFD) {
    auto s=Match(node,h,true);Require(s,"unexpected ACK in exclusive stream execution");
    if(s->failure_ns>=0) {
      Require(h.ack.seq<=s->recovery->Timer().High(),"terminal ACK covers unsent bytes");
      TerminalFeedback(*s,packet,h.l3Prot,h.ack.seq);Notify();return 0;
    }
    moe_cosim::SelectiveAck selective_ack;
    if(s->selective_sender) {
      Require(packet->GetSize()==h.GetSerializedSize()+16,"wrong selective feedback extension size");
      std::array<uint8_t,16> bytes{};
      packet->CreateFragment(h.GetSerializedSize(),16)->CopyData(bytes.data(),bytes.size());
      selective_ack=moe_cosim::SelectiveAck::Decode(bytes);
      s->selective_sender->Validate(h.ack.seq,selective_ack,s->recovery->Timer().High());
      SelectiveLog(*s,"feedback_received",packet,selective_ack);
    }
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
    if(s->selective_sender) {
      s->selective_sender->Feedback(h.ack.seq,selective_ack,s->recovery->Timer().High());
      if(s->nack_grace)GraceFeedback(*s,packet,h.ack.seq,h.l3Prot==0xFD);
      else if(h.l3Prot==0xFD && s->selective_sender->FastRetry(h.ack.seq)) {
        hw->RecoverQueue(s->qp);SelectiveLog(*s,"fast_retransmit",packet,selective_ack);
      }
      s->qp->snd_nxt=s->selective_sender->Skip(s->qp->snd_nxt);
    } else if(h.l3Prot==0xFD)hw->RecoverQueue(s->qp);
    if(s->reference)Require(!cnp,"reference ACK must not carry legacy CNP feedback");
    else if(cnp)hw->cnp_received_mlx(s->qp);
    SampleRate(*s,2,packet,h.ack.seq,packet->GetSize(),cnp);
    while(s->acked_prefix<s->published_prefix && s->work[s->acked_prefix].end<=s->qp->snd_una) {
      auto& w=s->work[s->acked_prefix];
      w.acked=StreamNow();++s->acked_prefix;s->queue.CompleteThrough(w.ticket+1);
      if(payload_capacity_) {
        Require(w.payload.size()==w.bytes && payload_used_.at(s->src)>=w.bytes,"payload pool release mismatch");
        payload_used_.at(s->src)-=w.bytes;Bytes().swap(w.payload);
        payload_events_.push_back({{"kind","released"},{"time_ns",StreamNow()},{"source",s->src},
          {"stream",s->qp->GetTag()},{"work",w.id},{"bytes",w.bytes},{"used_bytes",payload_used_.at(s->src)}});
      }
      if(w.acked_callback)Simulator::ScheduleNow(w.acked_callback);
    }
    Pump(*s);hw->m_nic[s->source_nic].dev->TriggerTransmit();Notify();return 0;
  }
  uint64_t before=0;
  Stream* data_stream=nullptr;
  if(h.l3Prot==0x11) {
    data_stream=Match(node,h,false);Require(data_stream,"unexpected data in exclusive stream execution");
    if(data_stream->selective_receiver)return SelectiveReceive(*data_stream,packet,h);
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
  Require(s.failure_ns<0,"failed source stream remained in NIC scheduler");
  // Sequence lookup also handles native NACK rewind; a forward-only TX cursor
  // would silently select the wrong work after RecoverQueue.
  const auto end=s.work.begin()+s.published_prefix;
  auto it=std::upper_bound(s.work.begin(),end,qp->snd_nxt,
    [](uint64_t sequence,const Work& w){return sequence<w.end;});
  Require(it!=end && it->begin<=qp->snd_nxt,"stream packet covers unpublished bytes");
  Require(!payload_capacity_ || !it->payload.empty(),"released payload selected for retransmission");
  auto hw=fabric_.drivers_[node]->m_rdma;
  // Native header and packet generation, capped at this work boundary. No
  // operation ID is encoded and neighbouring writes are never coalesced.
  const auto mtu=hw->m_mtu;hw->m_mtu=std::min<uint64_t>(mtu,it->end-qp->snd_nxt);
  try {
    const auto begin=qp->snd_nxt;
    auto packet=hw->GetNxtPacket(qp);hw->m_mtu=mtu;
    if(s.selective_sender)qp->snd_nxt=s.selective_sender->Skip(qp->snd_nxt);
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
  ++self->inflight_;Require(++self->observations_<=self->limits_.link_transmissions,"stream packet observation budget exceeded");
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
  for(const auto& item:streams_)if(!item.second.queue.Drained() || item.second.qp->snd_una!=item.second.qp->m_size ||
      (item.second.reference && item.second.reference->np.Pending()) ||
      (item.second.selective_receiver && item.second.selective_receiver->Used()))return false;
  return WireIdle();
}
bool PersistentStreams::WireIdle() const {
  if(fabric_.NetworkEventsPending())return false;
  if(inflight_)return false;
  for(const auto& item:streams_)if(item.second.reference && item.second.reference->np.Pending())return false;
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
    CancelGrace(s,"close");
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
  if(grace_wait_) {
    Json states=Json::array();
    for(const auto& [id,s]:streams_) {
      auto row=GraceState(s);row["stream"]=id;states.push_back(row);
    }
    result["nack_grace"]={{"config",recovery_config_.at("reordering")},{"states",states},{"events",grace_events_},
      {"scope","classical fixed source NACK grace; actual feedback only; one retry per prefix; independent RTO remains active; not RACK/IRN or calibrated hardware"}};
  }
  if(selective_mode_) {
    Json states=Json::array();
    for(const auto& [id,s]:streams_) {
      Json held=Json::array(),received=Json::array();
      for(const auto& [begin,end]:s.selective_receiver->Held())held.push_back({begin,end});
      for(const auto& [begin,end]:s.selective_sender->Received())received.push_back({begin,end});
      states.push_back({{"stream",id},{"receiver_prefix",s.selective_receiver->Prefix()},
        {"reorder_bytes",s.selective_receiver->Used()},{"peak_reorder_bytes",s.selective_receiver->Peak()},
        {"rejected_packets",s.selective_receiver->Rejected()},{"held_ranges",held},{"source_sacked_ranges",received},
        {"native_window_bytes",s.qp->GetWin()}});
    }
    result["selective_repeat"]={{"receive_buffer_bytes",selective_capacity_},{"inflight_bytes",selective_window_},
      {"max_packet_overshoot_bytes",fabric_.config_.at("payload_bytes").get<uint64_t>()-1},
      {"max_reorder_packets_per_stream",moe_cosim::SelectiveReceiver::kMaxPackets},
      {"states",states},{"events",selective_events_},
      {"wire_format","MSR1 16-byte big-endian magic/begin/end/valid after native ACK header; actual model bytes, not production RoCE"},
      {"scope","bounded byte-only selective-repeat reference, not IRN reproduction, modern GIN or hardware calibration; source uses received feedback only"}};
  }
  if(payload_capacity_) {
    Json owners=Json::array(),held=Json::array();
    for(const auto& [source,used]:payload_used_)owners.push_back({{"source",source},{"used_bytes",used},{"peak_bytes",payload_peak_.at(source)}});
    for(const auto& [stream,s]:streams_)for(const auto& w:s.work)if(!w.payload.empty())
      held.push_back({{"source",s.src},{"stream",stream},{"work",w.id},{"bytes",w.payload.size()}});
    result["payload_pool"]={{"capacity_bytes_per_source",payload_capacity_},{"owners",owners},{"held",held},{"events",payload_events_},
      {"scope","encoded copied payload bytes; release only on full-work cumulative ACK; excludes metadata, packets and observation copies"}};
    if(release_payload_on_isolation_) {
      result["payload_pool"]["on_source_isolation"]="release_copied_payload/1";
      result["payload_pool"]["scope"]="encoded copied payload bytes; full-work ACK release or local copy discard after source isolation; no ACK/credit/lease/target revocation implied; excludes packets and metadata";
      result["payload_pool"]["discarded_work"]=Json::array();
      for(const auto& [stream,s]:streams_)for(const auto& w:s.work)if(w.payload_discarded>=0)
        result["payload_pool"]["discarded_work"].push_back({{"stream",stream},{"work",w.id},{"bytes",w.bytes},{"time_ns",w.payload_discarded}});
    }
  }
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
    if(isolate_failures_) {
      r["failures"]=Json::array();bool healthy_drained=true;
      for(const auto& [id,s]:streams_) {
        if(s.failure_ns>=0)r["failures"].push_back({{"stream",id},{"source",s.src},{"time_ns",s.failure_ns}});
        else if(!s.queue.Drained() || s.qp->snd_una!=s.qp->m_size)healthy_drained=false;
      }
      result["source_isolation"]={{"scope","source stream only; no peer failure notification or write revocation"},
        {"wire_idle",WireIdle()},{"healthy_registered_work_drained",healthy_drained},
        {"failed_resources_released",false},{"terminal_feedback_packets",terminal_feedback_count_},
        {"terminal_feedback_complete",terminal_feedback_count_==terminal_feedback_.size()},
        {"terminal_feedback",terminal_feedback_}};
    }
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
