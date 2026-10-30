#include "shared_compute.hh"
#include <iostream>

int main() {
  using namespace moe_rdma;
  try {
    ns3::Time::SetResolution(ns3::Time::NS);SharedCompute queue(4,10);
    auto add=[&queue](const char* key,unsigned rank,int64_t duration,std::function<void()> done={}) {
      queue.Submit(key,rank,duration,{{"kind","functional_check"}}, {},done?std::move(done):[](){});
    };
    add("original.a",0,10,[&](){add("original.b",0,5);});add("original.c",1,3);
    ns3::Simulator::Schedule(ns3::NanoSeconds(1),[&](){add("helper.h",0,2);add("original.zero",0,0);});
    ns3::Simulator::Schedule(ns3::NanoSeconds(2),[&](){add("helper.k",1,4);});
    ns3::Simulator::Run();Require(queue.Empty(),"shared FIFO did not drain");
    const auto report=queue.Report();std::map<std::string,std::pair<int64_t,int64_t>> expected={
      {"original.a",{0,10}},{"original.b",{12,17}},{"original.c",{0,3}},
      {"helper.h",{10,12}},{"original.zero",{12,12}},{"helper.k",{3,7}}};
    Require(report.at("tasks").size()==expected.size(),"wrong task count");
    for(const auto& row:report.at("tasks")) {
      auto times=expected.at(row.at("key").get<std::string>());
      Require(row.at("start_ns")==times.first && row.at("done_ns")==times.second,"independent FIFO schedule differs");
    }
    unsigned rejected=0;
    for(auto bad:std::vector<std::tuple<std::string,unsigned,int64_t>>{{"original.a",0,1},{"bad.rank",4,1},{"bad.cost",0,-1}}) {
      try{add(std::get<0>(bad).c_str(),std::get<1>(bad),std::get<2>(bad));}
      catch(const std::exception&){++rejected;}
    }
    Require(rejected==3 && queue.Report()==report,"bad shared work mutated execution");
    std::cout<<Json({{"passed",true},{"tasks",6},{"rejections",3},{"report",report}}).dump()<<'\n';
    ns3::Simulator::Destroy();return 0;
  } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';ns3::Simulator::Destroy();return 1;}
}
