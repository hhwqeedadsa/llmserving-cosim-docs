#include "persistent_streams.hh"
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
    Require(argc==5,"usage: moe-rdma-stream-probe TOPOLOGY.json RDMA.json WORK.json NEW_RESULT.json");
    std::ifstream exists(argv[4]);Require(!exists.good(),"stream output already exists");
    ns3::Time::SetResolution(ns3::Time::NS);
    auto config=ReadJson(argv[2]),input=ReadJson(argv[3]);
    Require(input.at("schema_version")=="moe-rdma-stream-work/1","wrong stream work schema");
    ns3::RngSeedManager::SetSeed(config.at("seed"));
    Fabric fabric(ReadJson(argv[1]),config);PersistentStreams transport(fabric);
    Require(input.at("streams").is_array() && !input.at("streams").empty() && input.at("streams").size()<=16,"stream count budget exceeded");
    for(const auto& s:input.at("streams"))transport.Open(s.at("id"),s.at("src"),s.at("dst"),s.at("plane"),
      s.at("wire_port"),s.at("source_nic"),s.at("destination_nic"),s.at("queue_depth"));
    const auto& work=input.at("work");Require(work.is_array() && !work.empty() && work.size()<=64,"work count budget exceeded");
    std::map<uint64_t,std::size_t> index;
    for(std::size_t i=0;i<work.size();++i) {
      Require(index.emplace(work[i].at("id"),i).second,"duplicate stream work id");
      Require(work[i].at("after").is_array() && work[i].at("after").size()<=64,"invalid work dependencies");
      for(const auto& d:work[i].at("after")) {
        const auto kind=d.at("event").get<std::string>();
        Require(index.count(d.at("work")) && index.at(d.at("work"))<i && (kind=="accepted" || kind=="acked"),"dependency must reference earlier work and accepted/acked");
      }
    }
    std::vector<bool> submitted(work.size(),false),accepted(work.size(),false),acked(work.size(),false);
    unsigned complete=0;bool finished=false;
    std::function<void()> progress;
    progress=[&](){
      for(std::size_t i=0;i<work.size();++i) if(!submitted[i]) {
        bool ready=true;
        for(const auto& d:work[i].at("after")) {
          const auto parent=index.at(d.at("work"));
          ready=ready && (d.at("event")=="accepted"?accepted[parent]:acked[parent]);
        }
        if(!ready)continue;
        submitted[i]=true;
        transport.Submit(work[i].at("stream"),work[i].at("id"),work[i].at("bytes"),
          [&,i](){Require(!accepted[i],"duplicate application acceptance");accepted[i]=true;progress();},
          [&,i](){Require(!acked[i],"duplicate application ACK");acked[i]=true;++complete;progress();});
      }
      if(!finished && complete==work.size() && transport.Drained()) {
        finished=true;transport.Close();ns3::Simulator::Stop();
      }
    };
    transport.changed=progress;
    ns3::Simulator::ScheduleNow(progress);
    ns3::Simulator::Stop(ns3::NanoSeconds(fabric.MaxSimNs()));ns3::Simulator::Run();
    auto result=fabric.Report();result["persistent_transport"]=transport.Report();
    result["completed_work"]=complete;result["loaded_libraries"]=Json::array();
    dl_iterate_phdr(Libraries,&result["loaded_libraries"]);
    const bool passed=finished && complete==work.size() && transport.Drained() && fabric.Drained();
    result["passed"]=passed;
    std::ofstream out(argv[4]);out<<result.dump()<<'\n';out.close();Require(bool(out),"cannot write stream report");
    ns3::Simulator::Destroy();Require(passed,"incomplete persistent stream execution; inspect report");
    std::cout<<"completed "<<complete<<" event-driven persistent native byte writes\n";return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';ns3::Simulator::Destroy();return 1;}
}
