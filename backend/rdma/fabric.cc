#include "fabric.hh"
#include "ns3/custom-header.h"
#include "ns3/error-model.h"
#include "ns3/rdma-hw.h"
#include "ns3/switch-node.h"
#include "cnp_switch.hh"
#include <algorithm>
#include <fstream>
#include <queue>
#include <stdexcept>

namespace moe_rdma {
using namespace ns3;
void Require(bool ok, const std::string& why) { if(!ok) throw std::runtime_error("RDMA: " + why); }
Json ReadJson(const std::string& path) {
  std::ifstream input(path); Require(bool(input), "cannot read " + path); return Json::parse(input);
}
static int64_t Now() { return Simulator::Now().GetNanoSeconds(); }
static CustomHeader Header(Ptr<const Packet> p) {
  CustomHeader h(CustomHeader::L2_Header | CustomHeader::L3_Header | CustomHeader::L4_Header);
  h.getInt = 1; p->PeekHeader(h); return h;
}
class FaultModel final: public ErrorModel {
 public:
  static TypeId GetTypeId() {
    static TypeId tid=TypeId("moe_rdma::FaultModel").SetParent<ErrorModel>().AddConstructor<FaultModel>();return tid;
  }
  std::function<bool(Ptr<Packet>)> corrupt;
 private:
  bool DoCorrupt(Ptr<Packet> p) override {
    return corrupt(p);
  }
  void DoReset() override {} // Rules belong to the Fabric's single execution.
};
Ipv4Address Fabric::Address(unsigned gpu, unsigned plane) const {
  return Ipv4Address(0x0b000001u + gpu * 32u + plane);
}
void Fabric::Noop() {}

Fabric::Fabric(const Json& topology, const Json& config): topology_(topology), config_(config) {
  Require(Time::GetResolution() == Time::NS, "explicit nanosecond time resolution required");
  Require(topology.at("schema_version") == "moe-scaleout-physical-topology/1" ||
          topology.at("schema_version") == "moe-scaleout-physical-topology/2", "wrong topology schema");
  Require(config.at("schema_version") == "moe-scaleout-rdma/1" || config.at("schema_version") == "moe-scaleout-rdma/2", "wrong RDMA schema");
  Require(config.at("cc_mode") == 1, "first native adapter accepts DCQCN=1 only");
  Require(!(config.contains("drop_once") && config.contains("faults")), "choose drop_once or faults");
  if(config.contains("recovery")) {
    const auto& c=config.at("recovery");const std::string mode=c.at("mode");
    Require(mode=="native" || mode=="go_back_n","unknown recovery mode");
    if(mode=="go_back_n")recovery_=std::make_unique<RdmaRecovery>(c,[this](uint64_t id){
      failure_id_=id;failure_ns_=Now();Simulator::Stop();
    });
    else Require(c.size()==1,"native recovery mode has no override parameters");
  }
  resource_tracking_=config.contains("admission") || config.contains("lifecycle_capture") || bool(recovery_);
  queue_metrics_=config.value("queue_metrics",false);
  if(config.contains("admission")) {
    const auto& a=config.at("admission");admission_policy_=a.at("policy");
    admission_limit_=a.at("max_active_per_source");decision_ns_=a.at("decision_ns");
    byte_tracking_=a.contains("max_active_bytes_per_source");
    admission_byte_limit_=a.value("max_active_bytes_per_source",uint64_t(0));
    if(a.contains("per_destination")) {
      admission_peer_limit_=a.at("per_destination").at("max_active");
      admission_peer_bytes_=a.at("per_destination").at("max_bytes");
      Require(admission_peer_limit_>0 && admission_peer_bytes_>0,"explicit per-destination shares must be positive");
    }
    Require(admission_limit_<=1024 && decision_ns_>=0 && decision_ns_<=1000000 &&
      (admission_policy_!="passthrough" || decision_ns_==0),"invalid software admission budget");
  }
  if(config.contains("lifecycle_capture")) {
    lifecycle_limit_=config.at("lifecycle_capture").at("max_events");
    Require(lifecycle_limit_>0 && lifecycle_limit_<=100000,"invalid bounded lifecycle capture");
  }
  if(config.contains("packet_capture")) {
    const auto& c=config.at("packet_capture");
    if(c.contains("wire_headers")) {
      Require(c.at("wire_headers").is_boolean(),"wire_headers must be boolean");
      capture_wire_headers_=c.at("wire_headers").get<bool>();
    }
    capture_limit_=c.at("max_events");capture_start_=c.at("start_ns");capture_end_=c.at("end_ns");
    Require(capture_limit_>0 && capture_limit_<=100000 && capture_start_>=0 && capture_end_>capture_start_ &&
            capture_end_<=MaxSimNs(),"invalid bounded packet capture");
    for(const auto& n:c.at("nodes"))capture_nodes_.insert(n.get<unsigned>());
  }
  if(config.contains("faults")) {
    Require(config.at("faults").is_array() && config.at("faults").size()<=32,"fault rule budget exceeded");
    for(const auto& rule:config.at("faults"))fault_rules_.push_back(rule);
  }
  if(config.contains("drop_once")) {
    const auto& c=config.at("drop_once");
    fault_rules_.push_back({{"node",c.at("dst_gpu")},{"port",0},{"message_id",c.at("message_id")},
       {"kind","data"},{"sequence",c.at("sequence")},{"start_ns",0},{"end_ns",MaxSimNs()},{"count",1}});
  }
  fault_counts_.resize(fault_rules_.size(),0);
  const auto& ns = topology.at("nodes");
  const auto limit=[&](const char* key,unsigned fallback){return config.contains("limits")?config.at("limits").at(key).get<unsigned>():fallback;};
  const unsigned gpu_limit=limit("max_gpus",32),sw_limit=limit("max_switches",16),link_limit=limit("max_links",256);
  Require(gpu_limit<=1024 && sw_limit<=256 && link_limit<=16384 && MaxMessages()<=1000000 && MaxSimNs()<=1000000000,
          "execution limits exceed supported bounds");
  Require(ns.size() <= gpu_limit+sw_limit && topology.at("links").size() <= link_limit, "native graph budget exceeded");
  unsigned switches = 0;
  // Validate counts before constructing SwitchNode's large fixed MMU arrays.
  unsigned gpu_count=0, switch_count=0;
  for (const auto& n : ns) {
    if(n.at("kind") == "gpu") ++gpu_count;
    else {
      ++switch_count;
      Require(n.at("plane").get<unsigned>() < 16, "plane budget exceeded");
    }
  }
  Require(gpu_count > 0 && gpu_count <= gpu_limit && switch_count <= sw_limit,
          "GPU/switch budget exceeded before network allocation");
  if(config.contains("drop_once"))Require(config.at("drop_once").at("dst_gpu").get<unsigned>()<gpu_count,
          "legacy drop destination is not a GPU");
  for (const auto& n : ns) {
    Require(n.at("id") == nodes_.GetN(), "node IDs must be contiguous");
    if(n.at("kind") == "gpu") {
      Require(switches == 0, "GPU IDs must precede switches");
      nodes_.Add(CreateObject<Node>()); ++gpus_;
    } else {
      ++switches;
      planes_ = std::max(planes_, n.at("plane").get<unsigned>() + 1);
      auto sw = CreateObject<CnpSwitch>();
      sw->SetAttribute("EcnEnabled", BooleanValue(true));
      sw->SetAttribute("CcMode", UintegerValue(1));
      sw->SetAttribute("AckHighPrio", UintegerValue(1));
      sw->SetEcmpSeed(config.at("seed").get<uint32_t>() + n.at("id").get<uint32_t>());
      nodes_.Add(sw);
    }
  }
  Require(gpus_ > 0 && gpus_ <= gpu_limit && switches <= sw_limit && planes_ <= 16, "GPU/switch/plane budget exceeded");
  adjacency_.resize(nodes_.GetN()); drivers_.resize(gpus_);ports_.resize(nodes_.GetN());
  if(resource_tracking_) {
    deciding_.resize(gpus_,false);
    for(unsigned src=0;src<gpus_;++src)admission_.push_back(std::make_unique<moe_cosim::CommunicationAdmission>(
        admission_policy_,admission_limit_,MaxMessages(),admission_byte_limit_,admission_peer_limit_,admission_peer_bytes_));
  }
  reachable_.resize(gpus_*gpus_*planes_,0);
  InternetStackHelper internet; internet.Install(nodes_);
  unsigned edge_index=0;
  for(const auto& l : topology.at("links")) {
    const unsigned a=l.at("src"), b=l.at("dst");
    Require(a < b && b < nodes_.GetN(), "require upward-oriented unique physical edges");
    QbbHelper helper;
    helper.SetDeviceAttribute("DataRate", DataRateValue(DataRate(l.at("rate_bps").get<uint64_t>())));
    helper.SetChannelAttribute("Delay", TimeValue(NanoSeconds(l.at("delay_ns").get<int64_t>())));
    auto d=helper.Install(nodes_.Get(a), nodes_.Get(b));
    Ipv4AddressHelper ips;
    const uint32_t base = 0x0a000000u + (++edge_index << 8);
    ips.SetBase(Ipv4Address(base), Ipv4Mask("255.255.255.0")); ips.Assign(d);
    for(unsigned side=0;side<2;++side) {
      const unsigned host = side ? b : a, peer = side ? a : b;
      auto dev=DynamicCast<QbbNetDevice>(d.Get(side)); const unsigned port=dev->GetIfIndex();
      Require(port == l.at(side ? "dst_port" : "src_port"), "native port differs from exported logical port");
      devices_[{host,port}] = dev;
      adjacency_[host].push_back({peer,port});
      ports_[host].resize(port+1);
      ports_[host][port].device=dev;
      ports_[host][port].metadata = {{"node",host},{"port",port},{"peer",peer},{"tier",l.at("tier")},
          {"rate_bps",l.at("rate_bps")},{"delay_ns",l.at("delay_ns")},
          {"model_tx_bytes",0u},{"data_tx_bytes",0u},{"control_tx_bytes",0u},
          {"data_packets",0u},{"ecn_ce_seen",0u},{"pfc_pause",0u},{"pfc_resume",0u},{"max_queue_at_hook_bytes",0u}};
      Require(dev->TraceConnectWithoutContext("PhyTxBegin", MakeBoundCallback(&Fabric::TxTrace,this,host,port)), "missing native PhyTxBegin");
      Require(dev->TraceConnectWithoutContext("MacRx", MakeBoundCallback(&Fabric::RxTrace,this,host,port)), "missing native MacRx");
      if(capture_limit_ || recovery_)Require(dev->TraceConnectWithoutContext("PhyRxDrop",MakeBoundCallback(&Fabric::DropTrace,this,host,port)),"missing native PhyRxDrop");
      Require(dev->GetQueue()->TraceConnectWithoutContext("BeqEnqueue", MakeBoundCallback(&Fabric::QueueEnqueueTrace,this,host,port)), "missing BeqEnqueue");
      Require(dev->GetQueue()->TraceConnectWithoutContext("BeqDequeue", MakeBoundCallback(&Fabric::QueueDequeueTrace,this,host,port)), "missing BeqDequeue");
      Require(dev->TraceConnectWithoutContext("QbbPfc", MakeBoundCallback(&Fabric::PfcTrace,this,host,port)), "missing QbbPfc");
      if(host < gpus_) {
        const unsigned plane=ns.at(peer).at("plane"); auto ip=nodes_.Get(host)->GetObject<Ipv4>();
        ip->AddAddress(ip->GetInterfaceForDevice(dev), Ipv4InterfaceAddress(Address(host,plane),Ipv4Mask("255.0.0.0")));
      }
      if(std::any_of(fault_rules_.begin(),fault_rules_.end(),[host,port](const Json& r){
           return r.at("node")==host && (r.at("port")==0 || r.at("port")==port);})) {
        auto error=CreateObject<FaultModel>();
        error->corrupt=[this,host,port](Ptr<Packet> p){return Corrupt(host,port,p);};
        dev->SetAttribute("ReceiveErrorModel",PointerValue(error));
      }
    }
  }
  for(unsigned host=0;host<nodes_.GetN();++host) {
    if(host>=gpus_) {
      auto sw=DynamicCast<SwitchNode>(nodes_.Get(host));
      sw->m_mmu->ConfigBufferSize(config.at("switch_buffer_bytes"));
      sw->m_mmu->node_id=host;
      for(auto n:adjacency_[host]) {
        auto dev=devices_.at({host,n.port}); const auto rate=dev->GetDataRate().GetBitRate();
        auto ecn=config.at("ecn_by_rate").at(std::to_string(rate));
        // The fork's ConfigEcn API takes decimal KB and multiplies by 1000.
        // Its public MMU storage is in bytes; assign exact byte thresholds.
        sw->m_mmu->kmin[n.port]=ecn.at("kmin_bytes");
        sw->m_mmu->kmax[n.port]=ecn.at("kmax_bytes");
        sw->m_mmu->pmax[n.port]=ecn.at("pmax");
        const uint64_t delay=ports_.at(host).at(n.port).metadata.at("delay_ns");
        sw->m_mmu->ConfigHdrm(n.port, rate / 1000000 * delay / 8000 * 3);
        sw->m_mmu->pfc_a_shift[n.port]=3;
      }
      // Aggregate only after all per-port headroom entries have been written.
      sw->m_mmu->ConfigNPort(nodes_.Get(host)->GetNDevices()-1);
      Require(uint64_t(sw->m_mmu->total_hdrm)+sw->m_mmu->total_rsrv<sw->m_mmu->buffer_size,
              "headroom/reserve exceeds switch buffer");
      for(auto n:adjacency_[host]) {
        auto& row=ports_.at(host).at(n.port).metadata;
        row["ecn_kmin_bytes"]=sw->m_mmu->kmin[n.port];row["ecn_kmax_bytes"]=sw->m_mmu->kmax[n.port];
        row["headroom_bytes"]=sw->m_mmu->headroom[n.port];
        row["initial_pfc_threshold_bytes"]=sw->m_mmu->GetPfcThreshold(n.port);
      }
    } else {
      auto hw=CreateObject<RdmaHw>();
      hw->SetAttribute("CcMode",UintegerValue(1));
      hw->SetAttribute("Mtu",UintegerValue(config.at("payload_bytes").get<uint32_t>()));
      hw->SetAttribute("L2ChunkSize",UintegerValue(config.at("chunk_bytes").get<uint32_t>()));
      hw->SetAttribute("L2AckInterval",UintegerValue(config.at("ack_interval_bytes").get<uint32_t>()));
      hw->SetAttribute("L2BackToZero",BooleanValue(false));
      hw->SetAttribute("TotalPauseTimes",UintegerValue(0));
      drivers_[host]=CreateObject<RdmaDriver>(); auto driver=drivers_[host];
      driver->SetNode(nodes_.Get(host)); driver->SetRdmaHw(hw); nodes_.Get(host)->AggregateObject(driver); driver->Init();
      // Read effective attributes from the initialized object, including defaults.
      // This is provenance, not a calibrated configuration or a new override.
      Json attributes=Json::object();
      for(const auto* key:{"MinRate","RateAI","RateHAI"}) {
        DataRateValue value;hw->GetAttribute(key,value);attributes[key]=value.Get().GetBitRate();
      }
      for(const auto* key:{"EwmaGain","RateOnFirstCnp","RPTimer","RateDecreaseInterval","AlphaResumInterval"}) {
        DoubleValue value;hw->GetAttribute(key,value);attributes[key]=value.Get();
      }
      for(const auto* key:{"ClampTargetRate","RateBound","L2BackToZero"}) {
        BooleanValue value;hw->GetAttribute(key,value);attributes[key]=value.Get();
      }
      for(const auto* key:{"FastRecoveryTimes","CcMode","Mtu","L2ChunkSize","L2AckInterval","TotalPauseTimes"}) {
        UintegerValue value;hw->GetAttribute(key,value);attributes[key]=value.Get();
      }
      rdma_parameters_.push_back({{"node",host},{"attributes",attributes}});
      Require(driver->TraceConnectWithoutContext("QpComplete",MakeCallback(&Fabric::Acked,this)),"missing ACK-completion trace");
      if(recovery_)recovery_->Attach(host,hw);
    }
  }
  RdmaEgressQueue::ack_q_idx=0;
  // Reverse BFS per destination address/plane; GPUs are terminal endpoints.
  for(unsigned dst=0;dst<gpus_;++dst) for(unsigned plane=0;plane<planes_;++plane) {
    std::vector<int> distance(nodes_.GetN(),-1); std::queue<unsigned> queue;
    distance[dst]=0;queue.push(dst);
    while(!queue.empty()) {
      auto now=queue.front();queue.pop();
      for(auto peer:adjacency_[now]) {
        if(peer.peer>=gpus_ && ns.at(peer.peer).at("plane")!=plane)continue;
        if(distance[peer.peer]<0) {
          distance[peer.peer]=distance[now]+1;
          if(peer.peer>=gpus_)queue.push(peer.peer);
        }
      }
    }
    for(unsigned src=0;src<nodes_.GetN();++src) {
      if(src==dst || distance[src]<0)continue;
      if(src>=gpus_ && ns.at(src).at("plane")!=plane)continue;
      for(auto peer:adjacency_[src]) if(distance[peer.peer]==distance[src]-1) {
        if(peer.peer>=gpus_ && ns.at(peer.peer).at("plane")!=plane)continue;
        auto ip=Address(dst,plane);
        if(src<gpus_) {
          drivers_[src]->m_rdma->AddTableEntry(ip,peer.port); reachable_[(src*gpus_+dst)*planes_+plane]=true;
        } else DynamicCast<SwitchNode>(nodes_.Get(src))->AddTableEntry(ip,peer.port);
      }
    }
  }
  for(const auto n:capture_nodes_)Require(n<nodes_.GetN(),"capture node absent from topology");
  for(const auto& rule:fault_rules_) {
    const unsigned n=rule.at("node"),p=rule.at("port");
    Require(n<nodes_.GetN() && (p==0 || devices_.count({n,p})),"fault receive node/port absent from topology");
    const std::string kind=rule.at("kind");
    Require((kind=="data" || kind=="ack" || kind=="nack") && rule.at("count").get<unsigned>()>0 &&
      rule.at("count").get<unsigned>()<=10000 && rule.at("start_ns").get<int64_t>()>=0 &&
      rule.at("start_ns").get<int64_t>()<rule.at("end_ns").get<int64_t>() &&
      rule.at("end_ns").get<int64_t>()<=MaxSimNs(),"invalid bounded fault rule");
  }
}

// Internal, evaluation-only association. Never attach identity tags to packets.
int64_t Fabric::PacketFlow(Ptr<const Packet> p) const {
  const auto h=Header(p);
  if(h.l3Prot!=0x11 && h.l3Prot!=0xfc && h.l3Prot!=0xfd)return -1;
  const auto key=h.l3Prot==0x11?std::make_tuple(h.sip,h.dip,h.udp.sport):
      std::make_tuple(h.dip,h.sip,h.ack.dport);
  const auto it=port_to_flow_.find(key);
  return it==port_to_flow_.end()?-1:static_cast<int64_t>(it->second);
}
void Fabric::PacketEvent(unsigned event,unsigned node,unsigned port,Ptr<const Packet> p) {
  if(!capture_limit_ || Now()<capture_start_ || Now()>=capture_end_ ||
      (!capture_nodes_.empty() && !capture_nodes_.count(node)))return;
  ++packet_observations_;
  if(packet_events_.size()>=capture_limit_)return;
  const auto h=Header(p);
  const int64_t sequence=h.l3Prot==0x11?h.udp.seq:(h.l3Prot==0xfc || h.l3Prot==0xfd)?int64_t(h.ack.seq):-1;
  packet_events_.push_back({Now(),node,port,event,static_cast<int64_t>(p->GetUid()),PacketFlow(p),
      h.l3Prot,sequence,p->GetSize(),h.GetIpv4EcnBits(),
      ports_.at(node).at(port).device->GetQueue()->GetNBytesTotal(),
      h.l3Prot==0x11?static_cast<int64_t>(p->GetSize()-h.GetSerializedSize()):0});
  if(capture_wire_headers_) {
    const bool data=h.l3Prot==0x11;
    const bool ack=h.l3Prot==0xfc || h.l3Prot==0xfd;
    const std::array<int64_t,7> header={static_cast<int64_t>(p->GetUid()),h.sip,h.dip,h.l3Prot,
      data?h.udp.sport:ack?h.ack.sport:0,data?h.udp.dport:ack?h.ack.dport:0,data || ack};
    const auto [it,inserted]=wire_header_indices_.emplace(header,wire_headers_.size());
    if(inserted)wire_headers_.push_back(header);
    event_wire_headers_.push_back(it->second);
  }
}
bool Fabric::Corrupt(unsigned node,unsigned port,Ptr<Packet> p) {
  const auto id=PacketFlow(p);if(id<0)return false;
  const auto h=Header(p);const std::string kind=h.l3Prot==0x11?"data":h.l3Prot==0xfc?"ack":"nack";
  const uint32_t sequence=h.l3Prot==0x11?h.udp.seq:h.ack.seq;
  for(size_t i=0;i<fault_rules_.size();++i) {
    const auto& r=fault_rules_[i];
    if(r.at("node")==node && (r.at("port")==0 || r.at("port")==port) && r.at("message_id")==id &&
       r.at("kind")==kind && r.at("sequence")==sequence && Now()>=r.at("start_ns").get<int64_t>() &&
       Now()<r.at("end_ns").get<int64_t>() && fault_counts_[i]<r.at("count").get<unsigned>()) {
      ++fault_counts_[i];++fault_drops_;
      fault_events_.push_back({Now(),static_cast<int64_t>(i),node,port,static_cast<int64_t>(p->GetUid())});
      return true; // First matching rule owns this drop; counters shared across rails.
    }
  }
  return false;
}
void Fabric::DropTrace(Fabric* f,unsigned node,unsigned port,Ptr<const Packet> p) {
  if(f->recovery_) {Require(f->inflight_packets_>0,"drop without in-flight transmission");--f->inflight_packets_;f->CheckDrain();}
  f->PacketEvent(2,node,port,p);
}

void Fabric::Send(uint64_t id, uint32_t src,uint32_t dst,uint64_t bytes,unsigned plane,Done done,Done received) {
  Require(!persistent_stream_mode_,"legacy byte messages cannot share a persistent-stream controller");
  Require(src<gpus_ && dst<gpus_ && src!=dst && bytes>0 && bytes<=64*1024*1024 && plane<planes_,"invalid message");
  Require(reachable_[(src*gpus_+dst)*planes_+plane] && reachable_[(dst*gpus_+src)*planes_+plane],"no data/ACK packet-switch path");
  Require(id<2147483648u && !flows_.count(id) && flows_.size()<MaxMessages(),"message identity/count budget exceeded");
  // Stable across counterfactual runs even if completion order changes.
  const uint16_t port=10000+id%50000;
  Flow flow;flow.done=std::move(done);flow.received=std::move(received);
  flow.src=src;flow.dst=dst;flow.plane=plane;flow.bytes=bytes;flow.port=port;flow.ready=Now();
  flows_.emplace(id,std::move(flow));
  if(topology_.contains("intra_host") && topology_.at("intra_host").at("enabled")==true &&
     topology_.at("nodes").at(src).at("server")==topology_.at("nodes").at(dst).at("server")) {
    SendLocal(id);return;
  }
  const auto key=std::make_tuple(Address(src,plane).Get(),Address(dst,plane).Get(),port);
  Require(port_to_flow_.emplace(key,id).second,"RDMA endpoint/port identity collision; change bounded identity layout");
  if(resource_tracking_) {
    admission_.at(src)->Request({id,dst,bytes,Now()},Now());Lifecycle(0,id,src);PumpAdmission(src);
  } else Inject(id);
}
void Fabric::Inject(uint64_t id) {
  auto& f=flows_.at(id);Require(f.admitted<0,"logical transfer injected twice");f.admitted=Now();
  if(resource_tracking_)Lifecycle(1,id,f.src);
  drivers_[f.src]->AddQueuePair(f.src,f.dst,id,f.bytes,3,Address(f.src,f.plane),Address(f.dst,f.plane),f.port,f.port,0,
      config_.at("base_rtt_ns"),MakeCallback(&Fabric::Noop),MakeCallback(&Fabric::Noop));
  if(resource_tracking_) {
    Require(bool(drivers_[f.src]->m_rdma->GetQp(Address(f.dst,f.plane).Get(),f.port,3)),"native QP not registered");
    f.qp_registered=Now();Lifecycle(2,id,f.src);
  }
}
void Fabric::PumpAdmission(unsigned src) {
  auto& queue=*admission_.at(src);
  if(deciding_.at(src) || !queue.Eligible())return;
  if(!decision_ns_) {
    while(auto next=queue.Take(Now()))Inject(next->id);
    return;
  }
  // One source-local decision at a time. Select only from the queue that is
  // actually ready when this declared-cost event finishes; never poll at t=0.
  deciding_.at(src)=true;Lifecycle(6,0,src);
  Simulator::Schedule(NanoSeconds(decision_ns_),[this,src](){
    deciding_.at(src)=false;
    auto next=admission_.at(src)->Take(Now());
    if(next)Inject(next->id);
    PumpAdmission(src);
  });
}
void Fabric::Lifecycle(unsigned event,uint64_t id,unsigned src) {
  if(!lifecycle_limit_)return;
  ++lifecycle_observations_;
  if(lifecycle_events_.size()>=lifecycle_limit_)return;
  const auto& q=*admission_.at(src);
  lifecycle_events_.push_back({Now(),event,event==6?-1:static_cast<int64_t>(id),src,
      static_cast<int64_t>(q.Active().size()),static_cast<int64_t>(q.Pending()),q.Limit(),
      static_cast<int64_t>(drivers_.at(src)->m_rdma->m_qpMap.size())});
}
void Fabric::ReleaseSender(uint64_t id) {
  auto& f=flows_.at(id);
  Require(f.acked && f.resource_released<0 && !drivers_[f.src]->m_rdma->GetQp(Address(f.dst,f.plane).Get(),f.port,3),
          "credit release must follow native QP cleanup, once");
  admission_.at(f.src)->Complete(id,Now());f.resource_released=Now();Lifecycle(5,id,f.src);
  // Older queued work gets the returned credit before the completing graph
  // callback can produce new work. Data-ready never releases sender credits.
  PumpAdmission(f.src);
  if(f.done)f.done();
}

void Fabric::Acked(Ptr<RdmaQueuePair> qp) {
  auto& flow=flows_.at(qp->GetTag());
  Require(!flow.acked && qp->IsFinished(),"repeated or partial ACK completion");
  CheckReceived(qp->GetTag());
  Require(flow.data_ready,"ACK completion preceded receiver accepted all data");
  flow.acked=true;flow.ack=Now();
  flow.feedback=!qp->mlx.m_first_cnp;
  flow.rate_at_ack=qp->m_rate.GetBitRate();
  if(resource_tracking_)Lifecycle(4,qp->GetTag(),flow.src);
  // RdmaHw deletes its send QP after this trace returns. Do not load the next
  // graph or submit new work inside that cleanup stack.
  if(resource_tracking_)Simulator::ScheduleNow([this,id=qp->GetTag()](){ReleaseSender(id);});
  else if(flow.done)Simulator::ScheduleNow(flow.done);
}
void Fabric::CheckReceived(uint64_t id) {
  auto& f=flows_.at(id); if(f.data_ready)return;
  const uint16_t port=f.port;
  auto rx=drivers_[f.dst]->m_rdma->GetRxQp(Address(f.dst,f.plane).Get(),Address(f.src,f.plane).Get(),port,port,3,false);
  if(rx && rx->ReceiverNextExpectedSeq>=f.bytes) {
    f.data_ready=true;f.receive_ready=Now();
    if(resource_tracking_)Lifecycle(3,id,f.src);
    if(f.received)Simulator::ScheduleNow(f.received);
  }
}
void Fabric::TxTrace(Fabric* f,unsigned node,unsigned port,Ptr<const Packet> p) {
  if(f->recovery_)++f->inflight_packets_;
  f->PacketEvent(0,node,port,p);
  auto& row=f->ports_.at(node).at(port); const auto h=Header(p); const uint64_t size=p->GetSize();
  row.wire+=size;
  if(h.l3Prot==0x11)row.data+=size;else row.control+=size;
  if(h.l3Prot!=0x11)return;
  ++row.packets;
  auto it=f->port_to_flow_.find({h.sip,h.dip,h.udp.sport}); if(it==f->port_to_flow_.end())return;
  auto& flow=f->flows_.at(it->second);
  if(node==flow.src) {
    if(flow.first_tx<0)flow.first_tx=Now();
    if(flow.first_packet_uid<0)flow.first_packet_uid=p->GetUid();
    ++flow.packets;
    const uint64_t payload=size-h.GetSerializedSize();flow.payload+=payload;
    Require(h.udp.seq<=flow.source_covered_bytes && uint64_t(h.udp.seq)+payload<=flow.bytes,
            "source emitted a gap or bytes outside logical message");
    flow.source_covered_bytes=std::max(flow.source_covered_bytes,uint64_t(h.udp.seq)+payload);
  }
  if(h.udp.seq==0 && static_cast<int64_t>(p->GetUid())==flow.first_packet_uid)flow.path.push_back({node,port,Now()});
}
void Fabric::RxTrace(Fabric* f,unsigned node,unsigned port,Ptr<const Packet> p) {
  if(f->recovery_) {Require(f->inflight_packets_>0,"receive without in-flight transmission");--f->inflight_packets_;f->CheckDrain();}
  f->PacketEvent(1,node,port,p);
  auto h=Header(p);
  if(h.l3Prot==0x11 && h.GetIpv4EcnBits()==3) {
    ++f->ports_.at(node).at(port).ecn;
  }
  if(node>=f->gpus_ || h.l3Prot!=0x11)return;
  auto it=f->port_to_flow_.find({h.sip,h.dip,h.udp.sport});
  if(it!=f->port_to_flow_.end()) {
    const auto id=it->second;
    // MacRx fires before RdmaHw acceptance; inspect contiguous accepted bytes after it returns.
    Simulator::ScheduleNow([f,id](){f->CheckReceived(id);});
  }
}
void Fabric::QueueTrace(Fabric* f,unsigned node,unsigned port,Ptr<const Packet>,uint32_t) {
  if(node<f->gpus_)return;
  auto& row=f->ports_.at(node).at(port);
  const auto bytes=row.device->GetQueue()->GetNBytesTotal();
  row.max_queue=std::max(row.max_queue,bytes);
  ++f->queue_observations_;
  if(!f->config_.contains("limits") || f->config_.at("limits").at("queue_capture")=="events") {
    const auto maximum=f->config_.contains("limits")?f->config_.at("limits").at("max_queue_events").get<uint64_t>():200000;
    Require(f->queue_events_.size()<maximum,"passive queue event budget exceeded");
    f->queue_events_.push_back({static_cast<uint64_t>(Now()),node,port,bytes});
  }
}
void Fabric::QueueEnqueueTrace(Fabric* f,unsigned node,unsigned port,Ptr<const Packet> p,uint32_t q) {
  if(f->queue_metrics_ && node>=f->gpus_) {
    auto& row=f->ports_.at(node).at(port);const auto bytes=row.device->GetQueue()->GetNBytesTotal();
    Require(uint64_t(row.integral.Bytes())+p->GetSize()==bytes,"enqueue occupancy inconsistent with previous post-state");
    row.integral.Change(Now(),bytes);
  }
  f->PacketEvent(3,node,port,p);QueueTrace(f,node,port,p,q);
}
void Fabric::QueueDequeueTrace(Fabric* f,unsigned node,unsigned port,Ptr<const Packet> p,uint32_t q) {
  if(f->queue_metrics_ && node>=f->gpus_) {
    auto& row=f->ports_.at(node).at(port);const auto bytes=row.device->GetQueue()->GetNBytesTotal();
    Require(bytes>=p->GetSize() && row.integral.Bytes()==bytes,"dequeue occupancy inconsistent with previous post-state");
    row.integral.Change(Now(),bytes-p->GetSize()); // Native trace fires before byte subtraction.
  }
  f->PacketEvent(4,node,port,p);QueueTrace(f,node,port,p,q);
}
void Fabric::PfcTrace(Fabric* f,unsigned node,unsigned port,uint32_t type) {
  auto& row=f->ports_.at(node).at(port);
  if(type)++row.pause;else ++row.resume;
}
bool Fabric::Drained() const {
  for(const auto& flow:flows_)if(!flow.second.acked)return false;
  for(auto driver:drivers_)if(!driver->m_rdma->m_qpMap.empty())return false;
  if(resource_tracking_)for(unsigned src=0;src<gpus_;++src)
    if(!admission_[src]->Active().empty() || admission_[src]->Pending() || deciding_[src])return false;
  if(recovery_) {
    if(!recovery_->Idle() || inflight_packets_)return false;
    for(const auto& node:ports_)for(const auto& port:node)if(port.device && port.device->GetQueue()->GetNBytesTotal())return false;
    for(const auto& driver:drivers_)for(const auto& nic:driver->m_rdma->m_nic)
      if(nic.dev && nic.dev->GetRdmaQueue()->m_ackQ->GetNPackets())return false;
  }
  return true;
}
void Fabric::FinishWhenDrained(Done done) {
  Require(!finish_,"finish requested twice");
  if(!recovery_) {Require(Drained(),"premature service termination");done();return;}
  finish_=std::move(done);CheckDrain();
}
void Fabric::CheckDrain() {
  if(!finish_ || drain_check_pending_)return;
  drain_check_pending_=true;
  Simulator::ScheduleNow([this](){
    drain_check_pending_=false;
    if(finish_ && Drained()) {auto done=std::move(finish_);finish_={};done();}
  });
}
Json Fabric::Report() {
  Json r={{"native_network_executed",true},{"finish_ns",Now()},{"drained",Drained()},
    {"gpu_count",gpus_},{"switch_count",nodes_.GetN()-gpus_},{"planes",planes_},
    {"fault_drops",fault_drops_},
    {"messages",Json::array()},{"ports",Json::array()},
    {"queue_events",queue_events_},{"queue_event_columns",{"time_ns","node","port","bytes"}},
    {"wire_byte_scope","Native model packet sizes; excludes unmodelled Ethernet preamble/FCS/IFG"},
    {"queue_scope","BEgressQueue native hooks: enqueue after byte addition; dequeue before byte subtraction; excludes bytes already being serialized"},
    {"rx_qp_scope","Accepted receive state retained until Fabric teardown; no waiting receives at service completion"}};
  r["rdma_parameters"]={{"object_type","ns3::RdmaHw"},{"sample_phase","after_driver_init_before_QPs"},
    {"hosts",rdma_parameters_},{"hardware_calibrated",false},
    {"fabric_overrides",{"CcMode","Mtu","L2ChunkSize","L2AckInterval","L2BackToZero","TotalPauseTimes"}},
    {"other_attribute_source","pinned native defaults; no DCQCN parameter overrides in this adapter"},
    {"units",{{"MinRate","bit/s"},{"RateAI","bit/s"},{"RateHAI","bit/s"},
      {"RPTimer","us"},{"RateDecreaseInterval","us"},{"AlphaResumInterval","us"},
      {"EwmaGain","ratio"},{"RateOnFirstCnp","ratio"},{"ClampTargetRate","boolean"},{"RateBound","boolean"},
      {"L2BackToZero","boolean"},{"FastRecoveryTimes","count"},{"CcMode","enum"},{"Mtu","bytes"},
      {"L2ChunkSize","bytes"},{"L2AckInterval","bytes"},{"TotalPauseTimes","count"}}}};
  if(recovery_) {
    r["recovery"]=recovery_->Report();r["recovery"]["inflight_packets"]=inflight_packets_;
    r["recovery"]["finish_waiting_for_drain"]=bool(finish_);
    if(failure_id_>=0)r["failure"]={{"reason","recovery_retry_exhausted"},{"message_id",failure_id_},{"time_ns",failure_ns_},
      {"scope","whole shared simulation stopped; no successful callbacks or credits synthesized; live resources retained for diagnosis until teardown"}};
    else if(Now()>=MaxSimNs() && !Drained())r["failure"]={{"reason","simulation_time_limit"},{"time_ns",Now()},
      {"scope","simulation budget expired before drain; not a transport retry-exhaustion event"}};
  }
  // Numeric observer state is converted only after Run(). Sort identities so
  // hash-table lookup optimization cannot change exported ordering.
  const auto time=[](int64_t value)->Json{return value<0?Json(nullptr):Json(value);};
  std::vector<uint64_t> identities;
  for(const auto& item:flows_)identities.push_back(item.first);
  std::sort(identities.begin(),identities.end());
  for(const auto id:identities) {
    const auto& f=flows_.at(id);
    Json row={{"id",id},{"src",f.src},{"dst",f.dst},{"bytes",f.bytes},{"plane",f.plane},{"port",f.port},
      {"ready_ns",f.ready},{"first_tx_ns",time(f.first_tx)},{"receive_ready_ns",time(f.receive_ready)},{"ack_ns",time(f.ack)},
      {"ack_callbacks",f.acked?1:0},{"source_data_packets",f.packets},{"source_data_payload_bytes",f.payload},
      {"first_packet_path",Json::array()}};
    if(capture_limit_ || !fault_rules_.empty() || resource_tracking_) {
      auto rx=f.local?Ptr<RdmaRxQueuePair>():drivers_[f.dst]->m_rdma->GetRxQp(Address(f.dst,f.plane).Get(),Address(f.src,f.plane).Get(),f.port,f.port,3,false);
      row["receiver_contiguous_bytes"]=f.local?f.received_bytes:rx?rx->ReceiverNextExpectedSeq:0;
      row["source_unique_data_payload_bytes"]=f.local?f.payload:f.source_covered_bytes;
      row["first_packet_uid"]=f.first_packet_uid;
      row["completion_state"]=f.acked?"complete":f.data_ready?"awaiting_sender_ack":
          resource_tracking_ && !f.local && f.admitted<0?"awaiting_admission":"awaiting_contiguous_data";
    }
    if(resource_tracking_ && !f.local) {
      row["admission_granted_ns"]=time(f.admitted);row["qp_registered_ns"]=time(f.qp_registered);
      row["sender_resource_released_ns"]=time(f.resource_released);
      row["sender_qp_present"]=bool(drivers_[f.src]->m_rdma->GetQp(Address(f.dst,f.plane).Get(),f.port,3));
      // Read only source protocol state. A pending request has no native QP;
      // after native cleanup the full ACK is known from the completion trace.
      auto qp=drivers_[f.src]->m_rdma->GetQp(Address(f.dst,f.plane).Get(),f.port,3);
      row["sender_progress"]={{"scope","source_QP_snapshot; no timeout/recovery is inferred"},
        {"snd_una_bytes",qp?Json(qp->snd_una):f.acked?Json(f.bytes):Json(nullptr)},
        {"snd_nxt_bytes",qp?Json(qp->snd_nxt):f.acked?Json(f.bytes):Json(nullptr)}};
    }
    for(const auto& hop:f.path)row["first_packet_path"].push_back({{"node",hop.node},{"port",hop.port},{"time_ns",hop.time}});
    if(f.local) {
      row["transport"]="intra_host_chunked_crossbar";
      row["completion_kind"]="local_data_ready_no_RDMA_ACK";row["intra_host_chunks"]=f.chunks;
      if(f.ingress_start>=0) {
        row["intra_first_chunk"]={{"ingress_start_ns",f.ingress_start},{"ingress_end_ns",f.ingress_end}};
        if(f.egress_start>=0) {
          row["intra_first_chunk"]["egress_start_ns"]=f.egress_start;
          row["intra_first_chunk"]["egress_end_ns"]=f.egress_end;
        }
      }
    } else if(f.acked) {
      row["dcqcn_feedback_seen"]=f.feedback;row["sender_rate_at_ack_bps"]=f.rate_at_ack;
    }
    r["messages"].push_back(std::move(row));
  }
  if(resource_tracking_) {
    Json sources=Json::array();
    for(unsigned src=0;src<gpus_;++src)sources.push_back({{"src",src},{"active_ids",admission_[src]->Active()},
      {"pending_ids",admission_[src]->PendingIds()},{"decision_pending",deciding_[src]?true:false},
      {"native_send_qps",drivers_[src]->m_rdma->m_qpMap.size()}});
    r["admission"]={{"policy",admission_policy_},{"max_active_per_source",admission_limit_},{"decision_ns",decision_ns_},
      {"scope","inter_host_source_local_software_credits; zero limit is uncapped, not hardware capacity"},{"sources",sources}};
    if(admission_peer_limit_)r["admission"]["per_destination"]={{"max_active",admission_peer_limit_},{"max_bytes",admission_peer_bytes_}};
    if(byte_tracking_) {
      r["admission"]["max_active_bytes_per_source"]=admission_byte_limit_;
      r["admission"]["byte_scope"]="Sum of full logical payloads from grant through sender resource release; not NIC in-flight bytes or GPU memory";
      for(unsigned src=0;src<gpus_;++src) {
        auto& source=r["admission"]["sources"][src];
        source["active_bytes"]=admission_[src]->ActiveBytes();source["pending_bytes"]=admission_[src]->PendingBytes();
      }
    }
  }
  if(lifecycle_limit_)r["lifecycle_capture"]={{"scope","evaluation_only; sender credits and native QP, not GPU buffers"},
      {"events",lifecycle_events_},{"columns",{"time_ns","event","message_id","source","active","pending","software_limit","native_send_qps"}},
      {"event_codes",{{"request",0},{"grant",1},{"qp_registered",2},{"data_ready",3},{"ack_complete",4},{"qp_map_removed_credit_release",5},{"decision_start",6}}},
      {"observations",lifecycle_observations_},{"complete",lifecycle_observations_==lifecycle_events_.size()},
      {"omitted_events",lifecycle_observations_-lifecycle_events_.size()},
      {"owner_scope","Grant/release deltas reconstruct the full active owner set from t=0 when capture is complete; final owner sets are in admission.sources"}};
  if(capture_limit_) {
    r["packet_capture"]={{"scope","evaluation_only_native_device_hooks"},{"selection",config_.at("packet_capture")},{"events",packet_events_},
      {"columns",{"time_ns","node","port","event","packet_uid","message_id","ip_protocol","sequence","model_bytes","ecn","egress_queue_bytes","data_payload_bytes"}},
      {"event_codes",{{"tx",0},{"rx",1},{"drop",2},{"beq_enqueue",3},{"beq_dequeue",4}}},{"protocol_codes",{{"data",17},{"ack",252},{"nack",253},{"pfc",254},{"cnp",255}}},
      {"observations",packet_observations_},{"complete",packet_observations_==packet_events_.size()},
      {"omitted_events",packet_observations_-packet_events_.size()},
      {"notes","rx precedes protocol acceptance; drop is PhyRxDrop; BEgressQueue hooks exclude NIC data QP and separate high-priority ACK queue; UID is ns-3 packet lineage, not globally unique transmission; sequence is data offset or cumulative ACK/NACK next expected byte; -1 means unassociated"}};
    if(capture_wire_headers_)r["packet_capture"]["wire_headers"]={
      {"columns",{"packet_uid","ip_src","ip_dst","ip_protocol","source_port","destination_port","ports_present"}},
      {"headers",wire_headers_},{"event_header_indices",event_wire_headers_},
      {"scope","actual native header fields, deduplicated by full row in first-saved-event order; one index per saved event, same capture bounds; IPv4 addresses are numeric; UDP17 and qbb ACK252/NACK253 ports, otherwise ports_present=0 and ports=0; UID is lineage, not a unique transmission; evaluation only"}};
  }
  if(!fault_rules_.empty()) {
    r["fault_injection"]={{"rules",fault_rules_},{"drop_counts",fault_counts_},{"events",fault_events_},
      {"event_columns",{"time_ns","rule_index","node","port","packet_uid"}},
      {"scope","receive error model before MacRx; first matching rule; count shared across devices"}};
  }
  for(auto& node:ports_)for(unsigned index=1;index<node.size();++index) {
    auto& p=node[index];Json row=std::move(p.metadata);
    row["model_tx_bytes"]=p.wire;row["data_tx_bytes"]=p.data;row["control_tx_bytes"]=p.control;
    row["data_packets"]=p.packets;row["ecn_ce_seen"]=p.ecn;row["pfc_pause"]=p.pause;row["pfc_resume"]=p.resume;
    row["max_queue_at_hook_bytes"]=p.max_queue;
    if(queue_metrics_ && row.at("node").get<unsigned>()>=gpus_) {
      Require(p.integral.Bytes()==p.device->GetQueue()->GetNBytesTotal(),"final passive queue state differs from native queue");
      row["queue_metrics"]={{"area_byte_ns",p.integral.Area(Now())},{"peak_bytes",p.integral.Peak()},
        {"final_bytes",p.integral.Bytes()},{"changes",p.integral.Events()}};
    }
    r["ports"].push_back(std::move(row));
  }
  if(queue_metrics_)r["queue_metrics_scope"]="All switch BEgressQueue ports, all priorities, [0,finish_ns]; post-change occupancy excludes serialization; exact without packet capture";
  if(config_.contains("limits")) {
    r["queue_observations"]=queue_observations_;
    r["queue_capture"]=config_.at("limits").at("queue_capture");
    r["execution_limits"]=config_.at("limits");
  }
  if(topology_.contains("intra_host")) {
    r["intra_host_model"]=topology_.at("intra_host");r["intra_host_links"]=Json::array();
    for(const auto& item:local_links_)r["intra_host_links"].push_back(item.second);
  }
  return r;
}

void Fabric::SendLocal(uint64_t id) {
  auto& flow=flows_.at(id);const auto local=topology_.at("intra_host");
  const uint64_t rate=local.at("rate_bps"),chunk=local.at("chunk_bytes");
  const int64_t delay=local.at("delay_ns");
  Require(local.at("kind")=="nonblocking_chunked_crossbar" && rate>0 && chunk>0,"invalid local fabric");
  flow.local=true;
  const unsigned src=flow.src,dst=flow.dst;
  const auto reserve=[this,rate](unsigned gpu,bool ingress,int64_t arrival,uint64_t size){
    auto key=std::make_pair(gpu,ingress);
    const int64_t start=std::max(arrival,local_free_[key]);
    const int64_t duration=(size*8000000000ull+rate-1)/rate;
    local_free_[key]=start+duration;
    if(!local_links_.count(key))local_links_[key]={{"gpu",gpu},{"direction",ingress?"to_switch":"from_switch"},
      {"server",topology_.at("nodes").at(gpu).at("server")},{"rate_bps",rate},{"payload_bytes",0ull},
      {"busy_ns",0ull},{"max_wait_ns",0ull},{"chunks",0ull}};
    auto& link=local_links_.at(key);
    link["payload_bytes"]=link.at("payload_bytes").get<uint64_t>()+size;
    link["busy_ns"]=link.at("busy_ns").get<uint64_t>()+duration;
    link["chunks"]=link.at("chunks").get<uint64_t>()+1;
    link["max_wait_ns"]=std::max(link.at("max_wait_ns").get<uint64_t>(),static_cast<uint64_t>(start-arrival));
    return std::make_pair(start,start+duration);
  };
  for(uint64_t offset=0;offset<flow.bytes;offset+=chunk) {
    const auto size=std::min(chunk,flow.bytes-offset);const auto interval=reserve(src,true,Now(),size);
    Simulator::Schedule(NanoSeconds(interval.first-Now()),[this,id,size,offset,interval](){
      auto& f=flows_.at(id);
      if(f.first_tx<0)f.first_tx=Now();
      f.payload+=size;++f.chunks;
      if(offset==0) {f.ingress_start=interval.first;f.ingress_end=interval.second;}
    });
    Simulator::Schedule(NanoSeconds(interval.second+delay-Now()),[this,id,dst,size,offset,delay,reserve](){
      const auto output=reserve(dst,false,Now(),size);
      if(offset==0) {
        flows_.at(id).egress_start=output.first;
        flows_.at(id).egress_end=output.second;
      }
      Simulator::Schedule(NanoSeconds(output.second+delay-Now()),[this,id,size](){
        auto& f=flows_.at(id);f.received_bytes+=size;
        Require(f.received_bytes<=f.bytes,"local received bytes exceeded message");
        if(f.received_bytes==f.bytes) {
          f.data_ready=f.acked=true;f.receive_ready=f.ack=Now();
          if(f.received)Simulator::ScheduleNow(f.received);
          if(f.done)Simulator::ScheduleNow(f.done);
        }
      });
    });
  }
}
}
