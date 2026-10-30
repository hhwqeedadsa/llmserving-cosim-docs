#include "astra_adapter.hh"
#include "control_channel.hh"
#include "host_timing.hh"
#include "phase_gates.hh"
#include "execution_events.hh"
#include "source_ack_watchdog.hh"
#include "inflight_helpers.hh"
#include "astra-sim/system/Sys.hh"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iomanip>
#include <memory>
#include <link.h>

using namespace moe_rdma;
namespace moe_rdma { void InitializeLogging(); void ShutdownLogging(); }
namespace fs=std::filesystem;
class Service {
 public:
  Service(Fabric& fabric,ServiceTransport& transport,ControlChannel& control,int ranks,const std::string& bootstrap,const std::string& system,HostTiming& timing,bool phase_completion,uint64_t event_limit,const SourceAckWatchdogOptions& watchdog,const Json& helpers):
    fabric_(fabric),transport_(transport),control_(control),events_(control,event_limit),watchdog_(control,events_,watchdog),
    compute_(ranks,helpers.is_null()?1:helpers.at("configuration").at("max_compute_tasks").get<uint64_t>()),helpers_(transport,compute_,control,events_,helpers),
    messages_(fabric,transport),owners_(ranks),boot_done_(ranks,false),timing_(timing),phase_completion_(phase_completion),watchdog_enabled_(watchdog.enabled) {
    if(helpers_.Enabled()) {
      watchdog_.on_timeout=[this](const Json& notice){helpers_.Decide(notice);};
      helpers_.changed=[this](){TryFinish();};
    }
    if(events_.Enabled())messages_.SetObserver([this](const std::string& kind,const std::string& graph,uint64_t message,int rank){
      events_.Emit({{"kind",kind},{"graph_id",graph},{"message",message},{"rank",rank}});
      watchdog_.Observe(kind,graph,message,rank);
      if(helpers_.Enabled() && kind=="message_recv_complete")helpers_.MessageComplete(message);
    });
    for(int r=0;r<ranks;++r)
      adapters_.push_back(std::make_unique<Adapter>(r,messages_,[this,r](){Finished(r);}));
    for(int r=0;r<ranks;++r) {
      systems_.push_back(std::make_unique<AstraSim::Sys>(r,bootstrap,"empty",system,
        std::vector<AstraSim::AstraMemoryAPI*>{},adapters_[r].get(),
        std::vector<int>{ranks},std::vector<int>{1},1.0,1.0,false));
      systems_.back()->workload->bind_graph("_bootstrap");
    }
    ns3::Simulator::ScheduleNow([this](){
      for(auto& sys:systems_)sys->workload->activate_graph();
    });
  }
  void Finished(int rank) {
    if(owners_.at(rank).empty()) {
      Require(!boot_done_[rank],"rank completed twice without a graph");boot_done_[rank]=true;
    } else {
      Require(waves_.at(owners_[rank]).finished.insert(rank).second,"rank completed graph twice");
    }
    if(!pump_pending_) {
      pump_pending_=true;ns3::Simulator::ScheduleNow([this](){pump_pending_=false;Pump();});
    }
  }
  void Pump() {
    if(!std::all_of(boot_done_.begin(),boot_done_.end(),[](bool x){return x;}))return;
    Json completed=Json::array();
    for(auto it=waves_.begin();it!=waves_.end();) {
      auto& w=it->second;
      if(w.finished.size()!=w.ranks.size()) {++it;continue;}
      Require(messages_.Complete(w.messages),"graph finished before matched message callbacks");
      for(int rank:w.ranks) {
        Require(systems_[rank]->workload->can_load_graph(),"rank finished with live ASTRA resources");
        owners_[rank].clear();
      }
      completed.push_back({{"graph_id",it->first},{"finished_ns",Now()}});
      history_.push_back({{"graph_id",it->first},{"start_ns",w.start},{"finished_ns",Now()},{"rank_ids",w.ranks}});
      events_.Emit({{"kind","graph_retired"},{"graph_id",it->first}});
      it=waves_.erase(it);
    }
    // Partial rank completions do not make an EP group schedulable. Suppress
    // empty control round trips, preserving the initial decision and all
    // actual group completions in this finite, no-new-arrivals service.
    if(completed.empty() && decision_>0)return;
    Json available=Json::array();
    for(unsigned r=0;r<owners_.size();++r)if(owners_[r].empty())available.push_back(r);
    Json request={{"type","DECIDE"},{"decision_id",decision_},{"now_ns",Now()},
                  {"completed",completed},{"available_rank_ids",available}};
    std::string line;
    {
      auto exchange=timing_.Span("control_exchange");
      control_.Send(request.dump());
      auto wait=timing_.Span("frontend_wait");
      Require(bool(std::getline(std::cin,line)) && line.size()<=1048576,"frontend EOF or oversized commit");
    }
    Json commit;
    {auto phase=timing_.Span("commit_parse");commit=Json::parse(line);}
    Require(commit.at("type")=="COMMIT" && commit.at("decision_id")==decision_ && commit.at("now_ns")==Now(),
            "frontend commit identity/time mismatch");
    ++decision_;Require(decision_<=4096,"service decision budget exceeded");
    const auto& submissions=commit.at("submissions");
    if(commit.at("service_finished")==true) {
      Require(submissions.empty() && waves_.empty(),"premature service termination");
      finishing_=true;TryFinish();return;
    }
    Require(!submissions.empty() || !waves_.empty(),"frontend and network made no progress");
    std::vector<int> activate;
    {
    auto phase=timing_.Span("graph_load");
    for(const auto& submission:submissions) {
      const auto directory=fs::absolute(submission.get<std::string>());
      auto descriptor=ReadJson((directory/"wave.json").string());
      auto id=descriptor.at("graph_id").get<std::string>();
      Require(!id.empty() && used_.insert(id).second,"reused graph identity");
      auto ranks=descriptor.at("rank_ids").get<std::vector<int>>();
      Require(!ranks.empty() && std::set<int>(ranks.begin(),ranks.end()).size()==ranks.size(),"invalid graph ranks");
      Require(descriptor.at("schema_version")== (phase_completion_?"moe-scaleout-wave/2":"moe-scaleout-wave/1"),"wave completion schema differs from service mode");
      if(phase_completion_) {
        const auto& phase=descriptor.at("phase_completion");
        Require(phase.at("mode")=="ep_transport_flush/1" && phase.at("gates").size()==2*descriptor.at("routes").size(),"invalid phase completion declaration");
        std::set<std::string> names;
        for(const auto& gate:phase.at("gates")) {
          const auto name=gate.at("id").get<std::string>();
          Require(names.insert(name).second,"duplicate phase gate");
          std::map<int,uint64_t> members;
          for(const auto& m:gate.at("members"))Require(members.emplace(m.at("rank").get<int>(),m.at("node").get<uint64_t>()).second,"duplicate phase rank");
          Require(members.size()==ranks.size(),"incomplete phase group");
          for(int rank:ranks) {
            Require(members.count(rank),"foreign/missing phase rank");
            bool found=false;
            for(const auto& task:descriptor.at("rank_nodes").at(std::to_string(rank)))
              if(task.at("id")==members.at(rank)) {
                Require(task.at("kind")=="GATE" && task.at("duration_ns")==0,"phase member is not a zero-compute gate");found=true;
              }
            Require(found,"missing phase node");
          }
          phase_gates_.Add({id,name},members);
        }
      } else Require(!descriptor.contains("phase_completion"),"undeclared phase gates");
      std::vector<std::unique_ptr<Chakra::ETFeeder>> feeders;
      for(int rank:ranks) {
        Require(rank>=0 && rank<int(owners_.size()) && owners_[rank].empty() &&
                systems_[rank]->workload->can_load_graph(),"rank not available for graph");
        feeders.push_back(std::make_unique<Chakra::ETFeeder>((directory/("layer."+std::to_string(rank)+".et")).string()));
      }
      for(const auto& m:descriptor.at("messages")) {
        const auto src=m.at("src").get<int>(),dst=m.at("dst").get<int>();
        Require(std::find(ranks.begin(),ranks.end(),src)!=ranks.end() &&
                std::find(ranks.begin(),ranks.end(),dst)!=ranks.end(),"message leaves its EP group");
      }
      messages_.Register(descriptor.at("messages"),id);
      if(helpers_.Enabled())helpers_.Register(descriptor);
      else Require(!descriptor.contains("helper_candidates"),"undeclared helper graph candidates");
      // ETFeeder owns execution nodes; retaining rank_nodes/routes here would
      // duplicate the entire full-model JSON for every concurrent EP group.
      std::vector<uint64_t> message_ids;
      for(const auto& message:descriptor.at("messages"))message_ids.push_back(message.at("id"));
      waves_.emplace(id,Wave{std::move(message_ids),ranks,{},Now()});
      for(unsigned i=0;i<ranks.size();++i) {
        const int rank=ranks[i];owners_[rank]=id;auto* workload=systems_[rank]->workload;
        workload->hw_resource->configure_p2p_protocol_resources();
        if(events_.Enabled())workload->set_node_observer([this,id,rank](uint64_t node,bool completed){
          events_.Emit({{"kind",completed?"node_complete":"node_issue"},{"graph_id",id},{"rank",rank},{"node",node}});
          if(completed && helpers_.Enabled())helpers_.NodeComplete(id,rank,node);
        });
        // The pinned managed Workload skips ordinary zero-runtime COMP nodes
        // without retiring active_nodes_. Encode their declared zero cost as
        // its supported zero-time event path, preserving dependencies/logging.
        std::unordered_set<uint64_t> zero_nodes;
        std::map<uint64_t,int64_t> compute_costs;
        for(const auto& task:descriptor.at("rank_nodes").at(std::to_string(rank)))
          if((task.at("kind")=="COMP" || task.at("kind")=="GATE") && (helpers_.Enabled() || task.at("duration_ns")==0)) {
            const auto node=task.at("id").get<uint64_t>();
            Require((task.at("kind")=="GATE")==phase_gates_.Contains(id,rank,node),"undeclared/incorrect phase node");
            zero_nodes.insert(node);
            if(task.at("kind")=="COMP")compute_costs[node]=task.at("duration_ns").get<int64_t>();
          }
        workload->set_external_events(std::move(zero_nodes),[this,id,rank,compute_costs=std::move(compute_costs)](uint64_t node,std::function<void()> done){
          if(!phase_gates_.Contains(id,rank,node)) {
            if(!helpers_.Enabled()){done();return;}
            compute_.Submit(id+".r"+std::to_string(rank)+".n"+std::to_string(node),rank,compute_costs.at(node),
              {{"kind","original"},{"graph_id",id},{"node",node}}, {},std::move(done));return;
          }
          auto ready=phase_gates_.Arrive(id,rank,node,Now(),std::move(done));
          for(auto& callback:ready)callback(); // ASTRA's external completion schedules its own zero-time event.
        });
        workload->load_graph(std::move(feeders[i]),id);activate.push_back(rank);
      }
    }
    }
    // All EP groups are installed before any graph injects work.
    {auto phase=timing_.Span("graph_activate");
      for(int rank:activate)systems_[rank]->workload->activate_graph();}
    if(timing_.Enabled())timing_.Snapshot("message_resources",messages_.HostCounters());
  }
  Json Report() {
    Json report={{"service_finished",finished_},{"decisions",decision_},{"waves",history_},
      {"active_graphs",waves_.size()},{"message_callbacks",messages_.Report()}};
    if(events_.Enabled())report["execution_events"]=events_.Report();
    if(watchdog_enabled_)report["source_ack_watchdog"]=watchdog_.Report();
    if(helpers_.Enabled()){report["inflight_helpers"]=helpers_.Report();report["shared_compute"]=compute_.Report();}
    if(phase_completion_) {
      Json gates=Json::array();
      for(const auto& [key,group]:phase_gates_.Groups()) {
        Json members=Json::array();
        for(auto [rank,node]:group.members)members.push_back({{"rank",rank},{"node",node},
            {"arrived_ns",group.arrivals.count(rank)?Json(group.arrivals.at(rank)):Json(nullptr)}});
        gates.push_back({{"graph_id",key.first},{"id",key.second},{"members",members},
                        {"released_ns",group.released<0?Json(nullptr):Json(group.released)}});
      }
      report["phase_completion"]={{"mode","ep_transport_flush/1"},{"gates",gates}};
    }
    return report;
  }
  Json HostCounters() const {return messages_.HostCounters();}
 private:
  void TryFinish() {
    if(!finishing_ || finish_requested_ || (helpers_.Enabled() && !helpers_.Complete()))return;
    Require(!helpers_.Enabled() || compute_.Empty(),"service finish with occupied shared compute");finish_requested_=true;
    transport_.Finish([this](){events_.Emit({{"kind","service_drained"}});finished_=true;ns3::Simulator::Stop();});
  }
  static int64_t Now(){return ns3::Simulator::Now().GetNanoSeconds();}
  struct Wave { std::vector<uint64_t> messages;std::vector<int> ranks;std::set<int> finished;int64_t start; };
  Fabric& fabric_;ServiceTransport& transport_;ControlChannel& control_;ExecutionEvents events_;SourceAckWatchdog watchdog_;
  SharedCompute compute_;InflightHelpers helpers_;Messages messages_;
  std::vector<std::string> owners_;
  std::vector<bool> boot_done_;
  std::vector<std::unique_ptr<Adapter>> adapters_;
  std::vector<std::unique_ptr<AstraSim::Sys>> systems_;
  std::map<std::string,Wave> waves_;
  std::set<std::string> used_;
  Json history_=Json::array();
  unsigned decision_=0;bool pump_pending_=false,finished_=false;
  HostTiming& timing_;
  bool phase_completion_,watchdog_enabled_;
  bool finishing_=false,finish_requested_=false;
  PhaseGates phase_gates_;
};
static int Libraries(dl_phdr_info* info,std::size_t,void* data) {
  if(info->dlpi_name && info->dlpi_name[0])static_cast<Json*>(data)->push_back(info->dlpi_name);return 0;
}
static void Execute(char** argv,HostTiming& timing,const Json& transport_config,ControlChannel& control,bool phase_completion,uint64_t event_limit,const SourceAckWatchdogOptions& watchdog,const Json& helpers) {
    auto total=timing.Span("native_total");
    Require(!fs::exists(argv[5]),"result already exists");
    ns3::Time::SetResolution(ns3::Time::NS);Json topology,config;
    {auto phase=timing.Span("input_read");topology=ReadJson(argv[1]);config=ReadJson(argv[2]);}
    ns3::RngSeedManager::SetSeed(config.at("seed"));
    {auto phase=timing.Span("logging_init");InitializeLogging();}
    std::unique_ptr<Fabric> fabric_owner;
    {auto phase=timing.Span("fabric_init");fabric_owner=std::make_unique<Fabric>(topology,config);}
    auto& fabric=*fabric_owner;
    ServiceTransport transport(fabric,transport_config);
    int ranks=0;for(const auto& node:topology.at("nodes"))if(node.at("kind")=="gpu")++ranks;
    if(!helpers.is_null()) {
      unsigned declared=0;for(const auto& group:helpers.at("groups"))declared+=group.size();
      Require(declared==unsigned(ranks),"helper replica groups do not partition physical simulated GPUs");
    }
    std::unique_ptr<Service> service_owner;
    {auto phase=timing.Span("astra_init");service_owner=std::make_unique<Service>(fabric,transport,control,ranks,argv[4],argv[3],timing,phase_completion,event_limit,watchdog,helpers);}
    auto& service=*service_owner;
    ns3::Simulator::Stop(ns3::NanoSeconds(fabric.MaxSimNs()));
    {auto phase=timing.Span("simulator_run");ns3::Simulator::Run();}
    if(timing.Enabled()) {
      timing.Snapshot("message_resources_final",service.HostCounters());
      timing.Snapshot("ns3_events",{{"processed_events",ns3::Simulator::GetEventCount()}});
    }
    Json report;
    {auto phase=timing.Span("report_build");report=fabric.Report();report["service"]=service.Report();}
    if(transport.Persistent()) {
      report["service_transport"]=transport.Report();
      const auto& recovery=report["service_transport"]["native"]["recovery"];
      if(recovery.at("failed")==true)report["failure"]={{"reason","recovery_retry_exhausted"},
        {"stream",recovery.at("failure").at("stream")},{"time_ns",recovery.at("failure").at("time_ns")},
        {"scope","persistent source retries exhausted; whole ASTRA service stopped; no completion synthesized"}};
    }
    report["loaded_libraries"]=Json::array();
    dl_iterate_phdr(Libraries,&report["loaded_libraries"]);
    const bool passed=report["service"]["service_finished"]==true && fabric.Drained();report["passed"]=passed;
    {
    auto phase=timing.Span("report_write");std::ofstream out(argv[5]);
    if(config.contains("limits"))out<<report;
    else out<<std::setw(2)<<report;
    out<<'\n';out.close();Require(bool(out),"cannot write result");
    }
    {auto phase=timing.Span("simulator_cleanup");ns3::Simulator::Destroy();ShutdownLogging();}
    Require(passed,"service incomplete; inspect result");
}
int main(int argc,char** argv) {
  try {
    ControlChannel control;
    Require(argc>=6 && argc<=17,"usage: moe-rdma-service TOPOLOGY RDMA SYSTEM BOOTSTRAP_PREFIX NEW_RESULT [NEW_HOST_TIMING.jsonl] [--phase-completion ep_transport_flush/1] [--transport CONFIG.json] [--execution-events MAX_EVENTS] [--source-ack-watchdog CONFIG.json] [--inflight-helpers CONFIG.json]");
    int next=6;std::string host;Json transport,watchdog_json,helpers;bool phase_completion=false,has_transport=false;uint64_t event_limit=0;
    if(next<argc && std::string(argv[next]).rfind("--",0)!=0)host=argv[next++];
    while(next<argc) {
      Require(next+1<argc,"missing service option value");const std::string option=argv[next++],value=argv[next++];
      if(option=="--transport") {Require(!has_transport,"repeated transport option");has_transport=true;transport=ReadJson(value);}
      else if(option=="--phase-completion") {Require(!phase_completion && value=="ep_transport_flush/1","invalid phase completion option");phase_completion=true;}
      else if(option=="--execution-events") {
        Require(event_limit==0 && !value.empty() && value.find_first_not_of("0123456789")==std::string::npos,"invalid execution event option");
        event_limit=std::stoull(value);Require(event_limit>0 && event_limit<=262144,"invalid execution event limit");
      }
      else if(option=="--source-ack-watchdog") {
        Require(watchdog_json.is_null(),"repeated source ACK watchdog option");watchdog_json=ReadJson(value);
        Require(!watchdog_json.is_null(),"null source ACK watchdog option");
      }
      else if(option=="--inflight-helpers") {
        Require(helpers.is_null(),"repeated inflight helper option");helpers=ReadJson(value);
        Require(helpers.is_object() && helpers.size()==7,"invalid inflight helper deployment fields");
        const auto& config=helpers.at("configuration");
        Require(config.is_object() && config.size()==6 && config.at("schema_version")=="moe-inflight-helpers/1" &&
          (config.at("policy")=="observe_only/1" || config.at("policy")=="first_missing_bundle/1"),"invalid inflight helper fields/policy");
        for(auto [key,maximum]:std::vector<std::pair<const char*,uint64_t>>{{"max_compute_tasks",131072},{"max_events",65536},{"max_attempts",128},{"max_decisions",128}})
          Require(config.at(key).is_number_integer() && config.at(key)>=1 && config.at(key)<=maximum,"invalid inflight helper budget");
        Require(config.at("max_events").get<uint64_t>()>=12*config.at("max_attempts").get<uint64_t>(),"helper lifecycle budget insufficient");
        Require(helpers.at("groups").is_array() && helpers.at("groups").size()>=2 && helpers.at("groups").size()<=256 &&
          helpers.at("experts").is_number_integer() && helpers.at("experts")>=2 && helpers.at("experts")<=256,
          "invalid helper declared replica groups");
        std::set<unsigned> all_ranks;
        for(const auto& group:helpers.at("groups")) {
          Require(group.is_array() && group.size()>=2 && group.size()<=256 &&
            helpers.at("experts").get<unsigned>()%group.size()==0,"invalid helper complete EP group");
          int previous=-1;
          for(const auto& rank:group) {
            Require(rank.is_number_integer() && rank>=0 && rank<=65535 && rank.get<int>()>previous &&
              all_ranks.insert(rank.get<unsigned>()).second,"overlapping/unsorted helper replica ranks");previous=rank.get<int>();
          }
        }
        Require(!all_ranks.empty() && *all_ranks.rbegin()+1==all_ranks.size(),"helper ranks must form a contiguous physical partition");
        const auto& placement=helpers.at("expert_to_group_rank");
        if(!placement.is_null()) {
          Require(placement.is_array() && !placement.empty() && placement.size()<=128,"invalid helper expert placement layers");
          const auto size=helpers.at("groups").at(0).size();
          for(const auto& group:helpers.at("groups"))Require(group.size()==size,"explicit helper placement needs equal EP sizes");
          for(const auto& layer:placement) {
            Require(layer.is_array() && layer.size()==helpers.at("experts").get<unsigned>(),"invalid helper placement experts");
            std::vector<unsigned> counts(size,0);
            for(const auto& index:layer) {
              Require(index.is_number_integer() && index>=0 && index<size,"invalid helper placement index");++counts.at(index.get<unsigned>());
            }
            for(auto count:counts)Require(count==layer.size()/size,"unbalanced helper declared expert placement");
          }
        }
        for(const char* key:{"expert_ns_per_contribution","local_reduce_ns_per_extra"})
          Require(helpers.at(key).is_number_integer() && helpers.at(key)>=0 && helpers.at(key)<=1000000000,"invalid helper declared compute");
        Require(helpers.at("payload_sizes").is_object() && helpers.at("payload_sizes").size()==2,"invalid helper payload declaration");
        for(const char* key:{"dispatch","combine"})
          Require(helpers.at("payload_sizes").at(key).is_number_integer() && helpers.at("payload_sizes").at(key)>=1 &&
            helpers.at("payload_sizes").at(key)<=1048576,"invalid helper payload bytes");
      }
      else Require(false,"unexpected service option");
    }
    Require(next==argc,"unexpected service arguments");
    const auto watchdog=SourceAckWatchdogOptions::Parse(watchdog_json);
    Require(!watchdog.enabled || (event_limit>0 && has_transport),"source ACK watchdog requires execution events and persistent transport");
    Require(helpers.is_null() || (watchdog.enabled && phase_completion),"inflight helpers require watchdog and phase completion");
    HostTiming timing(host);Execute(argv,timing,transport,control,phase_completion,event_limit,watchdog,helpers);timing.Complete();return 0;
  } catch(const std::exception& error) {
    std::cerr<<error.what()<<'\n';ns3::Simulator::Destroy();ShutdownLogging();return 1;
  }
}
