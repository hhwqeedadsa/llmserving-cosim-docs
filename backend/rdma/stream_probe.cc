#include "persistent_streams.hh"
#include <fstream>
#include <iostream>
#include <link.h>
#include <deque>

using namespace moe_rdma;
static int Libraries(dl_phdr_info* info,std::size_t,void* data) {
  if(info->dlpi_name && info->dlpi_name[0])static_cast<Json*>(data)->push_back(info->dlpi_name);
  return 0;
}
static void Keys(const Json& value,std::initializer_list<const char*> keys) {
  Require(value.is_object() && value.size()==keys.size(),"wrong local-stage fields");
  for(auto key:keys)Require(value.contains(key),"missing local-stage field");
}
static int64_t Integer(const Json& value,int64_t low,int64_t high) {
  Require(value.is_number_integer() && value>=low && value<=high,"invalid local-stage integer");
  return value.get<int64_t>();
}
int main(int argc,char** argv) {
  try {
    Require(argc==5,"usage: moe-rdma-stream-probe TOPOLOGY.json RDMA.json WORK.json NEW_RESULT.json");
    std::ifstream exists(argv[4]);Require(!exists.good(),"stream output already exists");
    ns3::Time::SetResolution(ns3::Time::NS);
    auto config=ReadJson(argv[2]),input=ReadJson(argv[3]);
    const auto schema=input.at("schema_version").get<std::string>();
    const bool scale_mode=schema=="moe-rdma-stream-work/4";
    const bool local_mode=scale_mode || schema=="moe-rdma-stream-work/3";
    Require(schema=="moe-rdma-stream-work/1" || schema=="moe-rdma-stream-work/2" || local_mode,"wrong stream work schema");
    PersistentStreams::Limits limits;
    if(schema=="moe-rdma-stream-work/2" || local_mode) {
      const auto& declared=input.at("limits");
      Require(declared.is_object() && declared.size()==(scale_mode?3u:2u),"stream limits require exact count and optional v4 transmission fields");
      auto count=[&](const char* key,int maximum) {
        const auto& value=declared.at(key);
        Require(value.is_number_integer() && value>=1 && value<=maximum,"invalid stream count limit");
        return value.get<unsigned>();
      };
      limits={count("max_streams",scale_mode?4096:128),count("max_work",scale_mode?65536:8192),
              scale_mode?count("max_link_transmissions",2000000):200000u};
    } else Require(!input.contains("limits"),"v1 stream work has fixed limits");
    if(scale_mode) {
      Require(input.at("streams").is_array() && !input.at("streams").empty() && input.at("streams").size()<=limits.streams,
              "stream count budget exceeded before allocation");
      Require(input.at("work").is_array() && !input.at("work").empty() && input.at("work").size()<=limits.work,
              "stream work count budget exceeded before allocation");
    }
    ns3::RngSeedManager::SetSeed(config.at("seed"));
    const auto topology=ReadJson(argv[1]);
    Require(!input.contains("transport_recovery") || input.at("transport_recovery").is_object(),"transport_recovery must be an object");
    Fabric fabric(topology,config);PersistentStreams transport(fabric,limits,input.value("transport_recovery",Json()));
    std::set<unsigned> gpu_ids;
    if(local_mode) {
      Keys(input.at("local_model"),{"mode","duration_source"});
      Require(input.at("local_model")==Json{{"mode","serial_per_gpu/1"},{"duration_source","declared_ns"}},"unsupported local-stage model");
      for(const auto& n:topology.at("nodes"))if(n.at("kind")=="gpu")gpu_ids.insert(n.at("id").get<unsigned>());
    }
    if(input.contains("source_rate_capture"))transport.ConfigureRateCapture(input.at("source_rate_capture"));
    if(input.contains("dcqcn_reference"))transport.ConfigureDcqcnReference(input.at("dcqcn_reference"));
    Require(input.at("streams").is_array() && !input.at("streams").empty() && input.at("streams").size()<=limits.streams,"stream count budget exceeded");
    for(const auto& s:input.at("streams"))transport.Open(s.at("id"),s.at("src"),s.at("dst"),s.at("plane"),
      s.at("wire_port"),s.at("source_nic"),s.at("destination_nic"),s.at("queue_depth"));
    transport.ValidateFaultBindings();
    const auto& work=input.at("work");Require(work.is_array() && !work.empty() && work.size()<=limits.work,"work count budget exceeded");
    std::map<uint64_t,std::size_t> index;
    std::vector<std::array<std::vector<std::size_t>,3>> successors(work.size());
    std::vector<std::size_t> remaining(work.size(),0);
    std::vector<bool> local(work.size(),false);
    std::set<std::size_t> ready;
    for(std::size_t i=0;i<work.size();++i) {
      if(local_mode) {
        const auto kind=work[i].at("kind").get<std::string>();
        Require(kind=="write" || kind=="local","unknown local-stage work kind");local[i]=kind=="local";
        Integer(work[i].at("id"),0,65535);
        if(local[i]) {
          Keys(work[i],{"id","kind","rank","duration_ns","after"});
          const auto rank=Integer(work[i].at("rank"),0,65535);
          Require(gpu_ids.count(static_cast<unsigned>(rank)),"local-stage rank is not a topology GPU");
          Integer(work[i].at("duration_ns"),0,fabric.MaxSimNs());
        } else {
          Keys(work[i],{"id","kind","stream","bytes","after"});
          Integer(work[i].at("stream"),0,65535);Integer(work[i].at("bytes"),1,1024*1024);
        }
      }
      Require(index.emplace(work[i].at("id"),i).second,"duplicate stream work id");
      Require(work[i].at("after").is_array() && work[i].at("after").size()<=64,"invalid work dependencies");
      std::set<std::pair<std::size_t,unsigned>> dependencies;
      for(const auto& d:work[i].at("after")) {
        if(local_mode) {Keys(d,{"work","event"});Integer(d.at("work"),0,65535);}
        const auto kind=d.at("event").get<std::string>();
        Require(index.count(d.at("work")) && index.at(d.at("work"))<i,"dependency must reference earlier work");
        const auto parent=index.at(d.at("work"));
        Require(local[parent]?kind=="completed":(kind=="accepted" || kind=="acked"),"dependency event does not match predecessor kind");
        const unsigned event=kind=="accepted"?0:(kind=="acked"?1:2);
        Require(dependencies.emplace(parent,event).second,"repeated stream dependency");
        successors[parent][event].push_back(i);++remaining[i];
      }
      if(!remaining[i])ready.insert(i);
    }
    std::vector<bool> submitted(work.size(),false),accepted(work.size(),false),acked(work.size(),false);
    unsigned complete=0,local_complete=0;bool finished=false;
    std::vector<std::array<int64_t,3>> local_times(work.size(),std::array<int64_t,3>{-1,-1,-1});
    std::map<unsigned,std::deque<std::size_t>> local_queues;
    std::set<unsigned> local_busy;
    std::function<void()> progress;
    std::function<void(unsigned)> pump_local;
    auto signal=[&](std::size_t parent,unsigned event) {
      for(auto child:successors[parent][event]) {
        Require(remaining[child]>0,"duplicate stream dependency notification");
        if(!--remaining[child])ready.insert(child);
      }
    };
    pump_local=[&](unsigned rank) {
      auto& queue=local_queues[rank];
      if(local_busy.count(rank) || queue.empty())return;
      const auto i=queue.front();queue.pop_front();local_busy.insert(rank);
      local_times[i][1]=ns3::Simulator::Now().GetNanoSeconds();
      ns3::Simulator::Schedule(ns3::NanoSeconds(work[i].at("duration_ns").get<int64_t>()),[&,rank,i]() {
        Require(local_busy.erase(rank)==1 && local_times[i][2]<0,"repeated local-stage completion");
        local_times[i][2]=ns3::Simulator::Now().GetNanoSeconds();++complete;++local_complete;
        signal(i,2);
        // Older ready work already queued on this GPU retains FIFO precedence.
        pump_local(rank);progress();
      });
    };
    progress=[&](){
      // Input order breaks ties within one ready batch. Separate callbacks
      // at the same timestamp retain ns-3 event order.
      while(!ready.empty()) {
        const auto i=*ready.begin();ready.erase(ready.begin());
        Require(!submitted[i],"duplicate ready stream work");
        submitted[i]=true;
        if(local[i]) {
          local_times[i][0]=ns3::Simulator::Now().GetNanoSeconds();
          const auto rank=work[i].at("rank").get<unsigned>();
          local_queues[rank].push_back(i);pump_local(rank);continue;
        }
        transport.Submit(work[i].at("stream"),work[i].at("id"),work[i].at("bytes"),
          [&,i](){Require(!accepted[i],"duplicate application acceptance");accepted[i]=true;signal(i,0);progress();},
          [&,i](){Require(!acked[i],"duplicate application ACK");acked[i]=true;++complete;signal(i,1);progress();});
      }
      if(!finished && complete==work.size() && transport.Drained()) {
        finished=true;transport.Close();ns3::Simulator::Stop();
      }
    };
    transport.changed=progress;
    ns3::Simulator::ScheduleNow(progress);
    ns3::Simulator::Stop(ns3::NanoSeconds(fabric.MaxSimNs()));ns3::Simulator::Run();
    auto result=fabric.Report();result["persistent_transport"]=transport.Report();
    if(scale_mode) {
      result["persistent_transport"]["execution_limits"]=input.at("limits");
      result["persistent_transport"]["link_transmissions"]=transport.LinkTransmissions();
    }
    result["completed_work"]=complete;result["loaded_libraries"]=Json::array();
    if(local_mode) {
      result["local_model"]=input.at("local_model");result["local_work"]=Json::array();
      result["completed_local_work"]=local_complete;
      for(std::size_t i=0;i<work.size();++i)if(local[i])result["local_work"].push_back(Json{
        {"id",work[i].at("id")},{"rank",work[i].at("rank")},{"duration_ns",work[i].at("duration_ns")},
        {"ready_ns",local_times[i][0]},{"start_ns",local_times[i][1]},{"completed_ns",local_times[i][2]}});
    }
    dl_iterate_phdr(Libraries,&result["loaded_libraries"]);
    const bool passed=finished && complete==work.size() && transport.Drained() && fabric.Drained();
    result["passed"]=passed;
    std::ofstream out(argv[4]);out<<result.dump()<<'\n';out.close();Require(bool(out),"cannot write stream report");
    ns3::Simulator::Destroy();Require(passed,"incomplete persistent stream execution; inspect report");
    std::cout<<"completed "<<complete<<(local_mode?" event-driven network/local work nodes\n":" event-driven persistent native byte writes\n");return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';ns3::Simulator::Destroy();return 1;}
}
