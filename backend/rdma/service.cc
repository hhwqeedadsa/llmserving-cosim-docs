#include "astra_adapter.hh"
#include "host_timing.hh"
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
  Service(Fabric& fabric,int ranks,const std::string& bootstrap,const std::string& system,HostTiming& timing):
    fabric_(fabric),messages_(fabric),owners_(ranks),boot_done_(ranks,false),timing_(timing) {
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
      std::cout<<"MOE_RDMA_EVENT "<<request.dump()<<std::endl;
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
      fabric_.FinishWhenDrained([this](){finished_=true;ns3::Simulator::Stop();});return;
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
      messages_.Register(descriptor.at("messages"));
      // ETFeeder owns execution nodes; retaining rank_nodes/routes here would
      // duplicate the entire full-model JSON for every concurrent EP group.
      std::vector<uint64_t> message_ids;
      for(const auto& message:descriptor.at("messages"))message_ids.push_back(message.at("id"));
      waves_.emplace(id,Wave{std::move(message_ids),ranks,{},Now()});
      for(unsigned i=0;i<ranks.size();++i) {
        const int rank=ranks[i];owners_[rank]=id;auto* workload=systems_[rank]->workload;
        workload->hw_resource->configure_p2p_protocol_resources();
        // The pinned managed Workload skips ordinary zero-runtime COMP nodes
        // without retiring active_nodes_. Encode their declared zero cost as
        // its supported zero-time event path, preserving dependencies/logging.
        std::unordered_set<uint64_t> zero_nodes;
        for(const auto& task:descriptor.at("rank_nodes").at(std::to_string(rank)))
          if(task.at("kind")=="COMP" && task.at("duration_ns")==0)
            zero_nodes.insert(task.at("id").get<uint64_t>());
        workload->set_external_events(std::move(zero_nodes),[](uint64_t,std::function<void()> done){done();});
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
    return {{"service_finished",finished_},{"decisions",decision_},{"waves",history_},
      {"active_graphs",waves_.size()},{"message_callbacks",messages_.Report()}};
  }
  Json HostCounters() const {return messages_.HostCounters();}
 private:
  static int64_t Now(){return ns3::Simulator::Now().GetNanoSeconds();}
  struct Wave { std::vector<uint64_t> messages;std::vector<int> ranks;std::set<int> finished;int64_t start; };
  Fabric& fabric_;Messages messages_;
  std::vector<std::string> owners_;
  std::vector<bool> boot_done_;
  std::vector<std::unique_ptr<Adapter>> adapters_;
  std::vector<std::unique_ptr<AstraSim::Sys>> systems_;
  std::map<std::string,Wave> waves_;
  std::set<std::string> used_;
  Json history_=Json::array();
  unsigned decision_=0;bool pump_pending_=false,finished_=false;
  HostTiming& timing_;
};
static int Libraries(dl_phdr_info* info,std::size_t,void* data) {
  if(info->dlpi_name && info->dlpi_name[0])static_cast<Json*>(data)->push_back(info->dlpi_name);return 0;
}
static void Execute(char** argv,HostTiming& timing) {
    auto total=timing.Span("native_total");
    Require(!fs::exists(argv[5]),"result already exists");
    ns3::Time::SetResolution(ns3::Time::NS);Json topology,config;
    {auto phase=timing.Span("input_read");topology=ReadJson(argv[1]);config=ReadJson(argv[2]);}
    ns3::RngSeedManager::SetSeed(config.at("seed"));
    {auto phase=timing.Span("logging_init");InitializeLogging();}
    std::unique_ptr<Fabric> fabric_owner;
    {auto phase=timing.Span("fabric_init");fabric_owner=std::make_unique<Fabric>(topology,config);}
    auto& fabric=*fabric_owner;
    int ranks=0;for(const auto& node:topology.at("nodes"))if(node.at("kind")=="gpu")++ranks;
    std::unique_ptr<Service> service_owner;
    {auto phase=timing.Span("astra_init");service_owner=std::make_unique<Service>(fabric,ranks,argv[4],argv[3],timing);}
    auto& service=*service_owner;
    ns3::Simulator::Stop(ns3::NanoSeconds(fabric.MaxSimNs()));
    {auto phase=timing.Span("simulator_run");ns3::Simulator::Run();}
    if(timing.Enabled()) {
      timing.Snapshot("message_resources_final",service.HostCounters());
      timing.Snapshot("ns3_events",{{"processed_events",ns3::Simulator::GetEventCount()}});
    }
    Json report;
    {auto phase=timing.Span("report_build");report=fabric.Report();report["service"]=service.Report();}
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
    Require(argc==6 || argc==7,"usage: moe-rdma-service TOPOLOGY RDMA SYSTEM BOOTSTRAP_PREFIX NEW_RESULT [NEW_HOST_TIMING.jsonl]");
    HostTiming timing(argc==7?argv[6]:"");Execute(argv,timing);timing.Complete();return 0;
  } catch(const std::exception& error) {
    std::cerr<<error.what()<<'\n';ns3::Simulator::Destroy();ShutdownLogging();return 1;
  }
}
