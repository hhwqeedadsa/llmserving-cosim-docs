#include "ns3/ub-flow-control.h"
#include "ns3/ub-queue-manager.h"
#include "ns3/packet.h"
#include "ns3/ub-datalink.h"
#include <iostream>
#include <stdexcept>

namespace {
void Check(bool v,const char* why) { if(!v) throw std::runtime_error(why); }
struct Probe {
    unsigned decisions=0,changes=0;
    bool blocked=false;
    int32_t available=0;
    void Decision(ns3::Ptr<ns3::UbIngressQueue>,uint32_t bytes,int32_t credit,uint32_t needed,bool limited) {
        ++decisions;blocked=limited;available=credit;
        Check(bytes==161 && needed==2 && limited==(credit<2),"Head-size eligibility trace differs");
    }
    void Credit(uint32_t vl,int32_t level,int32_t delta) {
        ++changes;Check(vl==1 && level==1 && delta==-1,"Credit consume trace differs");
    }
};
}
int main() {
    try {
        auto fc=ns3::CreateObject<ns3::UbCbfc>();
        fc->Init(20,8,4,32,2,1,0,0);
        auto q=ns3::CreateObject<ns3::UbPacketQueue>();
        q->SetInPortId(1);q->SetOutPortId(0);q->SetIngressPriority(1);
        auto head=ns3::Create<ns3::Packet>(161);q->Push(head);
        Probe observer;
        Check(fc->TraceConnectWithoutContext("CreditDecision",ns3::MakeCallback(&Probe::Decision,&observer)),"Missing decision trace");
        Check(fc->TraceConnectWithoutContext("CreditState",ns3::MakeCallback(&Probe::Credit,&observer)),"Missing state trace");
        Check(!fc->IsFcLimited(q),"Two cells must serve a 161-byte head");
        auto other=ns3::Create<ns3::Packet>(1);
        ns3::UbDatalinkPacketHeader header;header.SetPacketVL(1);other->AddHeader(header);
        Check(fc->CbfcConsumeCrd(other),"Independent packet must consume one cell");
        Check(fc->IsFcLimited(q),"Nonzero one-cell credit must block a two-cell head");
        Check(q->Front()==head && q->GetNextPacketSize()==161,"Observation consumed the head");
        Check(observer.decisions==2 && observer.changes==1 && observer.blocked && observer.available==1,
            "Missing actual eligibility/credit observation");
        Check(fc->GetCreditCellBytes()==160 && fc->GetTransmitCredits().at(1)==1,"Initial-state accessor differs");
        fc->TraceDisconnectWithoutContext("CreditDecision",ns3::MakeCallback(&Probe::Decision,&observer));
        fc->TraceDisconnectWithoutContext("CreditState",ns3::MakeCallback(&Probe::Credit,&observer));
        Check(fc->IsFcLimited(q) && observer.decisions==2,"Disconnected observer remains active");
        std::cout<<"network facts credit geometry/non-consuming observer checks passed\n";
        return 0;
    } catch(const std::exception& e) { std::cerr<<e.what()<<'\n';return 1; }
}
