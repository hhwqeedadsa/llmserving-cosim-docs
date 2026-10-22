#include "persistent_streams.hh"
#include "ns3/custom-header.h"
#include <algorithm>

namespace moe_rdma {
using namespace ns3;
static int64_t StreamNow() { return Simulator::Now().GetNanoSeconds(); }
static CustomHeader StreamHeader(Ptr<const Packet> packet) {
  CustomHeader h(CustomHeader::L2_Header|CustomHeader::L3_Header|CustomHeader::L4_Header);
  h.getInt=1;packet->PeekHeader(h);return h;
}
PersistentStreams::PersistentStreams(Fabric& fabric):fabric_(fabric) {
  Require(!fabric_.persistent_stream_mode_ && fabric_.flows_.empty() && !fabric_.resource_tracking_ && !fabric_.recovery_ &&
          fabric_.fault_rules_.empty(),"stream probe excludes legacy sends/admission/recovery/fault injection");
  Require(!fabric_.topology_.contains("intra_host") || !fabric_.topology_.at("intra_host").at("enabled").get<bool>(),
          "stream probe requires physical RDMA paths; intra-host bypass unsupported");
  fabric_.persistent_stream_mode_=true;
  for(unsigned node=0;node<fabric_.drivers_.size();++node) {
    auto hw=fabric_.drivers_[node]->m_rdma;
    Require(hw->m_cc_mode==1 && hw->m_ack_interval==1 && !hw->m_backto0,"stream adapter requires DCQCN/ACK-each-packet/no-backtozero");
    for(auto& nic:hw->m_nic) if(nic.dev) {
      nic.dev->m_rdmaReceiveCb=MakeCallback(&PersistentStreams::Receive,this,node);
      nic.dev->m_rdmaEQ->m_rdmaGetNxtPkt=MakeCallback(&PersistentStreams::Next,this,node);
    }
  }
  for(unsigned node=0;node<fabric_.ports_.size();++node)
    for(unsigned port=0;port<fabric_.ports_[node].size();++port) {
      auto device=fabric_.ports_[node][port].device;if(!device)continue;
      Require(device->TraceConnectWithoutContext("PhyTxBegin",MakeBoundCallback(&PersistentStreams::Tx,this,node,port)),"missing stream Tx hook");
      Require(device->TraceConnectWithoutContext("MacRx",MakeBoundCallback(&PersistentStreams::Rx,this,node,port)),"missing stream Rx hook");
    }
}
void PersistentStreams::Open(uint64_t id,unsigned src,unsigned dst,unsigned plane,uint16_t wire_port,
                            unsigned source_nic,unsigned destination_nic,unsigned depth) {
  Require(!closed_ && streams_.size()<16 && !streams_.count(id) && src<fabric_.gpus_ && dst<fabric_.gpus_ &&
          src!=dst && plane<fabric_.planes_ && wire_port>=10000 && !wire_ports_.count(wire_port),"invalid stream identity/binding");
  auto sip=fabric_.Address(src,plane),dip=fabric_.Address(dst,plane);
  auto shw=fabric_.drivers_[src]->m_rdma,dhw=fabric_.drivers_[dst]->m_rdma;
  // This scope needs explicit one-NIC reachability per address/plane. Do not
  // substitute an ECMP hash for application connection-to-NIC binding.
  Require(shw->m_rtTable[dip.Get()]==std::vector<int>{static_cast<int>(source_nic)} &&
          dhw->m_rtTable[sip.Get()]==std::vector<int>{static_cast<int>(destination_nic)},"stream requires unique declared source and reverse NIC routes");
  Require(source_nic<shw->m_nic.size() && destination_nic<dhw->m_nic.size(),"stream NIC missing");
  GinWorkQueue validate_depth(depth,64);(void)validate_depth;
  auto [it,inserted]=streams_.try_emplace(id,src,dst,plane,source_nic,destination_nic,wire_port,depth);
  Require(inserted,"stream insertion failed");wire_ports_.insert(wire_port);
  // A zero-byte initial prefix creates NO data and reserves NO future bytes.
  // The native scheduler may remove its idle membership; the object/map/rate
  // state persists, and later append restores only scheduler membership.
  shw->AddQueuePair(src,dst,id,0,3,sip,dip,wire_port,wire_port,0,fabric_.config_.at("base_rtt_ns"),
                    MakeCallback(&Fabric::Noop),MakeCallback(&Fabric::Noop));
  it->second.qp=shw->GetQp(dip.Get(),wire_port,3);
  Require(bool(it->second.qp),"stream QP creation failed");
}
void PersistentStreams::Submit(uint64_t stream,uint64_t work,uint64_t bytes,Done accepted,Done acked) {
  Require(!closed_ && streams_.count(stream) && !work_ids_.count(work) && work_ids_.size()<64 &&
          bytes>0 && bytes<=1024*1024,"invalid stream work or work budget");
  auto& s=streams_.at(stream);
  Require(s.submitted_bytes+bytes<=64*1024*1024,"stream byte/sequence budget exceeded");
  const auto ticket=s.queue.Reserve(work,work);
  Work w;w.id=work;w.bytes=bytes;w.ticket=ticket;w.ready=StreamNow();
  w.accepted_callback=std::move(accepted);w.acked_callback=std::move(acked);
  s.work.push_back(std::move(w));s.submitted_bytes+=bytes;work_ids_.insert(work);
  Pump(s);Notify();
}
void PersistentStreams::Pump(Stream& s) {
  Require(fabric_.drivers_[s.src]->m_rdma->GetQp(s.qp->dip.Get(),s.wire_port,3)==s.qp,"persistent QP identity changed");
  for(auto& w:s.work) if(w.published<0 && s.queue.CanPrepare(w.ticket)) {
    s.queue.Prepare(w.ticket);s.queue.PublishThrough(w.ticket+1);
    const auto consumed=s.queue.ConsumePublished();Require(consumed && consumed->work_id==w.id,"stream work consumption mismatch");
    w.begin=s.qp->m_size;w.end=w.begin+w.bytes;w.published=StreamNow();
    s.qp->m_size=w.end; // Only actually published work is offered to the NIC.
  }
  auto& nic=fabric_.drivers_[s.src]->m_rdma->m_nic[s.source_nic];
  if(s.qp->GetBytesLeft()) {
    if(std::find(nic.qpGrp->m_qps.begin(),nic.qpGrp->m_qps.end(),s.qp)==nic.qpGrp->m_qps.end()) {
      nic.qpGrp->AddQp(s.qp);++s.scheduler_insertions;
    }
    nic.dev->TriggerTransmit(); // Does not reset nextAvail, seq, rate or alpha.
  }
}
PersistentStreams::Stream* PersistentStreams::Match(unsigned node,const CustomHeader& h,bool source) {
  for(auto& entry:streams_) {
    auto& s=entry.second;
    if(source) {
      if(node==s.src && h.sip==s.qp->dip.Get() && h.dip==s.qp->sip.Get() &&
         h.ack.dport==s.wire_port && h.ack.sport==s.wire_port && h.ack.pg==3)return &s;
    } else if(node==s.dst && h.sip==s.qp->sip.Get() && h.dip==s.qp->dip.Get() &&
              h.udp.sport==s.wire_port && h.udp.dport==s.wire_port && h.udp.pg==3)return &s;
  }
  return nullptr;
}
int PersistentStreams::Receive(unsigned node,Ptr<Packet> packet,CustomHeader& h) {
  auto hw=fabric_.drivers_[node]->m_rdma;
  if(h.l3Prot==0xFC || h.l3Prot==0xFD) {
    auto s=Match(node,h,true);Require(s,"unexpected ACK in exclusive stream execution");
    Require(h.ack.seq<=s->qp->snd_nxt,"stream ACK covers unsent bytes");
    s->qp->Acknowledge(h.ack.seq);
    // Keep normal native NACK and DCQCN feedback handling, but do NOT invoke
    // native QpComplete: an ACKed published prefix is not connection close.
    if(h.l3Prot==0xFD)hw->RecoverQueue(s->qp);
    if((h.ack.flags>>qbbHeader::FLAG_CNP)&1)hw->cnp_received_mlx(s->qp);
    for(auto& w:s->work) if(w.published>=0 && w.acked<0 && w.end<=s->qp->snd_una) {
      w.acked=StreamNow();++s->acked_prefix;s->queue.CompleteThrough(w.ticket+1);
      if(w.acked_callback)Simulator::ScheduleNow(w.acked_callback);
    }
    Pump(*s);hw->m_nic[s->source_nic].dev->TriggerTransmit();Notify();return 0;
  }
  const auto result=hw->Receive(packet,h);
  if(h.l3Prot==0x11) {
    auto s=Match(node,h,false);Require(s,"unexpected data in exclusive stream execution");
    CheckAccepted(*s);
  }
  Notify();return result;
}
void PersistentStreams::CheckAccepted(Stream& s) {
  auto rx=fabric_.drivers_[s.dst]->m_rdma->GetRxQp(s.qp->dip.Get(),s.qp->sip.Get(),s.wire_port,s.wire_port,3,false);
  Require(bool(rx),"stream receiver state absent after acceptance");
  for(auto& w:s.work) if(w.published>=0 && w.received<0 && w.end<=rx->ReceiverNextExpectedSeq) {
    w.received=StreamNow();++s.accepted_prefix;
    if(w.accepted_callback)Simulator::ScheduleNow(w.accepted_callback);
  }
}
Ptr<Packet> PersistentStreams::Next(unsigned node,Ptr<RdmaQueuePair> qp) {
  auto found=streams_.find(qp->GetTag());Require(found!=streams_.end() && found->second.qp==qp,"foreign QP in stream packet generator");
  auto& s=found->second;
  auto it=std::find_if(s.work.begin(),s.work.end(),[&](const Work& w){return w.published>=0 && w.begin<=qp->snd_nxt && qp->snd_nxt<w.end;});
  Require(it!=s.work.end(),"stream packet covers unpublished bytes");
  auto hw=fabric_.drivers_[node]->m_rdma;
  // Native header and packet generation, capped at this work boundary. No
  // operation ID is encoded and neighbouring writes are never coalesced.
  const auto mtu=hw->m_mtu;hw->m_mtu=std::min<uint64_t>(mtu,it->end-qp->snd_nxt);
  try { auto packet=hw->GetNxtPacket(qp);hw->m_mtu=mtu;return packet; }
  catch(...) { hw->m_mtu=mtu;throw; }
}
void PersistentStreams::Tx(PersistentStreams* self,unsigned node,unsigned port,Ptr<const Packet> packet) {
  ++self->inflight_;Require(++self->observations_<=200000,"stream packet observation budget exceeded");
  auto h=StreamHeader(packet);
  if(h.l3Prot==0x11)for(auto& item:self->streams_) {
    auto& s=item.second;
    if(node==s.src && h.sip==s.qp->sip.Get() && h.dip==s.qp->dip.Get() && h.udp.sport==s.wire_port) {
      Require(port==s.source_nic,"stream transmitted on wrong bound NIC");
      ++s.packets;s.tx_payload+=packet->GetSize()-h.GetSerializedSize();
      self->source_packets_.push_back({StreamNow(),static_cast<int64_t>(item.first),node,s.dst,port,
        h.udp.seq,static_cast<int64_t>(packet->GetSize()-h.GetSerializedSize()),static_cast<int64_t>(packet->GetUid())});
    }
  }
}
void PersistentStreams::Rx(PersistentStreams* self,unsigned node,unsigned port,Ptr<const Packet> packet) {
  Require(self->inflight_>0,"stream receive without in-flight packet");--self->inflight_;self->Notify();
  auto h=StreamHeader(packet);
  if(h.l3Prot==0x11)if(auto s=self->Match(node,h,false))Require(port==s->destination_nic,"stream received on wrong bound NIC");
}
void PersistentStreams::Notify() {
  if(notification_pending_)return;
  notification_pending_=true;
  Simulator::ScheduleNow([this](){notification_pending_=false;if(changed)changed();});
}
bool PersistentStreams::Drained() const {
  if(inflight_)return false;
  for(const auto& item:streams_)if(!item.second.queue.Drained() || item.second.qp->snd_una!=item.second.qp->m_size)return false;
  for(const auto& ports:fabric_.ports_)for(const auto& p:ports)if(p.device && p.device->GetQueue()->GetNBytesTotal())return false;
  for(const auto& driver:fabric_.drivers_)for(const auto& nic:driver->m_rdma->m_nic)
    if(nic.dev && nic.dev->GetRdmaQueue()->m_ackQ->GetNPackets())return false;
  return true;
}
void PersistentStreams::Close() {
  Require(!closed_ && Drained(),"stream close before physical drain or repeated close");
  for(auto& item:streams_) {
    auto& s=item.second;s.queue.Close();
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
  result["source_packet_columns"]={"time_ns","stream","src","dst","nic","sequence","payload_bytes","packet_uid"};
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
