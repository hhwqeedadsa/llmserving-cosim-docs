#include "fabric.hh"
#include "ns3/custom-header.h"
#include "ns3/error-model.h"
#include "ns3/rdma-hw.h"
#include "ns3/switch-node.h"
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
class DropOnce final: public ErrorModel {
 public:
  static TypeId GetTypeId() {
    static TypeId tid=TypeId("moe_rdma::DropOnce").SetParent<ErrorModel>().AddConstructor<DropOnce>();return tid;
  }
  uint16_t port=0;uint32_t sequence=0;bool dropped=false;std::function<void()> observed;
 private:
  bool DoCorrupt(Ptr<Packet> p) override {
    const auto h=Header(p);
    if(!dropped && h.l3Prot==0x11 && h.udp.sport==port && h.udp.seq==sequence) {
      dropped=true;observed();return true;
    }
    return false;
  }
  void DoReset() override { dropped=false; }
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
  for (const auto& n : ns) {
    Require(n.at("id") == nodes_.GetN(), "node IDs must be contiguous");
    if(n.at("kind") == "gpu") {
      Require(switches == 0, "GPU IDs must precede switches");
      nodes_.Add(CreateObject<Node>()); ++gpus_;
    } else {
      ++switches;
      planes_ = std::max(planes_, n.at("plane").get<unsigned>() + 1);
      auto sw = CreateObject<SwitchNode>();
      sw->SetAttribute("EcnEnabled", BooleanValue(true));
      sw->SetAttribute("CcMode", UintegerValue(1));
      sw->SetAttribute("AckHighPrio", UintegerValue(1));
      sw->SetEcmpSeed(config.at("seed").get<uint32_t>() + n.at("id").get<uint32_t>());
      nodes_.Add(sw);
    }
  }
  Require(gpus_ > 0 && gpus_ <= gpu_limit && switches <= sw_limit && planes_ <= 16, "GPU/switch/plane budget exceeded");
  adjacency_.resize(nodes_.GetN()); drivers_.resize(gpus_);
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
      ports_[{host,port}] = {{"node",host},{"port",port},{"peer",peer},{"tier",l.at("tier")},
          {"rate_bps",l.at("rate_bps")},{"delay_ns",l.at("delay_ns")},
          {"model_tx_bytes",0u},{"data_tx_bytes",0u},{"control_tx_bytes",0u},
          {"data_packets",0u},{"ecn_ce_seen",0u},{"pfc_pause",0u},{"pfc_resume",0u},{"max_queue_at_hook_bytes",0u}};
      Require(dev->TraceConnectWithoutContext("PhyTxBegin", MakeBoundCallback(&Fabric::TxTrace,this,host,port)), "missing native PhyTxBegin");
      Require(dev->TraceConnectWithoutContext("MacRx", MakeBoundCallback(&Fabric::RxTrace,this,host,port)), "missing native MacRx");
      Require(dev->GetQueue()->TraceConnectWithoutContext("BeqEnqueue", MakeBoundCallback(&Fabric::QueueTrace,this,host,port)), "missing BeqEnqueue");
      Require(dev->GetQueue()->TraceConnectWithoutContext("BeqDequeue", MakeBoundCallback(&Fabric::QueueTrace,this,host,port)), "missing BeqDequeue");
      Require(dev->TraceConnectWithoutContext("QbbPfc", MakeBoundCallback(&Fabric::PfcTrace,this,host,port)), "missing QbbPfc");
      if(host < gpus_) {
        const unsigned plane=ns.at(peer).at("plane"); auto ip=nodes_.Get(host)->GetObject<Ipv4>();
        ip->AddAddress(ip->GetInterfaceForDevice(dev), Ipv4InterfaceAddress(Address(host,plane),Ipv4Mask("255.0.0.0")));
        if(config.contains("drop_once") && host==config.at("drop_once").at("dst_gpu")) {
          auto error=CreateObject<DropOnce>();error->port=10000+config.at("drop_once").at("message_id").get<unsigned>();
          error->sequence=config.at("drop_once").at("sequence");error->observed=[this](){++fault_drops_;};
          dev->SetAttribute("ReceiveErrorModel",PointerValue(error));
        }
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
        const uint64_t delay=ports_.at({host,n.port}).at("delay_ns");
        sw->m_mmu->ConfigHdrm(n.port, rate / 1000000 * delay / 8000 * 3);
        sw->m_mmu->pfc_a_shift[n.port]=3;
      }
      // Aggregate only after all per-port headroom entries have been written.
      sw->m_mmu->ConfigNPort(nodes_.Get(host)->GetNDevices()-1);
      Require(uint64_t(sw->m_mmu->total_hdrm)+sw->m_mmu->total_rsrv<sw->m_mmu->buffer_size,
              "headroom/reserve exceeds switch buffer");
      for(auto n:adjacency_[host]) {
        auto& row=ports_.at({host,n.port});
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
      Require(driver->TraceConnectWithoutContext("QpComplete",MakeCallback(&Fabric::Acked,this)),"missing ACK-completion trace");
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
          drivers_[src]->m_rdma->AddTableEntry(ip,peer.port); reachable_[{src,dst,plane}]=true;
        } else DynamicCast<SwitchNode>(nodes_.Get(src))->AddTableEntry(ip,peer.port);
      }
    }
  }
}

void Fabric::Send(uint64_t id, uint32_t src,uint32_t dst,uint64_t bytes,unsigned plane,Done done,Done received) {
  Require(src<gpus_ && dst<gpus_ && src!=dst && bytes>0 && bytes<=64*1024*1024 && plane<planes_,"invalid message");
  Require(reachable_[{src,dst,plane}] && reachable_[{dst,src,plane}],"no data/ACK packet-switch path");
  Require(id<2147483648u && !flows_.count(id) && flows_.size()<MaxMessages(),"message identity/count budget exceeded");
  // Stable across counterfactual runs even if completion order changes.
  const uint16_t port=10000+id%50000;
  Json row={{"id",id},{"src",src},{"dst",dst},{"bytes",bytes},{"plane",plane},{"port",port},
    {"ready_ns",Now()},{"first_tx_ns",nullptr},{"receive_ready_ns",nullptr},{"ack_ns",nullptr},
    {"ack_callbacks",0},{"source_data_packets",0},{"source_data_payload_bytes",0},{"first_packet_path",Json::array()}};
  flows_.emplace(id,Flow{row,std::move(done),std::move(received),false,false,src,dst,plane,bytes});
  if(topology_.contains("intra_host") && topology_.at("intra_host").at("enabled")==true &&
     topology_.at("nodes").at(src).at("server")==topology_.at("nodes").at(dst).at("server")) {
    SendLocal(id);return;
  }
  const auto key=std::make_tuple(Address(src,plane).Get(),Address(dst,plane).Get(),port);
  Require(port_to_flow_.emplace(key,id).second,"RDMA endpoint/port identity collision; change bounded identity layout");
  drivers_[src]->AddQueuePair(src,dst,id,bytes,3,Address(src,plane),Address(dst,plane),port,port,0,
      config_.at("base_rtt_ns"),MakeCallback(&Fabric::Noop),MakeCallback(&Fabric::Noop));
}

void Fabric::Acked(Ptr<RdmaQueuePair> qp) {
  auto& flow=flows_.at(qp->GetTag());
  Require(!flow.acked && qp->IsFinished(),"repeated or partial ACK completion");
  CheckReceived(qp->GetTag());
  Require(flow.data_ready,"ACK completion preceded receiver accepted all data");
  flow.acked=true;flow.record["ack_callbacks"]=1;flow.record["ack_ns"]=Now();
  flow.record["dcqcn_feedback_seen"]=!qp->mlx.m_first_cnp;
  flow.record["sender_rate_at_ack_bps"]=qp->m_rate.GetBitRate();
  // RdmaHw deletes its send QP after this trace returns. Do not load the next
  // graph or submit new work inside that cleanup stack.
  if(flow.done)Simulator::ScheduleNow(flow.done);
}
void Fabric::CheckReceived(uint64_t id) {
  auto& f=flows_.at(id); if(f.data_ready)return;
  const uint16_t port=f.record.at("port");
  auto rx=drivers_[f.dst]->m_rdma->GetRxQp(Address(f.dst,f.plane).Get(),Address(f.src,f.plane).Get(),port,port,3,false);
  if(rx && rx->ReceiverNextExpectedSeq>=f.bytes) {
    f.data_ready=true;f.record["receive_ready_ns"]=Now();
    if(f.received)Simulator::ScheduleNow(f.received);
  }
}
void Fabric::TxTrace(Fabric* f,unsigned node,unsigned port,Ptr<const Packet> p) {
  auto& row=f->ports_.at({node,port}); const auto h=Header(p); const uint64_t size=p->GetSize();
  row["model_tx_bytes"]=row.at("model_tx_bytes").get<uint64_t>()+size;
  const std::string field=h.l3Prot==0x11?"data_tx_bytes":"control_tx_bytes";
  row[field]=row.at(field).get<uint64_t>()+size;
  if(h.l3Prot!=0x11)return;
  row["data_packets"]=row.at("data_packets").get<uint64_t>()+1;
  auto it=f->port_to_flow_.find({h.sip,h.dip,h.udp.sport}); if(it==f->port_to_flow_.end())return;
  auto& flow=f->flows_.at(it->second);
  if(node==flow.src) {
    if(flow.record["first_tx_ns"].is_null())flow.record["first_tx_ns"]=Now();
    flow.record["source_data_packets"]=flow.record["source_data_packets"].get<unsigned>()+1;
    flow.record["source_data_payload_bytes"]=flow.record["source_data_payload_bytes"].get<uint64_t>()+size-h.GetSerializedSize();
  }
  if(h.udp.seq==0)flow.record["first_packet_path"].push_back({{"node",node},{"port",port},{"time_ns",Now()}});
}
void Fabric::RxTrace(Fabric* f,unsigned node,unsigned port,Ptr<const Packet> p) {
  auto h=Header(p);
  if(h.l3Prot==0x11 && h.GetIpv4EcnBits()==3) {
    auto& r=f->ports_.at({node,port});r["ecn_ce_seen"]=r.at("ecn_ce_seen").get<unsigned>()+1;
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
  auto device=f->devices_.at({node,port}); if(node<f->gpus_)return;
  const auto bytes=device->GetQueue()->GetNBytesTotal();auto& row=f->ports_.at({node,port});
  row["max_queue_at_hook_bytes"]=std::max(row.at("max_queue_at_hook_bytes").get<uint32_t>(),bytes);
  ++f->queue_observations_;
  if(!f->config_.contains("limits") || f->config_.at("limits").at("queue_capture")=="events") {
    const auto maximum=f->config_.contains("limits")?f->config_.at("limits").at("max_queue_events").get<uint64_t>():200000;
    Require(f->queue_events_.size()<maximum,"passive queue event budget exceeded");
    f->queue_events_.push_back({static_cast<uint64_t>(Now()),node,port,bytes});
  }
}
void Fabric::PfcTrace(Fabric* f,unsigned node,unsigned port,uint32_t type) {
  auto& row=f->ports_.at({node,port});const auto key=type?"pfc_pause":"pfc_resume";
  row[key]=row.at(key).get<unsigned>()+1;
}
bool Fabric::Drained() const {
  for(const auto& flow:flows_)if(!flow.second.acked)return false;
  for(auto driver:drivers_)if(!driver->m_rdma->m_qpMap.empty())return false;
  return true;
}
Json Fabric::Report() {
  Json r={{"native_network_executed",true},{"finish_ns",Now()},{"drained",Drained()},
    {"gpu_count",gpus_},{"switch_count",nodes_.GetN()-gpus_},{"planes",planes_},
    {"fault_drops",fault_drops_},
    {"messages",Json::array()},{"ports",Json::array()},
    {"queue_events",queue_events_},{"queue_event_columns",{"time_ns","node","port","bytes"}},
    {"wire_byte_scope","Native model packet sizes; excludes unmodelled Ethernet preamble/FCS/IFG"},
    {"queue_scope","BEgressQueue after enqueue/dequeue; excludes bytes already being serialized"},
    {"rx_qp_scope","Accepted receive state retained until Fabric teardown; no waiting receives at service completion"}};
  // Terminal reporting consumes records instead of duplicating every flow's
  // JSON tree. Called once after Run(); no packet/callback may follow it.
  for(auto& item:flows_)r["messages"].push_back(std::move(item.second.record));
  for(auto& item:ports_)r["ports"].push_back(std::move(item.second));
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
  flow.record["transport"]="intra_host_chunked_crossbar";
  flow.record["completion_kind"]="local_data_ready_no_RDMA_ACK";
  flow.record["intra_host_chunks"]=0;
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
      if(f.record["first_tx_ns"].is_null())f.record["first_tx_ns"]=Now();
      f.record["source_data_payload_bytes"]=f.record.at("source_data_payload_bytes").get<uint64_t>()+size;
      f.record["intra_host_chunks"]=f.record.at("intra_host_chunks").get<uint64_t>()+1;
      if(offset==0)f.record["intra_first_chunk"]={{"ingress_start_ns",interval.first},{"ingress_end_ns",interval.second}};
    });
    Simulator::Schedule(NanoSeconds(interval.second+delay-Now()),[this,id,dst,size,offset,delay,reserve](){
      const auto output=reserve(dst,false,Now(),size);
      if(offset==0) {
        flows_.at(id).record["intra_first_chunk"]["egress_start_ns"]=output.first;
        flows_.at(id).record["intra_first_chunk"]["egress_end_ns"]=output.second;
      }
      Simulator::Schedule(NanoSeconds(output.second+delay-Now()),[this,id,size](){
        auto& f=flows_.at(id);f.received_bytes+=size;
        Require(f.received_bytes<=f.bytes,"local received bytes exceeded message");
        if(f.received_bytes==f.bytes) {
          f.data_ready=f.acked=true;f.record["receive_ready_ns"]=Now();f.record["ack_ns"]=Now();
          f.record["ack_callbacks"]=1;
          if(f.received)Simulator::ScheduleNow(f.received);
          if(f.done)Simulator::ScheduleNow(f.done);
        }
      });
    });
  }
}
}
