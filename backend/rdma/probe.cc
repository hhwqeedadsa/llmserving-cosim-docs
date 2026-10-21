#include "fabric.hh"
#include <fstream>
#include <iostream>
#include <link.h>
using namespace moe_rdma;
static int Libraries(dl_phdr_info* info,std::size_t,void* data) {
  if(info->dlpi_name && info->dlpi_name[0])static_cast<Json*>(data)->push_back(info->dlpi_name);return 0;
}
int main(int argc,char** argv) {
  try {
    Require(argc==5,"usage: moe-rdma-probe TOPOLOGY.json RDMA.json MESSAGES.json NEW_RESULT.json");
    std::ifstream existing(argv[4]);Require(!existing.good(),"output already exists");
    ns3::Time::SetResolution(ns3::Time::NS);
    auto config=ReadJson(argv[2]); ns3::RngSeedManager::SetSeed(config.at("seed"));
    Fabric fabric(ReadJson(argv[1]),config);
    auto messages=ReadJson(argv[3]);Require(messages.is_array() && messages.size()<=128,"message budget exceeded");
    unsigned done=0;
    for(const auto& m:messages) {
      const auto start=m.at("ready_ns").get<int64_t>();Require(start>=0 && start<fabric.MaxSimNs(),"message start outside time budget");
      ns3::Simulator::Schedule(ns3::NanoSeconds(start),[&fabric,&done,m](){
        fabric.Send(m.at("id"),m.at("src"),m.at("dst"),m.at("bytes"),m.at("plane"),[&done](){++done;});
      });
    }
    ns3::Simulator::Stop(ns3::NanoSeconds(fabric.MaxSimNs()));ns3::Simulator::Run();
    auto report=fabric.Report();report["completed_messages"]=done;report["loaded_libraries"]=Json::array();
    dl_iterate_phdr(Libraries,&report["loaded_libraries"]);
    const bool passed=done==messages.size() && fabric.Drained();report["passed"]=passed;
    std::ofstream out(argv[4]);out<<report.dump(2)<<'\n';out.close();Require(bool(out),"cannot write report");
    ns3::Simulator::Destroy();Require(passed,"incomplete messages; inspect report");
    std::cout<<"completed "<<done<<" native RDMA messages\n";return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';ns3::Simulator::Destroy();return 1;}
}
