#pragma once
#include "result_records.hh"
#include "queue_sources.hh"
#include "ns3/ub-port.h"
#include "ns3/ub-queue-manager.h"
#include "ns3/ub-switch.h"
#include "ns3/ub-flow-control.h"
#include "ns3/node-list.h"
#include "ns3/simulator.h"
#include <algorithm>
#include <map>
#include <memory>

namespace moe_ub {
// Exact passive events for mechanism verification. Packet UIDs are private
// evidence; callers must prepare an explicit restricted input for diagnosis.
class NetworkFacts {
    using Json=nlohmann::json;
    struct Port {
        NetworkFacts* owner;
        uint32_t node,id;
        ns3::Ptr<ns3::UbPort> port;
        ns3::Ptr<ns3::UbCbfc> fc;
        bool connected=false;
        uint64_t voq=0,fifo=0,tx_until=0;
        void Emit(const char* event,Json data) { owner->Append(node,id,event,std::move(data)); }
        void Tx(ns3::Ptr<ns3::Packet> p,ns3::Time duration) {
            const auto now=uint64_t(ns3::Simulator::Now().GetNanoSeconds());
            tx_until=std::max(tx_until,now+uint64_t(duration.GetNanoSeconds()));
            Emit("tx",{{"packet_uid",p->GetUid()},{"packet_bytes",p->GetSize()},
                {"completion_delay_ns",duration.GetNanoSeconds()},
                {"rate_bps",port->GetEffectiveDataRate().GetBitRate()}});
        }
        void Enqueue(ns3::Ptr<const ns3::Packet> p,uint32_t bytes) {
            Emit("fifo_enqueue",{{"packet_uid",p->GetUid()},{"packet_bytes",p->GetSize()},
                {"fifo_bytes",bytes}});
        }
        void Dequeue(ns3::Ptr<const ns3::Packet> p,uint32_t bytes) {
            Emit("fifo_dequeue",{{"packet_uid",p->GetUid()},{"packet_bytes",p->GetSize()},
                {"fifo_bytes",bytes}});
        }
        void Fifo(uint64_t bytes) { fifo=bytes;Emit("fifo",{{"bytes",bytes}}); }
        void Voq(uint64_t bytes) { voq=bytes;Emit("voq",{{"bytes",bytes}}); }
        void Credit(uint32_t vl,int32_t available,int32_t delta) {
            Emit("credit",{{"vl",vl},{"available_cells",available},{"delta_cells",delta}});
        }
        void Decision(ns3::Ptr<ns3::UbIngressQueue> q,uint32_t bytes,int32_t available,
                      uint32_t needed,bool blocked) {
            if(!owner->InRange()) return;
            // GetNextPacket() consumes a packet; only read Front() on a known
            // nonempty VOQ. Generated native/TP queue identities stay unknown.
            Json uid=nullptr;
            auto voq=ns3::DynamicCast<ns3::UbPacketQueue>(q);
            if(voq) uid=voq->Front()->GetUid();
            Emit("credit_decision",{{"in_port",q->GetInPortId()},
                {"vl",q->GetIngressPriority()},{"queue_type",voq?"VOQ":"generated"},
                {"packet_uid",uid},{"head_bytes",bytes},{"required_cells",needed},
                {"available_cells",available},{"blocked",blocked}});
        }
        void Connect() {
            connected=true;
            auto queue=port->GetUbQueue();
            QueueRequire(port->TraceConnectWithoutContext("TraComEventNotify",ns3::MakeCallback(&Port::Tx,this)) &&
                queue->TraceConnectWithoutContext("UbEnqueue",ns3::MakeCallback(&Port::Enqueue,this)) &&
                queue->TraceConnectWithoutContext("UbDequeue",ns3::MakeCallback(&Port::Dequeue,this)) &&
                queue->TraceConnectWithoutContext("WaitingBytes",ns3::MakeCallback(&Port::Fifo,this)) &&
                fc->TraceConnectWithoutContext("CreditState",ns3::MakeCallback(&Port::Credit,this)) &&
                fc->TraceConnectWithoutContext("CreditDecision",ns3::MakeCallback(&Port::Decision,this)),
                "Cannot connect passive network facts");
        }
        void Disconnect() {
            if(!connected) return;
            connected=false;
            auto queue=port->GetUbQueue();
            port->TraceDisconnectWithoutContext("TraComEventNotify",ns3::MakeCallback(&Port::Tx,this));
            queue->TraceDisconnectWithoutContext("UbEnqueue",ns3::MakeCallback(&Port::Enqueue,this));
            queue->TraceDisconnectWithoutContext("UbDequeue",ns3::MakeCallback(&Port::Dequeue,this));
            queue->TraceDisconnectWithoutContext("WaitingBytes",ns3::MakeCallback(&Port::Fifo,this));
            fc->TraceDisconnectWithoutContext("CreditState",ns3::MakeCallback(&Port::Credit,this));
            fc->TraceDisconnectWithoutContext("CreditDecision",ns3::MakeCallback(&Port::Decision,this));
        }
        ~Port() { Disconnect(); }
    };
public:
    static void Validate(const Json& p,uint64_t max_ns) {
        QueueRequire(p.is_object() && p.size()==4 && p.at("schema_version")=="moe-ub-network-facts/1" &&
            p.at("ports").is_array() && !p.at("ports").empty() && p.at("ports").size()<=4096,
            "Invalid network facts policy/port budget");
        QueueRequire(p.at("end_ns").is_number_unsigned() && p.at("end_ns").get<uint64_t>()>0 &&
            p.at("end_ns").get<uint64_t>()<=max_ns && p.at("max_records").is_number_unsigned() &&
            p.at("max_records").get<uint64_t>()>=2*p.at("ports").size(),"Invalid network facts limits");
        std::set<std::pair<uint32_t,uint32_t>> keys;
        for(const auto& row:p.at("ports")) {
            QueueRequire(row.is_object() && row.size()==2 && row.at("node_id").is_number_unsigned() &&
                row.at("port_id").is_number_unsigned() && row.at("node_id").get<uint64_t>()<=UINT32_MAX &&
                row.at("port_id").get<uint64_t>()<=UINT32_MAX,"Invalid network facts port");
            QueueRequire(keys.emplace(row.at("node_id"),row.at("port_id")).second,"Repeated network facts port");
        }
    }
    NetworkFacts(ResultRecords& records,Json policy):records_(records),policy_(std::move(policy)) {
        QueueRequire(ns3::Simulator::Now().GetNanoSeconds()==0 && records.Bounded(),
            "Network facts require zero-time attachment and bounded storage");
        for(const auto& row:policy_.at("ports")) {
            const uint32_t node=row.at("node_id"),id=row.at("port_id");
            QueueRequire(node<ns3::NodeList::GetNNodes(),"Unknown network facts node");
            auto n=ns3::NodeList::GetNode(node);auto sw=n->GetObject<ns3::UbSwitch>();
            QueueRequire(sw && id<n->GetNDevices(),"Unknown network facts outlet");
            auto port=ns3::DynamicCast<ns3::UbPort>(n->GetDevice(id));
            QueueRequire(port && port->GetFlowControl()->GetFcType()==ns3::FcType::CBFC,
                "Network facts currently support exclusive CBFC only");
            auto value=std::make_unique<Port>();auto* p=value.get();
            p->owner=this;p->node=node;p->id=id;p->port=port;
            p->fc=ns3::DynamicCast<ns3::UbCbfc>(port->GetFlowControl());
            p->voq=sw->GetQueueManager()->GetOutPortVoqWaitingBytes(id);
            p->fifo=port->GetUbQueue()->GetWaitingBytes();
            p->Emit("initial",{{"voq_bytes",p->voq},{"fifo_bytes",p->fifo},
                {"credit_cell_bytes",p->fc->GetCreditCellBytes()},{"credits",p->fc->GetTransmitCredits()}});
            ports_.emplace(std::make_pair(node,id),std::move(value));
            p->Connect();voq_sources_.Watch(node,id,[p](uint64_t b){p->Voq(b);});
        }
    }
    ~NetworkFacts() {
        for(auto& [key,p]:ports_) { voq_sources_.Unwatch(key.first,key.second);p->Disconnect(); }
    }
    Json Finish() {
        const auto now=uint64_t(ns3::Simulator::Now().GetNanoSeconds());
        const auto end=std::min(now,policy_.at("end_ns").get<uint64_t>());
        bool quiet=true;
        for(auto& [key,p]:ports_) {
            quiet=quiet && p->voq==0 && p->fifo==0 && p->tx_until<=now;
            // If the requested end precedes execution end, state at that
            // boundary is recovered from the complete prior event stream.
            Append(key.first,key.second,"final",{{"boundary_state_from_prior_events",true}},end);
        }
        return {{"schema_version","moe-ub-network-facts-capture/1"},{"policy",policy_},
            {"start_ns",0},{"end_ns",end},{"execution_end_ns",now},{"records",count_},
            {"complete",true},{"dropped_records",0},{"all_selected_ports_quiescent_at_execution_end",quiet},
            {"access_scope","internal_truth_prepare_restricted_inputs_before_diagnosis"},
            {"credit_scope","actual exclusive CBFC probes; bypassed control frames absent; generated head UID unknown"}};
    }
private:
    bool InRange() const { return uint64_t(ns3::Simulator::Now().GetNanoSeconds())<policy_.at("end_ns").get<uint64_t>(); }
    void Append(uint32_t node,uint32_t port,const char* event,Json data,std::optional<uint64_t> at={}) {
        if(!at && !InRange()) return;
        QueueRequire(count_<policy_.at("max_records").get<uint64_t>(),"Network facts budget exceeded; capture incomplete");
        records_.Add("network_facts",{{"sequence",++count_},{"node_id",node},{"port_id",port},
            {"time_ns",at.value_or(uint64_t(ns3::Simulator::Now().GetNanoSeconds()))},
            {"event",event},{"data",std::move(data)}});
    }
    ResultRecords& records_;Json policy_;uint64_t count_=0;
    std::map<std::pair<uint32_t,uint32_t>,std::unique_ptr<Port>> ports_;
    VoqSources voq_sources_;
};
}
