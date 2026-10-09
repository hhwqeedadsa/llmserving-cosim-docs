#include "ns3/ub-app.h"
#include "ns3/ub-transfer-executor.h"
#include "ns3/ub-ldst-api.h"
#include "ns3/ub-utils.h"
#include "json/json.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>

namespace {
using namespace ns3; using J=nlohmann::json;
void Check(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
uint64_t TimeNs(){return Simulator::Now().GetNanoSeconds();}
struct Audit {
    std::map<uint64_t,std::pair<uint32_t,uint32_t>> requests;
    std::map<uint32_t,uint64_t> bytes,ready,done;
    J packets=J::array(),queues=J::array();
    uint64_t starts=0,arrivals=0,hash=14695981039346656037ull;
    bool detail=true;
    void Hash(uint64_t v){for(int i=0;i<8;++i){hash^=v&255;hash*=1099511628211ull;v>>=8;}}
    void Created(Ptr<const Packet> p,uint32_t task,uint64_t,uint32_t n,UbMemOperationType type){
        Check(type==UbMemOperationType::STORE && requests.emplace(p->GetUid(),std::make_pair(task,n)).second,"duplicate STORE request");
    }
    void Delivered(uint64_t uid,uint32_t n,UbMemOperationType type){
        Check(type==UbMemOperationType::STORE,"wrong operation");auto x=requests.at(uid);Check(n==x.second,"changed bytes");
        bytes[x.first]+=n;ready[x.first]=TimeNs();requests.erase(uid);
    }
    static void Tx(Audit* a,uint32_t node,uint32_t port,Ptr<Packet> p,Time duration){
        ++a->starts;a->Hash(TimeNs());a->Hash(node);a->Hash(port);a->Hash(duration.GetNanoSeconds());
        std::vector<uint8_t> wire(p->GetSize());p->CopyData(wire.data(),wire.size());
        for(auto b:wire){a->hash^=b;a->hash*=1099511628211ull;}
        if(!a->detail || node!=4 || port<2)return;
        auto device=DynamicCast<UbPort>(NodeList::GetNode(node)->GetDevice(port));
        Check(duration==device->GetEffectiveDataRate().CalculateBytesTxTime(p->GetSize())+device->GetInterframeGap(),"serialization mismatch");
        auto copy=p->Copy();UbDatalinkHeader dl;copy->PeekHeader(dl);
        if(!dl.IsPacketUbMemHeader())return;
        UbDatalinkPacketHeader header;copy->RemoveHeader(header);if(header.GetACK())return;
        a->packets.push_back({{"port",port},{"time_ns",TimeNs()},{"bytes",p->GetSize()},
            {"duration_ns",duration.GetNanoSeconds()},{"rate_bps",device->GetEffectiveDataRate().GetBitRate()}});
    }
    void Rx(Ptr<Packet>){++arrivals;}
    void Voq(uint32_t port,uint64_t n){if(port>=2)queues.push_back({{"kind","voq"},{"port",port},{"time_ns",TimeNs()},{"bytes",n}});}
    static void Fifo(Audit* a,uint32_t port,uint64_t n){a->queues.push_back({{"kind","fifo"},{"port",port},{"time_ns",TimeNs()},{"bytes",n}});}
};
}
int main(int argc,char** argv){
    namespace fs=std::filesystem;
    try{
        Check(argc==5,"Usage: a3-egress-test STAR_CASE NEW_OUTPUT SHARED_0_OR_1 DETAIL_0_OR_1");
        const auto source=fs::absolute(argv[1]),out=fs::absolute(argv[2]);const bool shared=std::string(argv[3])=="1";
        Check(!fs::exists(out),"output exists");fs::create_directories(out);fs::current_path(out);
        for(auto name:{"network_attribute.txt","node.csv","topology.csv","routing_table.csv"})fs::copy_file(source/name,name);
        Time::SetResolution(Time::NS);RngSeedManager::SetSeed(1);RngSeedManager::SetRun(1);
        auto u=utils::UbUtils::Get();UbEgressQueue::GetTypeId();u->SetComponentsAttribute("network_attribute.txt");
        Config::SetDefault("ns3::UbLdstInstance::ThreadNum",UintegerValue(2));
        Config::SetDefault("ns3::UbLdstThread::StoreOutstanding",UintegerValue(2));
        Config::SetDefault("ns3::UbLdstThread::StoreRequestSize",UintegerValue(4096));
        Config::SetDefault("ns3::UbLdstApi::RequestIdCount",UintegerValue(4));
        Config::SetDefault("ns3::UbLdstApi::UsePacketSpray",BooleanValue(false));
        u->CreateTraceDir();u->CreateNode("node.csv");u->CreateTopo("topology.csv");u->AddRoutingTable("routing_table.csv");
        Audit a;a.detail=std::string(argv[4])=="1";std::vector<Ptr<UbApp>> apps;
        for(uint32_t i=0;i<4;++i){
            auto n=NodeList::GetNode(i);auto app=CreateObject<UbApp>();n->AddApplication(app);apps.push_back(app);
            auto api=n->GetObject<UbController>()->GetUbFunction()->GetUbLdstApi();
            Check(api->TraceConnectWithoutContext("RequestCreated",MakeCallback(&Audit::Created,&a)) &&
                api->TraceConnectWithoutContext("RequestArrived",MakeCallback(&Audit::Delivered,&a)),"missing delivery trace");
        }
        if(a.detail)Check(NodeList::GetNode(4)->GetObject<UbSwitch>()->GetQueueManager()->TraceConnectWithoutContext(
            "OutPortVoqWaitingBytes",MakeCallback(&Audit::Voq,&a)),"missing VOQ trace");
        std::vector<Ptr<UbPort>> ports;
        for(auto n=NodeList::Begin();n!=NodeList::End();++n)for(uint32_t i=0;i<(*n)->GetNDevices();++i){
            auto p=DynamicCast<UbPort>((*n)->GetDevice(i));ports.push_back(p);
            if((*n)->GetId()==4 && i>=2){
                p->SetServiceIntervals({{0,1000000,1000000000}},1000000);
                if(a.detail)Check(p->GetUbQueue()->TraceConnectWithoutContext("WaitingBytes",MakeBoundCallback(Audit::Fifo,&a,i)),"missing FIFO trace");
            }
            Check(p->TraceConnectWithoutContext("TraComEventNotify",MakeBoundCallback(Audit::Tx,&a,(*n)->GetId(),i)) &&
                p->TraceConnectWithoutContext("PktRcvNotify",MakeCallback(&Audit::Rx,&a)),"missing link trace");
        }
        for(uint32_t i=0;i<2;++i){
            UbTrafficGen::RuntimeTask t;t.taskId=100+i;t.sourceNode=i;t.destNode=shared?2:2+i;t.priority=7;
            t.dataSize=8192;t.op=UbTrafficGen::RuntimeTaskOp::MEM_STORE;
            Simulator::ScheduleNow([&,t](){apps[t.sourceNode]->GetTransferExecutor().Submit(t,[&,t](uint32_t id){
                Check(id==t.taskId && a.bytes[id]==t.dataSize && a.ready[id]<TimeNs(),"incorrect data/callback order");
                Check(a.done.emplace(id,TimeNs()).second,"duplicate completion");});});
        }
        Simulator::Stop(MilliSeconds(1));Simulator::Run();
        Check(a.done.size()==2 && a.requests.empty() && a.starts==a.arrivals,"unfinished execution");
        for(auto app:apps){auto n=app->GetNode();auto api=n->GetObject<UbController>()->GetUbFunction()->GetUbLdstApi();
            Check(n->GetObject<UbLdstInstance>()->IsIdle() && !api->GetPendingRequestCount() && !api->GetWaitingThreadCount() &&
                !app->GetTransferExecutor().GetPendingCount(),"native resource leak");}
        for(auto p:ports)Check(!p->IsBusy() && p->GetUbQueue()->IsEmpty() &&
            !p->GetNode()->GetObject<UbSwitch>()->GetQueueManager()->GetTotalOutPortBufferUsed(p->GetIfIndex()),"queue not drained");
        J result={{"passed",true},{"shared",shared},{"delivered_bytes",a.bytes},{"ready_ns",a.ready},{"done_ns",a.done},
            {"packets",a.packets},{"queues",a.queues},{"wire_and_time_hash",a.hash},{"physical_packets",a.starts}};
        std::ofstream(out/"result.json")<<result.dump(2)<<'\n';Simulator::Destroy();return 0;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';Simulator::Destroy();return 1;}
}
