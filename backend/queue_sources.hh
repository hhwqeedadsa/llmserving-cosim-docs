#pragma once
#include "queue_windows.hh"
#include "ns3/ub-queue-manager.h"
#include "ns3/ub-switch.h"
#include "ns3/node-list.h"
#include <map>

namespace moe_ub {
// Subscribe once per switch, then dispatch to its selected port. Registering a
// full fabric must not invoke every port observer for every queue change.
class VoqSources {
    using Consumer=std::function<void(uint64_t)>;
    struct Source { ns3::Ptr<ns3::UbQueueManager> manager;std::map<uint32_t,Consumer> ports; };
    std::map<uint32_t,Source> sources_;
    static void Changed(VoqSources* self,uint32_t node,uint32_t port,uint64_t bytes) {
        const auto& ports=self->sources_.at(node).ports;
        const auto found=ports.find(port);if(found!=ports.end()) found->second(bytes);
    }
public:
    ~VoqSources() {
        for(const auto& [node,source]:sources_)
            source.manager->TraceDisconnectWithoutContext("OutPortVoqWaitingBytes",ns3::MakeBoundCallback(Changed,this,node));
    }
    void Watch(uint32_t node,uint32_t port,Consumer consumer) {
        auto found=sources_.find(node);
        if(found==sources_.end()) {
            auto manager=ns3::NodeList::GetNode(node)->GetObject<ns3::UbSwitch>()->GetQueueManager();
            found=sources_.emplace(node,Source{manager,{}}).first;
            QueueRequire(manager->TraceConnectWithoutContext("OutPortVoqWaitingBytes",ns3::MakeBoundCallback(Changed,this,node)),"Cannot observe ready VOQ");
        }
        QueueRequire(found->second.ports.emplace(port,std::move(consumer)).second,"Repeated VOQ subscription");
    }
    void Unwatch(uint32_t node,uint32_t port) {
        auto found=sources_.find(node);if(found==sources_.end()) return;
        found->second.ports.erase(port);
        if(found->second.ports.empty()) {
            found->second.manager->TraceDisconnectWithoutContext("OutPortVoqWaitingBytes",ns3::MakeBoundCallback(Changed,this,node));
            sources_.erase(found);
        }
    }
};
}
