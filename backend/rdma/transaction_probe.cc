#include "remote_transactions.hh"
#include "transaction_session_validation.hh"
#include <fstream>
#include <iostream>
#include <link.h>
#include <deque>
using namespace moe_rdma;
static int Libraries(dl_phdr_info* info,std::size_t,void* data) {
  if(info->dlpi_name && info->dlpi_name[0])static_cast<Json*>(data)->push_back(info->dlpi_name);
  return 0;
}
static void Keys(const Json& value,const std::vector<std::string>& keys) {
  Require(value.is_object() && value.size()==keys.size(),"wrong transaction node fields");
  for(const auto& key:keys)Require(value.contains(key),"missing transaction node field");
}
static uint64_t Integer(const Json& value,uint64_t maximum) {
  Require(value.is_number_integer() && (value.is_number_unsigned() || value.get<int64_t>()>=0),"invalid transaction node integer");
  const auto result=value.get<uint64_t>();Require(result<=maximum,"transaction node integer out of bounds");return result;
}
int main(int argc,char** argv) {
  try {
    Require(argc==5,"usage: moe-rdma-transaction-probe TOPOLOGY.json RDMA.json WORK.json NEW_RESULT.json");
    std::ifstream exists(argv[4]);Require(!exists.good(),"transaction output already exists");
    ns3::Time::SetResolution(ns3::Time::NS);
    const auto config=ReadJson(argv[2]),input=ReadJson(argv[3]);
    ValidateTransactionTransport(input,config.contains("limits")?config.at("limits").at("max_sim_ns").get<int64_t>():5000000);
    const auto execution_limits=ReadTransactionLimits(input);
    const bool sparse_mode=input.at("schema_version")=="moe-rdma-transactions/5";
    const bool explicit_submission=input.contains("submission_model");
    Require(!explicit_submission || sparse_mode,"submission model requires v5");
    const bool local_memory_mode=sparse_mode || input.at("schema_version")=="moe-rdma-transactions/4";
    const bool source_mode=local_memory_mode || input.at("schema_version")=="moe-rdma-transactions/3";
    const bool local_mode=source_mode || input.at("schema_version")=="moe-rdma-transactions/2";
    if(local_mode) {
      std::vector<std::string> keys={"schema_version","connections","windows","operations","local_model"};
      if(source_mode)keys.push_back("source_model");
      if(local_memory_mode)keys.push_back("local_memory_model");
      if(explicit_submission)keys.push_back("submission_model");
      if(input.contains("execution_limits"))keys.push_back("execution_limits");
      if(input.contains("dcqcn_reference"))keys.push_back("dcqcn_reference");
      if(input.contains("transport_recovery"))keys.push_back("transport_recovery");
      if(input.contains("buffer_model"))keys.push_back("buffer_model");
      for(const auto* name:{"registration_model","registrations","application_window_model","application_windows"})if(input.contains(name))keys.push_back(name);
      Keys(input,keys);
      Require(input.at("local_model")==Json{{"mode","serial_per_gpu/1"},{"duration_source","declared_ns"}},"unsupported transaction local model");
    }
    ns3::RngSeedManager::SetSeed(config.at("seed"));
    const auto topology=ReadJson(argv[1]);
    if(sparse_mode || input.contains("registration_model") || input.contains("registrations") || input.contains("application_window_model") || input.contains("application_windows"))
      ValidateSessionProgram(input,topology,config.contains("limits")?config.at("limits").at("max_sim_ns").get<int64_t>():5000000);
    Fabric fabric(topology,config);RemoteTransactions transactions(fabric,input);
    std::set<unsigned> gpus;
    for(const auto& n:topology.at("nodes"))if(n.at("kind")=="gpu")gpus.insert(n.at("id").get<unsigned>());
    const auto& operations=input.at("operations");
    Require(operations.is_array() && !operations.empty() && operations.size()<=(local_mode?execution_limits.Nodes():32u),"transaction count budget exceeded");
    std::map<uint64_t,std::size_t> index;
    std::vector<bool> local(operations.size(),false);
    unsigned remote_count=0,local_count=0;
    for(std::size_t i=0;i<operations.size();++i) {
      const auto& op=operations[i];
      if(local_mode) {
        Integer(op.at("id"),65535);
        const auto kind=op.at("kind").get<std::string>();
        Require(kind=="local" || kind=="remote","unsupported transaction node kind");local[i]=kind=="local";
        std::vector<std::string> keys={"id","kind","after"};
        if(local[i]) {
          keys.insert(keys.end(),{"rank","duration_ns"});
          const auto rank=Integer(op.at("rank"),31);
          Require(gpus.count(static_cast<unsigned>(rank)),"local transaction rank is not a GPU");
          Integer(op.at("duration_ns"),fabric.MaxSimNs());
          if(op.contains("copy")) {Require(local_memory_mode,"copy requires v4");keys.push_back("copy");}
        } else {
          keys.insert(keys.end(),{"connection","verb","key","address"});
          Require(op.at("verb")=="put" || op.at("verb")=="fetch_add","unsupported transaction verb");
          Require(!op.contains("source") || (source_mode && op.at("verb")=="put" && !op.contains("hex")),"source requires v3 put without hex");
          keys.push_back(op.contains("source")?"source":op.at("verb")=="put"?"hex":"value");
          Integer(op.at("connection"),execution_limits.connections-1);Integer(op.at("key"),UINT32_MAX);Integer(op.at("address"),UINT64_MAX);
          if(op.at("verb")=="fetch_add")Integer(op.at("value"),UINT64_MAX);
          if(explicit_submission)keys.push_back("aggregate_requests");
          if(op.contains("queue_fence"))keys.push_back("queue_fence");
          Require(!(op.contains("queue_fence") && op.contains("local_wait")),"queue fence and local wait require separate steps");
        }
        if(op.contains("local_wait"))keys.push_back("local_wait");
        for(const auto* name:{"application_uses","release_window"})if(op.contains(name))keys.push_back(name);
        Keys(op,keys);
        if(op.contains("local_wait"))transactions.LocalWaits(op); // validate even blocked predicates
        transactions.ValidateSource(op);
        transactions.ValidateLocalMemory(op);
      }
      if(local[i])++local_count;else ++remote_count;
      Require(local_count<=execution_limits.local && remote_count<=execution_limits.remote,"transaction kind count budget exceeded");
      Require(index.emplace(operations[i].at("id"),i).second,"duplicate operation id");
      Require(operations[i].at("after").is_array() && operations[i].at("after").size()<=32,"invalid operation dependencies");
      std::set<std::pair<uint64_t,std::string>> dependencies;
      for(const auto& d:operations[i].at("after")) {
        if(local_mode) {Keys(d,{"operation","event"});Integer(d.at("operation"),65535);}
        const auto kind=d.at("event").get<std::string>();
        Require(index.count(d.at("operation")) && index.at(d.at("operation"))<i,"dependency must name an earlier operation");
        Require(local[index.at(d.at("operation"))]?kind=="completed":
          ((explicit_submission && kind=="prepared") || kind=="applied" || kind=="request_acked" || kind=="completed"),"dependency must match predecessor kind");
        Require(dependencies.emplace(d.at("operation").get<uint64_t>(),kind).second,"repeated transaction dependency");
      }
    }
    Require(remote_count>0,"transaction probe requires remote work");
    std::vector<bool> submitted(operations.size(),false);bool finished=false;
    unsigned complete=0,local_complete=0;
    std::vector<std::array<int64_t,3>> local_times(operations.size(),std::array<int64_t,3>{-1,-1,-1});
    std::map<unsigned,std::deque<std::size_t>> local_queues;
    std::set<unsigned> local_busy;
    std::function<void()> progress;
    std::function<void(unsigned)> pump_local;
    pump_local=[&](unsigned rank) {
      auto& queue=local_queues[rank];
      if(local_busy.count(rank) || queue.empty())return;
      const auto i=queue.front();queue.pop_front();local_busy.insert(rank);
      local_times[i][1]=ns3::Simulator::Now().GetNanoSeconds();
      transactions.StartLocal(operations[i]);
      ns3::Simulator::Schedule(ns3::NanoSeconds(operations[i].at("duration_ns").get<int64_t>()),[&,rank,i]() {
        transactions.CompleteLocal(operations[i]);
        Require(local_busy.erase(rank)==1 && local_times[i][2]<0,"duplicate transaction local completion");
        local_times[i][2]=ns3::Simulator::Now().GetNanoSeconds();++local_complete;
        // An older admitted node retains precedence over a newly unlocked node.
        pump_local(rank);progress();
      });
    };
    progress=[&](){
      for(std::size_t i=0;i<operations.size();++i)if(!submitted[i]) {
        bool ready=true;
        for(const auto& d:operations[i].at("after")) {
          const auto parent=index.at(d.at("operation"));
          ready=ready && (local[parent]?local_times[parent][2]>=0:transactions.Occurred(d.at("operation"),d.at("event")));
        }
        if(ready && transactions.LocalReady(operations[i]) && transactions.QueueFenceReady(operations[i])) {
          if(local[i]) {
            transactions.AdmitLocal(operations[i]);
            submitted[i]=true;
            local_times[i][0]=ns3::Simulator::Now().GetNanoSeconds();
            const auto rank=operations[i].at("rank").get<unsigned>();
            local_queues[rank].push_back(i);pump_local(rank);
          } else {transactions.Submit(operations[i]);submitted[i]=true;}
        }
      }
      complete=0;
      for(std::size_t i=0;i<operations.size();++i)if(!local[i] && transactions.Occurred(operations[i].at("id"),"completed"))++complete;
      if(!finished && complete==remote_count && local_complete==local_count && transactions.Drained()) {
        finished=true;transactions.Close();ns3::Simulator::Stop();
      }
    };
    transactions.changed=progress;ns3::Simulator::ScheduleNow(progress);
    ns3::Simulator::Stop(ns3::NanoSeconds(fabric.MaxSimNs()));
    std::string run_error;
    if(source_mode) {
      try {ns3::Simulator::Run();}catch(const std::exception& e) {run_error=e.what();}
    } else ns3::Simulator::Run();
    auto result=fabric.Report();result["transactions"]=transactions.Report();result["completed_operations"]=complete;
    auto pending=[&](const Json& op) {
      Json row={{"id",op.at("id")}};
      try {row["local_wait"]=transactions.LocalWaits(op);}catch(const std::exception& e) {
        if(!local_memory_mode)throw;
        row["local_wait"]=Json::array();row["local_wait_error"]=e.what();
      }
      return row;
    };
    result["pending_operations"]=Json::array();
    for(std::size_t i=0;i<operations.size();++i)if(!local[i] && !submitted[i])
      result["pending_operations"].push_back(pending(operations[i]));
    if(local_mode) {
      result["local_model"]=input.at("local_model");result["local_work"]=Json::array();
      result["completed_local_work"]=local_complete;result["pending_local_work"]=Json::array();
      for(std::size_t i=0;i<operations.size();++i)if(local[i]) {
        result["local_work"].push_back(Json{{"id",operations[i].at("id")},{"rank",operations[i].at("rank")},
          {"duration_ns",operations[i].at("duration_ns")},{"ready_ns",local_times[i][0]},
          {"start_ns",local_times[i][1]},{"completed_ns",local_times[i][2]}});
        if(local_times[i][2]<0) {
          auto row=pending(operations[i]);row["admitted"]=static_cast<bool>(submitted[i]);
          result["pending_local_work"].push_back(row);
        }
      }
    }
    result["loaded_libraries"]=Json::array();dl_iterate_phdr(Libraries,&result["loaded_libraries"]);
    const bool passed=run_error.empty() && finished && complete==remote_count && local_complete==local_count && transactions.Drained() && fabric.Drained();
    if(!run_error.empty())result["execution_error"]=run_error;
    result["passed"]=passed;
    std::ofstream out(argv[4]);out<<result.dump()<<'\n';out.close();Require(bool(out),"cannot write transaction report");
    ns3::Simulator::Destroy();Require(passed,"incomplete native transaction execution; inspect report");
    std::cout<<"completed "<<complete<<" functional remote memory transactions over actual native packets\n";return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';ns3::Simulator::Destroy();return 1;}
}
