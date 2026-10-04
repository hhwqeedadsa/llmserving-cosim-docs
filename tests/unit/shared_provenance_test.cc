#include "port_observation.hh"
#include "ns3/ub-utils.h"
#include "ns3/ub-transport.h"
#include "ns3/simulator.h"
#include "ns3/node-list.h"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

namespace
{
void Check(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }
template<class F> void Reject(F action)
{
    bool rejected=false;
    try { action(); } catch (const std::runtime_error&) { rejected=true; }
    Check(rejected,"expected provenance rejection");
}
ns3::Ptr<ns3::Packet> MakeNativePacket(ns3::Ptr<ns3::UbTransportChannel> tp,uint32_t task)
{
    auto segment=ns3::CreateObject<ns3::UbWqeSegment>(0,1,0,0,1,ns3::TaOpcode::TA_OPCODE_WRITE,
                                                   1,static_cast<ns3::UbPriority>(7),task);
    segment->SetWqeSize(1);segment->SetTaskByteOffset(0);
    return tp->GenDataPacket(segment,1,1,1);
}
}
int main(int argc,char** argv)
{
    namespace fs=std::filesystem;
    auto* utils=utils::UbUtils::Get();
    try
    {
        Check(argc==3,"Usage: cosim-provenance-test UB_CASE NEW_RUN_DIR");
        const auto input=fs::absolute(argv[1]),out=fs::absolute(argv[2]);
        Check(!fs::exists(out) && fs::create_directory(out),"Run directory must be new");
        fs::current_path(out);fs::create_directory("network");
        for(const auto* name:{"network_attribute.txt","node.csv","topology.csv","routing_table.csv","transport_channel.csv"})
            fs::copy_file(input/name,fs::path("network")/name);
        ns3::Time::SetResolution(ns3::Time::NS);
        utils->SetComponentsAttribute("network/network_attribute.txt");
        ns3::GlobalValue::Bind("UB_QUEUE_TRACE_ENABLE",ns3::BooleanValue(false));
        ns3::GlobalValue::Bind("UB_PARSE_TRACE_ENABLE",ns3::BooleanValue(false));
        utils->CreateTraceDir();utils->CreateNode("network/node.csv");utils->CreateTopo("network/topology.csv");
        utils->AddRoutingTable("network/routing_table.csv");utils->CreateTp("network/transport_channel.csv");
        {
            auto source=ns3::NodeList::GetNode(0)->GetObject<ns3::UbController>();
            auto target=ns3::NodeList::GetNode(1)->GetObject<ns3::UbController>();
            auto tp=source->GetTpnMap().begin()->second;
            const auto tpn=source->GetTpnMap().begin()->first;
            auto target_tp=target->GetTpnMap().begin()->second;
            const auto tp_refs=tp->GetReferenceCount();
            Reject([&]{moe_ub::WriteProvenance duplicate({source,source});});
            MakeNativePacket(tp,999); // Failed construction must not leave a subscription.
            auto owner=std::make_shared<moe_ub::WriteProvenance>(std::vector{source,target});
            Check(tp->GetReferenceCount()==tp_refs,"source subscription retained native TP");
            owner->Register({{1001,7,"first",1,2,0,1,17,1}});
            auto first=std::make_unique<moe_ub::PortObservation>(moe_ub::ObservationConfig{3,0,0,30000,1000,true},owner);
            auto other=std::make_unique<moe_ub::PortObservation>(moe_ub::ObservationConfig{2,0,0,30000,1000,false},owner);
            Check(first->Provenance()==other->Provenance(),"ports did not share source owner");
            const auto* descriptor=&first->Provenance()->Ledger().Descriptor(7);
            Check(descriptor==&other->Provenance()->Ledger().Descriptor(7),"ports copied descriptors");
            const auto first_packet=MakeNativePacket(tp,7);
            Check(owner->Ledger().Lookup(first_packet,1).task_id==7 && owner->Ledger().CreatedNotifications()==1,
                  "one native source event was delivered more than once");
            std::weak_ptr<moe_ub::WriteProvenance> lifetime=owner;
            owner.reset();first.reset();
            Check(!lifetime.expired(),"removing one port destroyed the shared owner");
            lifetime.lock()->Register({{1002,8,"second",1,2,0,1,18,1}});
            const auto second_packet=MakeNativePacket(tp,8);
            const auto& ledger=other->Provenance()->Ledger();
            Check(ledger.Lookup(first_packet,1).task_id==7 && ledger.Lookup(second_packet,1).task_id==8 &&
                  ledger.TransferCount()==2 && ledger.PacketCount()==2 && ledger.CreatedNotifications()==2 &&
                  &ledger.Descriptor(7)==descriptor,"remaining port lost subscription or old graph records");
            auto audit=other->Provenance()->Audit();
            // A native TP removed from the controller may still be held by a late
            // event. Removing the final port must leave its callback memory safe.
            source->DestroyTp(tpn);
            other.reset();
            Check(lifetime.expired(),"callback retained provenance owner or ports");
            MakeNativePacket(tp,7); // Retained ledger accepts a known task after owner destruction.
            Reject([&]{MakeNativePacket(tp,999);}); // Proves the detached TP callback still runs.
            MakeNativePacket(target_tp,999); // TP still in its map must have been disconnected.
            audit["remaining_port_receives_after_peer_destruction"]=true;
            audit["removed_tp_late_callback_validated"]=true;
            audit["mapped_tp_callback_disconnected"]=true;
            audit["native_tp_not_retained"]=true;
            audit["shared_owner_released"]=true;
            audit["network_transfers_executed"]=false;
            std::ofstream("lifecycle.json")<<audit.dump(2)<<'\n';
        }
        utils->Destroy();ns3::Simulator::Destroy();
        std::cout<<"PASS: shared source lifetime, independent port destruction, native callback multiplicity and late removed TP\n";
        return 0;
    }
    catch(const std::exception& e)
    {
        std::cerr<<e.what()<<'\n';utils->Destroy();ns3::Simulator::Destroy();return 1;
    }
}
