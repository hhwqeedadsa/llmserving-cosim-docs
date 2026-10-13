#include "ns3/ub-app.h"
#include "ns3/ub-transfer-executor.h"
#include "ns3/ub-ldst-api.h"
#include "ns3/ub-ldst-thread.h"
#include "ns3/ub-utils.h"
#include "json/json.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <set>

namespace {
using namespace ns3; using J=nlohmann::json;
void Check(bool ok,const char* why) { if(!ok) throw std::runtime_error(why); }
struct Audit {
    J issues=J::array();
    std::map<uint32_t,std::map<uint64_t,uint32_t>> ranges;
    std::set<std::tuple<uint32_t,uint64_t,uint32_t>> pending;
    std::set<uint32_t> done;
    unsigned peak=0,workerBlocks=0,poolBlocks=0,released=0;
    bool nextBeforeRetirement=false;
    void Queue(Ptr<const UbLdstThread> t,std::string event,Ptr<const UbLdstTaskSegment> s,uint64_t offset,uint32_t bytes) {
        const auto key=std::make_tuple(s->GetTaskId(),offset,bytes);
        if(event=="request_issued") {
            Check(pending.insert(key).second,"request issued twice");
            Check(ranges[s->GetTaskId()].emplace(offset,bytes).second,"duplicate byte offset");
            issues.push_back({{"task",s->GetTaskId()},{"offset",offset},{"bytes",bytes},
                {"time_ns",Simulator::Now().GetNanoSeconds()},{"producer",*s->GetProducer()}});
            if(s->GetTaskId()==103 && !done.count(100)) nextBeforeRetirement=true;
        } else if(event=="response_released") {
            Check(pending.erase(key)==1,"response released an unowned request");++released;
        } else if(event=="outstanding_blocked") {
            Check(t->GetInFlight(false)==t->GetCapacity(false),"false worker block");++workerBlocks;
        } else if(event=="request_pool_blocked") ++poolBlocks;
        Check(pending.size()==t->GetOutstandingCount(),"request ownership differs from worker state");
        Check(t->GetInFlight(false)<=t->GetCapacity(false),"producer queues multiplied total credit");
        peak=std::max(peak,t->GetOutstandingCount());
    }
};
}
int main(int argc,char** argv) {
    try {
        Check(argc==7,"Usage: native-queue-policy-test CASE NEW_OUTPUT POLICY PRODUCERS QUOTA POOL");
        namespace fs=std::filesystem;
        auto source=fs::absolute(argv[1]),out=fs::absolute(argv[2]);std::string policy=argv[3];
        unsigned producers=std::stoul(argv[4]),quota=std::stoul(argv[5]),pool=std::stoul(argv[6]);
        Check(!fs::exists(out),"output exists");fs::create_directories(out);fs::current_path(out);
        for(auto name:{"network_attribute.txt","node.csv","topology.csv","routing_table.csv"})fs::copy_file(source/name,name);
        Time::SetResolution(Time::NS);RngSeedManager::SetSeed(1);RngSeedManager::SetRun(1);
        auto utils=utils::UbUtils::Get();utils->SetComponentsAttribute("network_attribute.txt");
        if(policy!="default")Config::SetDefault("ns3::UbLdstThread::StoreQueuePolicy",StringValue(policy));
        Config::SetDefault("ns3::UbLdstInstance::ThreadNum",UintegerValue(1));
        Config::SetDefault("ns3::UbLdstThread::StoreOutstanding",UintegerValue(quota));
        Config::SetDefault("ns3::UbLdstThread::StoreRequestSize",UintegerValue(4096));
        Config::SetDefault("ns3::UbLdstApi::RequestIdCount",UintegerValue(pool));
        Config::SetDefault("ns3::UbLdstApi::UsePacketSpray",BooleanValue(false));
        for(auto flag:{"UB_TRACE_ENABLE","UB_TASK_TRACE_ENABLE","UB_PACKET_TRACE_ENABLE","UB_PORT_TRACE_ENABLE",
                      "UB_QUEUE_TRACE_ENABLE","UB_RECORD_PKT_TRACE","UB_FLOW_CONTROL_TRACE_ENABLE",
                      "UB_CONGESTION_CONTROL_TRACE_ENABLE","UB_PARSE_TRACE_ENABLE"})GlobalValue::Bind(flag,BooleanValue(false));
        utils->CreateNode("node.csv");utils->CreateTopo("topology.csv");utils->AddRoutingTable("routing_table.csv");
        auto node=NodeList::GetNode(0);auto app=CreateObject<UbApp>();node->AddApplication(app);
        auto instance=node->GetObject<UbLdstInstance>();auto thread=instance->GetLdstThread(0);
        auto api=node->GetObject<UbController>()->GetUbFunction()->GetUbLdstApi();
        Audit a;Check(thread->TraceConnectWithoutContext("QueueState",MakeCallback(&Audit::Queue,&a)),"missing queue trace");
        const std::vector<unsigned> sizes={40*4096+1,32,8192,64,4097,4096};
        Simulator::ScheduleNow([&](){
            for(unsigned i=0;i<sizes.size();++i) {
                UbTrafficGen::RuntimeTask t;t.taskId=100+i;t.sourceNode=0;t.destNode=1;t.dataSize=sizes[i];t.priority=7;
                t.op=UbTrafficGen::RuntimeTaskOp::MEM_STORE;
                if(producers) t.nativeProducer=producers==1?10:(i%3+1)*10;
                app->GetTransferExecutor().Submit(t,[&,t](uint32_t id){
                    Check(id==t.taskId && a.done.insert(id).second,"duplicate/wrong callback");
                    uint64_t end=0;for(auto [offset,bytes]:a.ranges.at(id)){Check(offset==end,"gap or overlap");end+=bytes;}
                    Check(end==t.dataSize,"completion before all useful requests");
                });
            }
        });
        Simulator::Stop(MilliSeconds(1));Simulator::Run();
        Check(a.done.size()==6 && a.pending.empty() && instance->IsIdle() && !api->GetPendingRequestCount() &&
              !api->GetWaitingThreadCount() && !app->GetTransferExecutor().GetPendingCount(),"unfinished tasks or leaked resources");
        std::vector<unsigned> expected;
        if(policy=="producer_round_robin" && producers>1) {
            expected={100,101,102,100,104,102,100,104,105};
            expected.insert(expected.end(),38,100);expected.push_back(103);
        } else {
            expected.insert(expected.end(),41,100);
            for(unsigned id:{101,102,102,103,104,104,105})expected.push_back(id);
        }
        Check(a.issues.size()==expected.size() && a.released==expected.size(),"request or release count changed");
        for(size_t i=0;i<expected.size();++i)Check(a.issues[i]["task"]==expected[i],"request ordering violates declared policy");
        Check(a.peak==std::min(quota,pool),"did not exercise total capacity");
        Check((pool<quota?a.poolBlocks:a.workerBlocks)>0,"missing actual blocking branch");
        Check(a.nextBeforeRetirement,"producer unnecessarily waited for transaction retirement");
        J result={{"passed",true},{"issues",a.issues},{"released",a.released},{"peak",a.peak},
            {"worker_blocks",a.workerBlocks},{"pool_blocks",a.poolBlocks},{"next_before_retirement",a.nextBeforeRetirement}};
        std::ofstream(out/"result.json")<<result.dump(2)<<'\n';Simulator::Destroy();return 0;
    } catch(const std::exception& e) { std::cerr<<e.what()<<'\n';Simulator::Destroy();return 1; }
}
