#include "remote_transactions.hh"
#include <fstream>
#include <iostream>
#include <link.h>
using namespace moe_rdma;
static int Libraries(dl_phdr_info* info,std::size_t,void* data) {
  if(info->dlpi_name && info->dlpi_name[0])static_cast<Json*>(data)->push_back(info->dlpi_name);
  return 0;
}
int main(int argc,char** argv) {
  try {
    Require(argc==5,"usage: moe-rdma-transaction-probe TOPOLOGY.json RDMA.json WORK.json NEW_RESULT.json");
    std::ifstream exists(argv[4]);Require(!exists.good(),"transaction output already exists");
    ns3::Time::SetResolution(ns3::Time::NS);
    const auto config=ReadJson(argv[2]),input=ReadJson(argv[3]);
    ns3::RngSeedManager::SetSeed(config.at("seed"));
    Fabric fabric(ReadJson(argv[1]),config);RemoteTransactions transactions(fabric,input);
    const auto& operations=input.at("operations");
    Require(operations.is_array() && !operations.empty() && operations.size()<=32,"transaction count budget exceeded");
    std::map<uint64_t,std::size_t> index;
    for(std::size_t i=0;i<operations.size();++i) {
      Require(index.emplace(operations[i].at("id"),i).second,"duplicate operation id");
      Require(operations[i].at("after").is_array() && operations[i].at("after").size()<=32,"invalid operation dependencies");
      for(const auto& d:operations[i].at("after")) {
        const auto kind=d.at("event").get<std::string>();
        Require(index.count(d.at("operation")) && index.at(d.at("operation"))<i &&
          (kind=="applied" || kind=="request_acked" || kind=="completed"),"dependency must name an earlier operation event");
      }
    }
    std::vector<bool> submitted(operations.size(),false);bool finished=false;
    unsigned complete=0;
    std::function<void()> progress=[&](){
      for(std::size_t i=0;i<operations.size();++i)if(!submitted[i]) {
        bool ready=true;
        for(const auto& d:operations[i].at("after"))ready=ready && transactions.Occurred(d.at("operation"),d.at("event"));
        if(ready){submitted[i]=true;transactions.Submit(operations[i]);}
      }
      complete=0;
      for(const auto& op:operations)if(transactions.Occurred(op.at("id"),"completed"))++complete;
      if(!finished && complete==operations.size() && transactions.Drained()) {
        finished=true;transactions.Close();ns3::Simulator::Stop();
      }
    };
    transactions.changed=progress;ns3::Simulator::ScheduleNow(progress);
    ns3::Simulator::Stop(ns3::NanoSeconds(fabric.MaxSimNs()));ns3::Simulator::Run();
    auto result=fabric.Report();result["transactions"]=transactions.Report();result["completed_operations"]=complete;
    result["loaded_libraries"]=Json::array();dl_iterate_phdr(Libraries,&result["loaded_libraries"]);
    const bool passed=finished && complete==operations.size() && transactions.Drained() && fabric.Drained();
    result["passed"]=passed;
    std::ofstream out(argv[4]);out<<result.dump()<<'\n';out.close();Require(bool(out),"cannot write transaction report");
    ns3::Simulator::Destroy();Require(passed,"incomplete native transaction execution; inspect report");
    std::cout<<"completed "<<complete<<" functional remote memory transactions over actual native packets\n";return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';ns3::Simulator::Destroy();return 1;}
}
