#pragma once
#include "layer_plan.hh"
#include <map>
#include <string>

namespace moe_ub {
// Bounded per-operation summaries of real node times and adapter callbacks.
// No packet times, dependencies, events or protocol state are modified.
class OperationStages {
public:
    OperationStages(const LayerPlan&,const ExecutionTimes&,const nlohmann::json& endpoints,uint64_t offset);
    void Transfer(const UbWriteTransfer&);
    void Local(const LocalDelivery&);
    nlohmann::json Rows() const;
private:
    struct Stage { uint64_t time=0,count=0; };
    struct Operation { uint64_t id;std::map<std::string,Stage> stages; };
    using Key=std::pair<std::string,int>;
    void Add(const Key&,const std::string&,uint64_t,bool first=false);
    std::string graph_;
    std::map<Key,Operation> operations_;
};
}
