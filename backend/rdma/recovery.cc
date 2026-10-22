#include "recovery.hh"
#include "ns3/core-module.h"
#include "ns3/ipv4-header.h"
#include "ns3/qbb-header.h"
#include <algorithm>
#include <stdexcept>

namespace moe_rdma {
using namespace ns3;
static int64_t Now(){return Simulator::Now().GetNanoSeconds();}
RdmaRecovery::RdmaRecovery(const Json& c,std::function<void(uint64_t)> failed):options_(c),
    initial_(c.at("initial_rto_ns")),maximum_(c.at("max_rto_ns")),limit_(c.at("max_retries")),
    records_(c.at("max_events"),c.contains("rtt_estimator")),failure_(std::move(failed)) {
  if(c.at("mode")!="go_back_n" || c.at("max_events").get<uint64_t>()>100000)
    throw std::invalid_argument("Invalid explicit recovery mode/event budget");
  if(c.contains("rtt_estimator")) {
    const auto& r=c.at("rtt_estimator");
    if(r.at("kind")!="karn")throw std::invalid_argument("Unknown RTT estimator kind");
    minimum_=r.at("min_rto_ns");granularity_=r.at("granularity_ns");
    if(minimum_<1 || granularity_<1)throw std::invalid_argument("Explicit RTT estimator requires positive bounds");
  }
  moe_cosim::SourceRecovery validate(1,initial_,maximum_,limit_,minimum_,granularity_);
}
RdmaRecovery::~RdmaRecovery(){
  for(auto& item:states_)Simulator::Cancel(item.second->event);
  for(auto hw:attached_)for(auto& nic:hw->m_nic)if(nic.dev) {
    nic.dev->m_rdmaReceiveCb=MakeCallback(&RdmaHw::Receive,PeekPointer(hw));
    nic.dev->m_rdmaPktSent=MakeCallback(&RdmaHw::PktSent,PeekPointer(hw));
  }
}
void RdmaRecovery::Attach(unsigned node,Ptr<RdmaHw> hw) {
  attached_.push_back(hw);
  for(auto& nic:hw->m_nic)if(nic.dev) {
    nic.dev->m_rdmaReceiveCb=MakeBoundCallback(&RdmaRecovery::Receive,this,node,PeekPointer(hw));
    nic.dev->m_rdmaPktSent=MakeBoundCallback(&RdmaRecovery::Sent,this,node,PeekPointer(hw));
  }
}
void RdmaRecovery::Event(unsigned event,uint64_t id,const State& s) {
  records_.Source(Now(),event,s.node,id,s.protocol.Timer());
}
void RdmaRecovery::Arm(uint64_t id,State& s) {
  Simulator::Cancel(s.event);
  if(s.protocol.Timer().Deadline()>=0 && !failed_)
    s.event=Simulator::Schedule(NanoSeconds(s.protocol.Timer().Deadline()-Now()),&RdmaRecovery::Fire,this,id);
}
void RdmaRecovery::Sent(RdmaRecovery* self,unsigned node,RdmaHw* hw,Ptr<RdmaQueuePair> qp,Ptr<Packet> p,Time gap) {
  hw->PktSent(qp,p,gap); // Preserve native pacing and congestion control.
  const auto id=qp->GetTag();auto it=self->states_.find(id);
  if(it==self->states_.end())it=self->states_.emplace(id,std::make_unique<State>(node,hw,qp,
      self->initial_,self->maximum_,self->limit_,self->minimum_,self->granularity_)).first;
  auto& s=*it->second;const auto old=s.protocol.Timer().Deadline();
  CustomHeader h(CustomHeader::L2_Header|CustomHeader::L3_Header|CustomHeader::L4_Header);h.getInt=1;p->PeekHeader(h);
  s.protocol.Sent(h.udp.seq,qp->snd_nxt,Now());
  self->records_.Rtt(Now(),0,id,h.udp.seq,qp->snd_nxt);
  if(old<0) {self->Event(0,id,s);self->Arm(id,s);}
}
int RdmaRecovery::Receive(RdmaRecovery* self,unsigned node,RdmaHw* hw,Ptr<Packet> p,CustomHeader& h) {
  if(h.l3Prot==0x11) {
    // Receiver-local duplicate detection before native acceptance. No global
    // flow/receiver truth is available to the sender's retransmission timer.
    auto rx=hw->GetRxQp(h.dip,h.sip,h.udp.dport,h.udp.sport,h.udp.pg,false);
    const auto payload=p->GetSize()-h.GetSerializedSize();
    const bool duplicate=rx && h.udp.seq<rx->ReceiverNextExpectedSeq &&
      uint64_t(h.udp.seq)+payload<=rx->ReceiverNextExpectedSeq;
    int ret=hw->Receive(p,h);
    if(duplicate)self->DuplicateAck(node,hw,h,rx);
    return ret;
  }
  if(h.l3Prot!=0xfc && h.l3Prot!=0xfd)return hw->Receive(p,h);
  auto qp=hw->GetQp(h.sip,h.ack.dport,h.ack.pg);
  if(!qp)return hw->Receive(p,h); // Late feedback after native QP cleanup.
  auto it=self->states_.find(qp->GetTag());
  if(it==self->states_.end())throw std::logic_error("Feedback for QP without an actual source transmission");
  auto& s=*it->second;
  const bool progress=s.protocol.Confirm(h.ack.seq,Now(),h.l3Prot==0xfd);
  self->records_.Rtt(Now(),h.l3Prot==0xfd?2:1,qp->GetTag(),h.ack.seq,0);
  if(progress)self->Event(s.protocol.Timer().Complete()?3:1,qp->GetTag(),s);
  else {++self->old_feedback_;self->Event(6,qp->GetTag(),s);}
  // A late cumulative ACK can cover beyond the cursor rewound by Go-Back-N.
  // Advance from real feedback, bounded above by source-emitted high water.
  qp->snd_nxt=std::max(qp->snd_nxt,uint64_t(h.ack.seq));
  int ret=hw->Receive(p,h);
  self->Arm(qp->GetTag(),s);
  if(s.protocol.Timer().Complete())s.qp=nullptr;
  return ret;
}
void RdmaRecovery::Fire(uint64_t id) {
  auto& s=*states_.at(id);
  const auto outcome=s.protocol.Expire(Now());
  records_.Rtt(Now(),3,id,0,0);
  if(outcome==moe_cosim::RetransmissionTimer::Expiry::Exhausted) {
    failed_=true;Event(4,id,s);
    for(auto& item:states_)Simulator::Cancel(item.second->event);
    failure_(id);return;
  }
  Event(2,id,s);
  // Reset the source cursor only. Native QP arbitration/pacing/PFC/DCQCN still
  // decide when actual retransmission packets may enter the physical link.
  s.hw->RecoverQueue(s.qp);
  Arm(id,s);
  s.hw->m_nic.at(s.hw->GetNicIdxOfQp(s.qp)).dev->TriggerTransmit();
}
void RdmaRecovery::DuplicateAck(unsigned node,Ptr<RdmaHw> hw,CustomHeader& h,Ptr<RdmaRxQueuePair> rx) {
  qbbHeader ack;ack.SetSeq(rx->ReceiverNextExpectedSeq);ack.SetPG(h.udp.pg);
  ack.SetSport(h.udp.dport);ack.SetDport(h.udp.sport);ack.SetIntHeader(h.udp.ih);
  if(h.GetIpv4EcnBits())ack.SetCnp();
  auto packet=Create<Packet>(std::max(60-14-20-int(ack.GetSerializedSize()),0));packet->AddHeader(ack);
  Ipv4Header ip;ip.SetDestination(Ipv4Address(h.sip));ip.SetSource(Ipv4Address(h.dip));ip.SetProtocol(0xfc);
  ip.SetTtl(64);ip.SetPayloadSize(packet->GetSize());ip.SetIdentification(rx->m_ipid++);
  packet->AddHeader(ip);hw->AddHeader(packet,0x800);
  ++duplicate_acks_;
  records_.Receiver({Now(),node,h.sip,h.dip,h.udp.sport,h.udp.dport,rx->ReceiverNextExpectedSeq,h.udp.seq});
  auto dev=hw->m_nic.at(hw->GetNicIdxOfRxQp(rx)).dev;
  dev->RdmaEnqueueHighPrioQ(packet);dev->TriggerTransmit();
}
bool RdmaRecovery::Idle() const {
  if(failed_)return false;
  for(const auto& item:states_)if(!item.second->protocol.Timer().Complete())return false;
  return true;
}
RdmaRecovery::Json RdmaRecovery::Report() const {
  Json states=Json::array();
  for(const auto& item:states_) {
    const auto& s=*item.second;const auto& t=s.protocol.Timer();
    states.push_back({{"id",item.first},{"source",s.node},{"acknowledged_bytes",t.Ack()},
      {"highest_sent_bytes",t.High()},{"retries_since_progress",t.Retries()},{"timeout_retransmissions",t.TotalRetries()},
      {"timeout_expiries",t.Expiries()},{"current_rto_ns",t.Rto()},{"deadline_ns",t.Deadline()},
      {"state",t.Failed()?"retry_exhausted":t.Complete()?"complete":"waiting_ack"}});
    if(s.protocol.Rtt()) {
      const auto& e=*s.protocol.Rtt();
      states.back()["rtt"]={{"samples",e.Samples()},{"discarded_probes",e.DiscardedProbes()},
        {"srtt_ns",e.Srtt()},{"rttvar_ns",e.Variance()},{"base_rto_ns",e.BaseRto()},
        {"ambiguity_until_bytes",e.Barrier()},{"probe_end_bytes",e.ProbeEnd()},{"probe_sent_ns",e.ProbeTime()}};
    }
  }
  auto result=records_.Report();
  result.update({{"config",options_},{"scope","declared source-ACK timer and receiver duplicate ACK extension; native pacing/queues/routes preserved"},
    {"failed",failed_},{"states",states},{"duplicate_ack_responses",duplicate_acks_},{"old_feedback_packets",old_feedback_}});
  return result;
}
}
