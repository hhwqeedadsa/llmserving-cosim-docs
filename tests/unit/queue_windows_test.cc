#include "queue_windows.hh"
#include "ns3/ub-queue-manager.h"
#include "ns3/ub-port.h"
#include <iostream>
#include <map>
#include <vector>

namespace {
void Check(bool ok,const char* why) { if(!ok) throw std::runtime_error(why); }
struct Audit {
    std::vector<uint64_t> voq,fifo;
    void Voq(uint32_t port,uint64_t bytes) { Check(port==1,"wrong VOQ port");voq.push_back(bytes); }
    void Fifo(uint64_t bytes) { fifo.push_back(bytes); }
};
}
int main() {
    using namespace moe_ub;using namespace ns3;
    try {
        std::map<uint64_t,QueueWindow> rows;
        QueueWindows q({2,13,4,{-3,0}},0,100,20,[&](auto i,const auto& r){Check(rows.emplace(i,r).second,"duplicate window");});
        q.Update(7,200,{});q.Update(10,{},0);q.Update(14,0,{});q.Finish(16);
        Check(rows.size()==3,"missing tail or no-send window");
        Check(rows[0].voq_byte_ns==600 && rows[1].voq_byte_ns==800 && rows[2].voq_byte_ns==200,"independent VOQ integral");
        Check(rows[0].fifo_byte_ns==80 && rows[1].fifo_byte_ns==20 && rows[2].fifo_byte_ns==0,"independent FIFO integral");
        Check(rows[0].voq_peak_bytes==200 && rows[2].voq_peak_bytes==200 && rows[2].fifo_peak_bytes==0,"independent peak");
        std::map<uint64_t,QueueWindow> boundary;
        QueueWindows b({0,25,10,{0,0}},0,0,0,[&](auto i,const auto& r){boundary[i]=r;});
        b.Update(10,30,{});b.Update(10,50,{});b.Update(10,20,{});b.Update(20,0,{});b.Finish(25);
        Check(boundary.size()==1 && boundary.at(1).voq_byte_ns==200 && boundary.at(1).voq_peak_bytes==50,"half-open/same-time peak");
        uint64_t emitted=0;
        QueueWindows sparse({0,INT64_MAX,1,{0,0}},0,0,0,[&](auto,const auto&){++emitted;});
        sparse.Update(INT64_MAX-1,{},9);sparse.Finish(INT64_MAX);
        Check(emitted==1,"empty window traversal or tail missing");
        Check(LocalSamplingClock{-7,500000000}.At(3)==-3,"integer local clock floor");
        Check(QueueAreaText(static_cast<unsigned __int128>(UINT64_MAX)*10)=="184467440737095516150","wide area precision");
        Audit audit;auto manager=CreateObject<UbQueueManager>();manager->SetPortsNum(2);manager->SetVLNum(8);manager->Init();
        Check(manager->TraceConnectWithoutContext("OutPortVoqWaitingBytes",MakeCallback(&Audit::Voq,&audit)),"VOQ state trace absent");
        manager->PushToInPortProcessing(0,1,7,200);
        Check(manager->GetOutPortVoqWaitingBytes(1)==0 && manager->GetTotalOutPortBufferUsed(1)==200 && audit.voq.empty(),"processing counted as ready VOQ");
        manager->MoveInPortProcessingToVoq(0,1,7,200);
        manager->PushToVoq(1,1,0,7);manager->PushToVoq(0,1,7,100);
        manager->PopFromVoq(1,1,0,7);manager->PopFromVoq(0,1,7,200);manager->PopFromVoq(0,1,7,100);
        Check(audit.voq==std::vector<uint64_t>({200,207,307,300,100,0}),"missing data/control VOQ updates");
        auto fifo=CreateObject<UbEgressQueue>();
        Check(fifo->TraceConnectWithoutContext("WaitingBytes",MakeCallback(&Audit::Fifo,&audit)),"FIFO state trace absent");
        fifo->DoEnqueue({0,7,Create<Packet>(65)});fifo->DoEnqueue({1,0,Create<Packet>(7)});
        auto in_flight=fifo->DoDequeue();(void)in_flight;
        Check(fifo->GetWaitingBytes()==7,"serializing packet counted as FIFO waiting");fifo->DoDequeue();
        Check(audit.fifo==std::vector<uint64_t>({65,72,7,0}),"FIFO packet-byte accounting");
        std::cout<<"PASS queue windows: independent area/peak, initial backlog, tail, boundaries, sparse gaps and all control/data updates\n";
        std::cout<<"QueueWindows object bytes "<<sizeof(QueueWindows)<<", QueueWindow bytes "<<sizeof(QueueWindow)<<'\n';
        Simulator::Destroy();return 0;
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';Simulator::Destroy();return 1;}
}
