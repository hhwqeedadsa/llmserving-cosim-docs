#include "legacy_executor.hh"
#include "legacy_execution_validation.hh"
#include "legacy_shared_validation.hh"
#include <fstream>
#include <iostream>
#include <link.h>

using namespace moe_rdma;
namespace {
int Libraries(dl_phdr_info* info,std::size_t,void* data){if(info->dlpi_name && info->dlpi_name[0])static_cast<Json*>(data)->push_back(info->dlpi_name);return 0;}
}
int main(int argc,char** argv) {
  try {
    Require(argc==5,"usage: legacy-deepep TOPOLOGY PROTOCOL EXECUTION NEW_RESULT");
    std::ifstream prior(argv[4]);Require(!prior.good(),"legacy result already exists");
    const auto topology=ReadJson(argv[1]),protocol=ReadJson(argv[2]),input=ReadJson(argv[3]);
    const bool shared=input.at("schema_version")=="moe-legacy-shared-execution/1";
    if(shared)ValidateLegacyShared(input,topology,protocol);else ValidateLegacyExecution(input,topology,protocol);
    const Json groups=shared?input.at("groups"):Json::array({Json{{"id","single"},{"execution",input}}});
    unsigned streams=0,work=0;
    for(const auto& group:groups) {
      streams+=group.at("execution").at("limits").at("streams").get<unsigned>();
      work+=group.at("execution").at("limits").at("work").get<unsigned>();
    }
    ns3::Time::SetResolution(ns3::Time::NS);ns3::RngSeedManager::SetSeed(protocol.at("seed"));
    Fabric fabric(topology,protocol);
    PersistentStreams transport(fabric,{streams,work,2000000,shared?200000u:100000u},input.at("transport_recovery"));
    transport.ConfigureMessagePayload(shared?64*1024*1024:32*1024*1024);
    transport.ConfigureDcqcnReference(input.at("dcqcn_reference"));
    std::vector<std::unique_ptr<LegacyExecutor>> executors;uint64_t next_work=0;unsigned stream_offset=0;
    auto finished=[&](){return std::all_of(executors.begin(),executors.end(),[](const auto& executor){return executor->Finished();});};
    auto finish=[&](){if(finished() && transport.Drained()){transport.Close();ns3::Simulator::Stop();}};
    for(const auto& group:groups) {
      const auto& execution=group.at("execution");
      executors.push_back(std::make_unique<LegacyExecutor>(transport,execution,stream_offset,next_work,finish));
      stream_offset+=execution.at("limits").at("streams").get<unsigned>();
    }
    transport.ValidateFaultBindings();
    transport.changed=[&](){for(auto& executor:executors)executor->Notify();};
    ns3::Simulator::Stop(ns3::NanoSeconds(fabric.MaxSimNs()));std::string error;
    try{ns3::Simulator::Run();}catch(const std::exception& e){error=e.what();}
    auto result=fabric.Report();
    if(shared) {
      Json reports=Json::array();stream_offset=0;
      for(unsigned i=0;i<executors.size();++i) {
        reports.push_back({{"id",groups.at(i).at("id")},{"stream_offset",stream_offset},
                           {"legacy",executors[i]->Report(false)}});
        stream_offset+=groups.at(i).at("execution").at("limits").at("streams").get<unsigned>();
      }
      result["legacy_shared"]={{"groups",reports},{"transport",transport.Report()},{"finished",finished()},
        {"scope","independent complete legacy groups sharing one Fabric/Simulator/transport; no GIN/RoCE/GPU calibration"}};
    } else result["legacy"]=executors[0]->Report();
    result["loaded_libraries"]=Json::array();
    dl_iterate_phdr(Libraries,&result["loaded_libraries"]);
    result["passed"]=error.empty() && finished() && fabric.Drained();
    if(!error.empty())result["execution_error"]=error;
    std::ofstream output(argv[4]);output<<result.dump()<<'\n';output.close();Require(bool(output),"cannot write legacy result");
    const auto passed=result.at("passed").get<bool>();ns3::Simulator::Destroy();Require(passed,"legacy execution incomplete; inspect retained result");return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';ns3::Simulator::Destroy();return 1;}
}
