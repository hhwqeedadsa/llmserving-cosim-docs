#include "persistent_streams.hh"
#include "remote_memory.hh"
#include "low_latency_posting.hh"
#include "legacy_event_graph.hh"
#include "legacy_execution_validation.hh"
#include <fstream>
#include <iostream>
#include <link.h>
#include <tuple>

using namespace moe_rdma;
namespace {
int64_t Now(){return ns3::Simulator::Now().GetNanoSeconds();}
std::string Hex(const std::vector<uint8_t>& bytes) {
  static const char digits[]="0123456789abcdef";std::string out;out.reserve(2*bytes.size());
  for(const auto b:bytes){out+=digits[b>>4];out+=digits[b&15];}return out;
}
using Key=std::tuple<unsigned,unsigned,unsigned>;
struct Event {std::string kind;unsigned rank;int operation;Json attributes;};
struct Operation {
  std::string id,kind;unsigned call,phase,src,dst,expert,bytes,token=0,context=0;
  uint32_t value=0,index=0;std::array<unsigned,4> events;
  uint64_t address=0,sequence=0;int64_t ticket=-1,end=-1;
  bool reserved=false,posted=false,sent=false,ack=false,response=false,retired=false;
  uint64_t old_value=0;int64_t publication=-1,applied=-1,acked=-1,replied=-1,completed=-1;
  std::vector<uint8_t> captured;
};
struct Context {
  unsigned id,src,dst,local_expert,remote_index;bool remote;
  uint64_t base,phase_bytes;LowLatencyPostingQueue queue;
  RemoteMemory memory;MemoryFrameDecoder requests,replies;std::vector<unsigned> operations;
  unsigned retired=0;
  Context(unsigned i,unsigned s,unsigned d,unsigned e,unsigned ri,unsigned depth,unsigned maximum,uint64_t stride,unsigned gpu)
    :id(i),src(s),dst(d),local_expert(e),remote_index(ri),remote(s!=d),
     base((uint64_t(i)+1)*2*stride),phase_bytes(stride),queue(depth,maximum) {
    Require(remote==LegacyUsesPostingQueue(s,d),"legacy local queue classification mismatch");
    memory.ConfigureRequestLimits(1,1024);
    memory.RegisterSparse(gpu,1,base,true,2*stride,{});
  }
};
class Executor {
 public:
  Executor(Fabric& fabric,const Json& input):fabric_(fabric),input_(input),
    transport_(fabric,{input.at("limits").at("streams"),input.at("limits").at("work"),input.at("limits").at("link_transmissions")},input.at("transport_recovery")) {
    ranks_=input.at("ranks");experts_=input.at("experts");local_experts_=experts_/ranks_;
    hidden_=input.at("hidden_size");dispatch_bytes_=input.at("dispatch_bytes");max_tokens_=input.at("max_tokens_per_rank");
    rank_gpu_=input.at("rank_to_gpu").get<std::vector<unsigned>>();const auto depth=input.at("queue_depth").get<unsigned>();
    stride_=((uint64_t(max_tokens_)*dispatch_bytes_+4+4095)/4096)*4096;
    unsigned remote=0;
    for(unsigned s=0;s<ranks_;++s)for(unsigned d=0;d<ranks_;++d)for(unsigned e=0;e<local_experts_;++e) {
      const auto id=contexts_.size();lookup_[{s,d,e}]=id;
      contexts_.emplace_back(id,s,d,e,remote,depth,1024,stride_,rank_gpu_.at(d));
      if(s!=d)++remote;
    }
    for(const auto& row:input.at("events")) {
      Event e{row.at(0),row.at(1),row.at(3).is_null()?-1:row.at(3).get<int>(),row.at(4)};
      events_.push_back(e);graph_.Add(row.at(2).get<std::vector<unsigned>>(),e.kind=="accepted" || e.kind=="sender_done");
    }
    for(const auto& row:input.at("operations")) {
      Operation op;op.id=row.at(0);op.call=row.at(1);op.phase=row.at(2);op.kind=row.at(3);
      op.src=row.at(4);op.dst=row.at(5);op.expert=row.at(6);op.bytes=row.at(8);
      if(!row.at(7).is_null())op.token=row.at(7);
      op.value=static_cast<uint32_t>(row.at(9).get<int64_t>());
      op.events=row.at(10).get<std::array<unsigned,4>>();op.index=row.at(11);
      op.context=lookup_.at({op.src,op.dst,op.expert%local_experts_});
      auto& c=contexts_.at(op.context);
      op.address=c.base+op.phase*stride_+(op.kind=="data"?uint64_t(op.index)*dispatch_bytes_:uint64_t(max_tokens_)*dispatch_bytes_);
      operations_.push_back(std::move(op));
    }
    transport_.ConfigureMessagePayload(32*1024*1024);
    transport_.ConfigureDcqcnReference(input.at("dcqcn_reference"));
    for(auto& c:contexts_)if(c.remote) {
      const auto i=c.id,stream=2*c.remote_index;
      transport_.Open(stream,rank_gpu_[c.src],rank_gpu_[c.dst],0,20000+stream,1,1,depth);
      transport_.Open(stream+1,rank_gpu_[c.dst],rank_gpu_[c.src],0,20001+stream,1,1,depth);
      transport_.SetReceiver(stream,[this,i](auto bytes){contexts_.at(i).requests.Feed(bytes,[this,i](const auto& f){Apply(i,f);});});
      transport_.SetReceiver(stream+1,[this,i](auto bytes){contexts_.at(i).replies.Feed(bytes,[this,i](const auto& f){Reply(i,f);});});
    }
    Require(remote*2==input.at("limits").at("streams"),"legacy stream count differs from full contexts");
    transport_.ValidateFaultBindings();transport_.changed=[this](){Notify();};Notify();
  }
  bool Finished() const{return finished_;}
  Json Report() const {
    Json times=Json::array();for(unsigned i=0;i<graph_.Size();++i)times.push_back(graph_.Get(i).time);
    Json ops=Json::array(),contexts=Json::array(),memory=Json::array();
    for(const auto& o:operations_)ops.push_back({o.context,o.sequence,o.ticket,o.end,o.publication,o.applied,o.acked,o.replied,o.completed,
      o.response?Json(o.old_value):Json(nullptr)});
    for(const auto& c:contexts_) {
      const auto q=c.queue.Snapshot();contexts.push_back({c.src,c.dst,c.local_expert,c.remote?int(c.remote_index):-1,
        q.reserved,q.prepared_prefix,q.published,q.consumed,q.completed,q.closed});
      Json counters=Json::array();
      for(unsigned phase=0;phase<2;++phase)counters.push_back(c.memory.Load32(rank_gpu_[c.dst],1,c.base+phase*stride_+uint64_t(max_tokens_)*dispatch_bytes_));
      memory.push_back({c.id,c.base,stride_,counters});
    }
    Json data=Json::array();
    for(unsigned i=0;i<operations_.size();++i) {
      const auto& o=operations_[i];if(o.kind!="data")continue;const auto& c=contexts_[o.context];
      data.push_back({i,Hex(c.memory.ReadBytes(rank_gpu_[o.dst],1,o.address,o.bytes))});
    }
    return Json{{"event_times_ns",times},{"operation_columns",{"context","sequence","begin_wqe","end_wqe","published_ns","applied_ns","request_acked_ns","response_ns","completed_ns","returned_value"}},
      {"operations",ops},{"contexts",contexts},{"counters",memory},{"final_data",data},
      {"memory_events",memory_events_},{"event_order",event_order_},{"publications",publications_},{"transport",transport_.Report()},
      {"finished",finished_},{"registered_contexts",contexts_.size()},
      {"local_operations_bypass_posting_queue",true},
      {"scope","complete indexed legacy logical execution with functional local visibility/expert copies; MRT1 not GIN/RoCE wire or GPU timing"},
      {"source_capture_leases_remaining",active_captures_},{"hardware_calibrated",false},{"cuda_executed",false}};
  }
 private:
  void Notify(){if(pending_ || finished_)return;pending_=true;ns3::Simulator::ScheduleNow([this](){pending_=false;Progress();});}
  void Fire(unsigned id){graph_.Fire(id,Now());event_order_.push_back({order_++,id,Now()});}
  void Progress() {
    while(!graph_.Empty()) {
      const auto i=graph_.Take();const auto& e=events_.at(i);
      if(e.kind=="issued") {Issue(e.operation);continue;}
      if(e.kind=="clean_next_buffer")Clean(e.rank,e.attributes.at("buffer_index"));
      if(e.kind=="expert_stage_done")Expert(e.rank);
      if(e.kind=="visible") {
        const auto& o=operations_.at(e.operation);const auto& c=contexts_.at(o.context);
        if(o.kind!="data")Require(c.memory.Load32(rank_gpu_[o.dst],1,o.address)==o.value,"legacy notification not present in actual local bytes");
      }
      Fire(i);
    }
    if(graph_.Complete() && retired_==operations_.size() && transport_.Drained()) {
      for(auto& c:contexts_) {Require(c.requests.Empty() && c.replies.Empty(),"legacy decoder not drained");c.queue.Close();}
      Require(active_captures_==0,"legacy source captures remain");transport_.Close();finished_=true;ns3::Simulator::Stop();
    }
  }
  void Issue(unsigned id) {
    auto& o=operations_.at(id);auto& c=contexts_.at(o.context);Require(!o.reserved,"duplicate legacy issue");
    if(!c.remote) {
      // The pinned self path is a local copy/store, not an IBGDA WQE. It must
      // not wait for a doorbell, consume queue credit or fabricate an ACK.
      o.sequence=c.operations.size();o.reserved=true;o.posted=true;c.operations.push_back(id);
      Fire(o.events[0]);Send(id);return;
    }
    const auto descriptor=o.kind=="data"?c.queue.ReservePut(id,o.index,{o.bytes}):c.queue.ReserveNotification(id);
    o.ticket=descriptor.begin;o.end=descriptor.end;o.sequence=c.operations.size();o.reserved=true;c.operations.push_back(id);Pump(c);
  }
  void Pump(Context& c) {
    if(!c.remote)return;
    for(const auto id:c.operations) {auto& o=operations_.at(id);if(o.posted)continue;
      for(auto t=o.ticket;t<o.end;++t)if(c.queue.CanPrepare(t))c.queue.Prepare(t);
      if(!c.queue.CanPost(id))continue;
      const auto p=c.queue.Post(id);o.posted=true;
      publications_.push_back({Now(),id,c.id,p.eligible,p.requested,p.previous,p.published});
      Fire(o.events[0]);
      while(const auto entry=c.queue.ConsumePublished())Send(entry->operation);
    }
  }
  std::vector<uint8_t> Capture(const Operation& o) {
    std::vector<uint8_t> bytes(o.bytes);
    if(o.phase==0)for(unsigned i=0;i<bytes.size();++i)bytes[i]=(o.src*17+o.token*13+o.expert*7+o.call*11+i)%251;
    else {const auto key=std::make_tuple(o.call,o.src,o.dst,o.expert,o.token);
      Require(expert_output_.count(key),"combine source lacks actual expert-stage input");bytes=expert_output_.at(key);}
    return bytes;
  }
  void Send(unsigned id) {
    auto& o=operations_.at(id);auto& c=contexts_.at(o.context);Require(!o.sent,"duplicate legacy send");
    MemoryFrame frame;frame.sequence=o.sequence;frame.key=1;frame.address=o.address;
    frame.verb=o.kind=="data"?MemoryFrame::Put:MemoryFrame::FetchAdd32;
    if(o.kind=="data") {o.captured=Capture(o);++active_captures_;frame.body=o.captured;}
    else frame.body=MemoryInteger(o.value,4);
    o.sent=true;if(c.remote)o.publication=Now();
    if(!c.remote) {Apply(c.id,frame);Retire(c);return;}
    transport_.SubmitPayload(2*c.remote_index,work_id_++,EncodeMemoryFrame(frame),[this,id](){auto& op=operations_.at(id);op.ack=true;op.acked=Now();Retire(contexts_.at(op.context));});
  }
  void Apply(unsigned context,const MemoryFrame& frame) {
    auto& c=contexts_.at(context);Require(frame.sequence<c.operations.size(),"unknown legacy received sequence");
    RemoteMemory::Result result{false,frame.verb==MemoryFrame::FetchAdd32,0};
    if(c.remote)result=c.memory.Apply(0,rank_gpu_[c.dst],frame);
    else if(result.atomic) {
      result.old_value=c.memory.Load32(rank_gpu_[c.dst],1,frame.address);
      c.memory.Store32(rank_gpu_[c.dst],1,frame.address,MemoryRead(frame.body.data(),4));
    } else Write(c,frame.address,frame.body);
    const auto id=c.operations.at(frame.sequence);auto& o=operations_.at(id);
    memory_events_.push_back({order_++,Now(),context,frame.sequence,frame.verb,frame.address,Hex(frame.body),result.duplicate,result.old_value});
    Require(!result.duplicate && o.applied<0,"legacy carrier replayed an application frame");
    // Target application does not give the source its return value. Only the
    // actual reverse-stream callback in Reply can populate source reply state.
    o.applied=Now();graph_.Release(o.events[1]);
    if(result.atomic && c.remote) {
      MemoryFrame reply;reply.verb=MemoryFrame::Reply32;reply.sequence=frame.sequence;reply.body=MemoryInteger(result.old_value,4);
      transport_.SubmitPayload(2*c.remote_index+1,work_id_++,EncodeMemoryFrame(reply),{});
    }
    Notify();
  }
  void Reply(unsigned context,const MemoryFrame& frame) {
    auto& c=contexts_.at(context);Require(frame.verb==MemoryFrame::Reply32 && frame.sequence<c.operations.size(),"invalid legacy atomic reply");
    auto& o=operations_.at(c.operations.at(frame.sequence));Require(o.kind!="data" && !o.response,"duplicate/unexpected legacy reply");
    o.old_value=MemoryRead(frame.body.data(),4);o.response=true;o.replied=Now();Retire(c);
  }
  void Retire(Context& c) {
    while(c.retired<c.operations.size()) {auto& o=operations_.at(c.operations[c.retired]);
      if(c.remote?(!o.ack || (o.kind!="data" && !o.response)):o.applied<0)break;
      Require(!o.retired,"duplicate legacy retirement");o.retired=true;o.completed=Now();++c.retired;++retired_;
      if(!o.captured.empty()){--active_captures_;std::vector<uint8_t>().swap(o.captured);}
      if(c.remote)c.queue.CompleteThrough(o.end);graph_.Release(o.events[3]);
    }
    Pump(c);Notify();
  }
  void Write(Context& c,uint64_t address,const std::vector<uint8_t>& bytes) {
    c.memory.AcquireWrite(0,{rank_gpu_[c.dst],1,address,bytes.size()});
    c.memory.CommitWrite(0,bytes);c.memory.ReleaseWrite(0);
  }
  void Clean(unsigned rank,unsigned phase) {
    for(auto& c:contexts_)if(c.dst==rank) {
      // No read leases refer to target storage: expert outputs were captured at
      // expert_stage_done. Request replay state remains independent of clean.
      const auto start=c.base+phase*stride_;
      for(unsigned offset=0;offset<stride_;offset+=32768) {
        const auto size=std::min<uint64_t>(32768,stride_-offset);
        // Avoid materializing clean pages. Only existing resident pages carry data.
        const auto& w=c.memory.Windows().at(0);
        bool resident=false;for(const auto& [page,bytes]:w.pages) { (void)bytes;
          if(page*RemoteMemory::PageBytes<phase*stride_+offset+size && phase*stride_+offset<(page+1)*RemoteMemory::PageBytes)resident=true;}
        if(resident)Write(c,start+offset,std::vector<uint8_t>(size,0));
      }
    }
  }
  void Expert(unsigned rank) {
    // Dispatch call is determined by actual expert-stage occurrence per rank.
    const auto call=expert_calls_[rank]++;
    for(const auto& o:operations_)if(o.phase==0 && o.kind=="data" && o.call==call && o.dst==rank) {
      // BF16 dispatch begins with a 16-byte source-index/reserved header;
      // the functional expert consumes the tensor bytes after that header.
      const auto& c=contexts_[o.context];auto bytes=c.memory.ReadBytes(rank_gpu_[rank],1,o.address+16,2*hidden_);
      expert_output_[{call,rank,o.src,o.expert,o.token}]=std::move(bytes);
    }
  }
  Fabric& fabric_;Json input_;PersistentStreams transport_;LegacyEventGraph graph_;
  unsigned ranks_,experts_,local_experts_,hidden_,dispatch_bytes_,max_tokens_;uint64_t stride_=0,work_id_=0,active_captures_=0;
  std::vector<unsigned> rank_gpu_;std::vector<Event> events_;std::vector<Operation> operations_;
  std::deque<Context> contexts_;std::map<Key,unsigned> lookup_;
  std::map<std::tuple<unsigned,unsigned,unsigned,unsigned,unsigned>,std::vector<uint8_t>> expert_output_;
  std::map<unsigned,unsigned> expert_calls_;unsigned retired_=0;bool pending_=false,finished_=false;
  uint64_t order_=0;Json memory_events_=Json::array(),event_order_=Json::array(),publications_=Json::array();
};
int Libraries(dl_phdr_info* info,std::size_t,void* data){if(info->dlpi_name && info->dlpi_name[0])static_cast<Json*>(data)->push_back(info->dlpi_name);return 0;}
}
int main(int argc,char** argv) {
  try {
    Require(argc==5,"usage: legacy-deepep TOPOLOGY PROTOCOL EXECUTION NEW_RESULT");
    std::ifstream prior(argv[4]);Require(!prior.good(),"legacy result already exists");
    const auto topology=ReadJson(argv[1]),protocol=ReadJson(argv[2]),input=ReadJson(argv[3]);
    ValidateLegacyExecution(input,topology,protocol);
    ns3::Time::SetResolution(ns3::Time::NS);ns3::RngSeedManager::SetSeed(protocol.at("seed"));
    Fabric fabric(topology,protocol);Executor executor(fabric,input);
    ns3::Simulator::Stop(ns3::NanoSeconds(fabric.MaxSimNs()));std::string error;
    try{ns3::Simulator::Run();}catch(const std::exception& e){error=e.what();}
    auto result=fabric.Report();result["legacy"]=executor.Report();result["loaded_libraries"]=Json::array();
    dl_iterate_phdr(Libraries,&result["loaded_libraries"]);
    result["passed"]=error.empty() && executor.Finished() && fabric.Drained();
    if(!error.empty())result["execution_error"]=error;
    std::ofstream output(argv[4]);output<<result.dump()<<'\n';output.close();Require(bool(output),"cannot write legacy result");
    const auto passed=result.at("passed").get<bool>();ns3::Simulator::Destroy();Require(passed,"legacy execution incomplete; inspect retained result");return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';ns3::Simulator::Destroy();return 1;}
}
