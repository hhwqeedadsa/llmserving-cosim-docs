#include "ns3/ub-app.h"
#include "ns3/ub-transfer-executor.h"
#include "ns3/ub-ldst-api.h"
#include "ns3/ub-utils.h"
#include "json/json.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <set>

namespace {
using namespace ns3;
using J=nlohmann::json;
void Check(bool ok,const char* why) { if(!ok) throw std::runtime_error(why); }
uint64_t TimeNs() { return Simulator::Now().GetNanoSeconds(); }
struct Range { uint32_t task; uint64_t offset; uint32_t bytes; UbMemOperationType type; };
struct Audit {
    bool detail=true;
    std::map<uint64_t,Range> requests,responses;
    std::map<uint32_t,std::map<uint64_t,uint32_t>> delivered;
    std::map<uint32_t,uint64_t> ready;
    J created=J::array(),data=J::array(),completed=J::array(),paths=J::array();
    uint64_t sent=0,received=0,hash=14695981039346656037ull;
    void Hash(uint64_t v) { for(int i=0;i<8;++i) { hash^=v&255;hash*=1099511628211ull;v>>=8; } }
    void Created(Ptr<const Packet> p,uint32_t task,uint64_t offset,uint32_t bytes,UbMemOperationType type) {
        Check(requests.emplace(p->GetUid(),Range{task,offset,bytes,type}).second,"duplicate request");
        created.push_back({{"uid",p->GetUid()},{"task",task},{"offset",offset},{"bytes",bytes},{"time_ns",TimeNs()},{"response",false}});
    }
    void Deliver(uint64_t uid,const Range& r) {
        Check(delivered[r.task].emplace(r.offset,r.bytes).second,"duplicate useful bytes");
        ready[r.task]=TimeNs();
        data.push_back({{"uid",uid},{"task",r.task},{"offset",r.offset},{"bytes",r.bytes},{"time_ns",TimeNs()}});
    }
    void Arrived(uint64_t uid,uint32_t bytes,UbMemOperationType type) {
        const auto& r=requests.at(uid);Check(r.type==type,"request type changed");
        if(type==UbMemOperationType::STORE) { Check(bytes==r.bytes,"STORE bytes changed");Deliver(uid,r); }
    }
    void Responded(uint64_t request,Ptr<const Packet> p,UbMemOperationType type) {
        const auto r=requests.at(request);Check(request!=p->GetUid() && r.type==type,"response identity/type");
        Check(responses.emplace(p->GetUid(),r).second,"duplicate response");requests.erase(request);
        created.push_back({{"uid",p->GetUid()},{"request_uid",request},{"task",r.task},{"offset",r.offset},
            {"bytes",r.bytes},{"time_ns",TimeNs()},{"response",true}});
    }
    void ResponseArrived(uint64_t uid,uint32_t task,uint64_t offset,uint32_t bytes,UbMemOperationType type) {
        const auto r=responses.at(uid);Check(r.task==task && r.offset==offset && r.bytes==bytes && r.type==type,"response useful range");
        if(type==UbMemOperationType::LOAD) Deliver(uid,r);
        responses.erase(uid);
    }
    static void Tx(Audit* a,uint32_t node,uint32_t port,Ptr<Packet> p,Time end) {
        ++a->sent;a->Hash(TimeNs());a->Hash(node);a->Hash(port);a->Hash(end.GetNanoSeconds());
        std::vector<uint8_t> bytes(p->GetSize());p->CopyData(bytes.data(),bytes.size());
        for(auto b:bytes) { a->hash^=b;a->hash*=1099511628211ull; }
        if(!a->detail) return;
        auto copy=p->Copy();UbDatalinkHeader dl;copy->PeekHeader(dl);
        if(!dl.IsPacketUbMemHeader()) return;
        UbDatalinkPacketHeader packet;UbCna16NetworkHeader network;
        copy->RemoveHeader(packet);copy->RemoveHeader(network);
        uint32_t opcode;
        if(packet.GetACK()) { UbCompactAckTransactionHeader ack;copy->RemoveHeader(ack);opcode=ack.GetTaOpcode(); }
        else { UbCompactTransactionHeader ta;copy->RemoveHeader(ta);opcode=ta.GetTaOpcode(); }
        a->paths.push_back({{"uid",p->GetUid()},{"node",node},{"port",port},{"time_ns",TimeNs()},
            {"finish_ns",end.GetNanoSeconds()},{"packet_bytes",p->GetSize()},{"opcode",opcode},
            {"src",utils::Cna16ToNodeId(network.GetScna())},{"dst",utils::Cna16ToNodeId(network.GetDcna())},
            {"response",packet.GetACK()},{"spray",packet.GetLoadBalanceMode()}});
    }
    void Rx(Ptr<Packet>) { ++received; }
};
}
int main(int argc,char** argv) {
    namespace fs=std::filesystem;
    try {
        Check(argc==6,"Usage: native-routing-test UB_CASE NEW_OUTPUT SOURCE TARGET DETAIL_0_OR_1");
        auto input=fs::absolute(argv[1]),out=fs::absolute(argv[2]);
        const auto src=std::stoul(argv[3]),dst=std::stoul(argv[4]);
        Check(!fs::exists(out),"output exists");fs::create_directories(out);fs::current_path(out);
        for(auto name:{"network_attribute.txt","node.csv","topology.csv","routing_table.csv"}) fs::copy_file(input/name,name);
        Time::SetResolution(Time::NS);RngSeedManager::SetSeed(1);RngSeedManager::SetRun(1);
        auto u=utils::UbUtils::Get();UbEgressQueue::GetTypeId();u->SetComponentsAttribute("network_attribute.txt");
        u->CreateTraceDir();u->CreateNode("node.csv");u->CreateTopo("topology.csv");u->AddRoutingTable("routing_table.csv");
        auto node=NodeList::GetNode(src);auto app=CreateObject<UbApp>();node->AddApplication(app);
        auto api=node->GetObject<UbController>()->GetUbFunction()->GetUbLdstApi();
        auto target=NodeList::GetNode(dst)->GetObject<UbController>()->GetUbFunction()->GetUbLdstApi();
        Audit a;a.detail=std::string(argv[5])=="1";
        api->TraceConnectWithoutContext("RequestCreated",MakeCallback(&Audit::Created,&a));
        api->TraceConnectWithoutContext("ResponseArrived",MakeCallback(&Audit::ResponseArrived,&a));
        target->TraceConnectWithoutContext("RequestArrived",MakeCallback(&Audit::Arrived,&a));
        target->TraceConnectWithoutContext("ResponseCreated",MakeCallback(&Audit::Responded,&a));
        std::vector<Ptr<UbPort>> ports;
        for(auto n=NodeList::Begin();n!=NodeList::End();++n) for(uint32_t i=0;i<(*n)->GetNDevices();++i) {
            auto p=DynamicCast<UbPort>((*n)->GetDevice(i));ports.push_back(p);
            Check(p->TraceConnectWithoutContext("TraComEventNotify",MakeBoundCallback(Audit::Tx,&a,(*n)->GetId(),i)) &&
                p->TraceConnectWithoutContext("PktRcvNotify",MakeCallback(&Audit::Rx,&a)),"missing physical trace");
        }
        for(uint32_t i=0;i<2;++i) {
            UbTrafficGen::RuntimeTask t;t.taskId=100+i;t.sourceNode=src;t.destNode=dst;t.priority=7;t.dataSize=65537;
            t.op=i?UbTrafficGen::RuntimeTaskOp::MEM_LOAD:UbTrafficGen::RuntimeTaskOp::MEM_STORE;
            Simulator::ScheduleNow([&,t]() { app->GetTransferExecutor().Submit(t,[&,t](uint32_t id) {
                Check(id==t.taskId,"callback identity");uint64_t bytes=0;
                for(auto [lo,size]:a.delivered.at(id)) { Check(lo==bytes,"delivery range gap");bytes+=size; }
                Check(bytes==t.dataSize && a.ready.at(id)<=TimeNs(),"premature completion");
                a.completed.push_back({{"task",id},{"bytes",bytes},{"ready_ns",a.ready.at(id)},{"done_ns",TimeNs()}});
            }); });
        }
        Simulator::Stop(MilliSeconds(1));Simulator::Run();
        Check(a.completed.size()==2 && a.requests.empty() && a.responses.empty() && a.sent==a.received,"unfinished network");
        Check(node->GetObject<UbLdstInstance>()->IsIdle() && api->GetPendingRequestCount()==0 &&
            api->GetWaitingThreadCount()==0 && app->GetTransferExecutor().GetPendingCount()==0,"native resources not idle");
        for(auto p:ports) Check(!p->IsBusy() && p->GetUbQueue()->IsEmpty() &&
            p->GetNode()->GetObject<UbSwitch>()->GetQueueManager()->GetTotalOutPortBufferUsed(p->GetIfIndex())==0,"physical queue not idle");
        BooleanValue spray;api->GetAttribute("UsePacketSpray",spray);
        J result={{"passed",true},{"source",src},{"target",dst},{"packet_spray",spray.Get()},
            {"created",a.created},{"data_arrivals",a.data},{"completions",a.completed},{"paths",a.paths},
            {"physical_starts",a.sent},{"physical_arrivals",a.received},{"wire_and_time_hash",a.hash},
            {"no_tp_created",node->GetObject<UbController>()->GetTransportCountForTest()==0}};
        std::ofstream("result.json")<<result.dump(2)<<'\n';
        Simulator::Destroy();std::cout<<"PASS native routing runtime: requests/responses, useful ranges and drained physical network\n";return 0;
    } catch(const std::exception& e) { std::cerr<<e.what()<<'\n';Simulator::Destroy();return 1; }
}
