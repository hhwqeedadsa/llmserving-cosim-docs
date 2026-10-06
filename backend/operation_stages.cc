#include "operation_stages.hh"
#include <algorithm>
#include <stdexcept>

namespace moe_ub {
using Json=nlohmann::json;
OperationStages::OperationStages(const LayerPlan& plan,const ExecutionTimes& timing,const Json& endpoints,uint64_t offset)
    :graph_(plan.data.at("graph_id")) {
    std::map<std::pair<int,uint64_t>,uint64_t> ends;
    for(const auto& row:endpoints) ends[{row.at("rank_id"),row.at("local_record_id")}]=row.at("end_local_ns");
    std::map<int,const Json*> graphs;
    for(const auto& g:plan.data.at("rank_graphs")) graphs[g.at("rank_id")]=&g;
    for(const auto& op:LayerOperationRegistry(plan)) for(const auto& endpoint:op.at("endpoint_records")) {
        const int rank=endpoint.at("rank_id");const uint64_t id=endpoint.at("local_record_id");
        const Key key{op.at("operation_id"),rank};
        if(!operations_.emplace(key,Operation{id+offset,{}}).second) throw std::runtime_error("Repeated stage operation");
        Add(key,"operation_complete",ends.at({rank,id}));
        if(plan.data.at("communication_template")=="kv_direct_v1") continue;
        const auto& rg=*graphs.at(rank);
        const auto& g=plan.data.contains("layers")?rg.at("layer_phases").at((id-1)/2):rg;
        auto node=[&](const Json& value,const std::string& prefix) {
            if(value.is_null()) return;
            const auto nodeId=value.get<uint64_t>();
            if(plan.nodes.at({rank,nodeId}).kind!="COMP") throw std::runtime_error("Preparation stage is not COMP");
            const auto& t=timing.at({rank,nodeId});
            if(!t.started || !t.finished) throw std::runtime_error("Incomplete preparation stage");
            Add(key,prefix+"_start",t.start_ns);Add(key,prefix+"_end",t.end_ns);
        };
        const std::string kind=op.at("kind");
        node(g.at("phase_nodes").at(kind=="dispatch"?"prepare":"local_merge"),"prepare");
        if(g.contains("local_prepare_nodes")) node(g.at("local_prepare_nodes").at(kind),"local_prepare");
    }
}
void OperationStages::Add(const Key& key,const std::string& stage,uint64_t time,bool first) {
    auto& row=operations_.at(key).stages[stage];
    row.time=!row.count?time:(first?std::min(row.time,time):std::max(row.time,time));++row.count;
}
void OperationStages::Transfer(const UbWriteTransfer& transfer) {
    const auto& d=transfer.Descriptor();
    if(d.graph_id!=graph_) throw std::runtime_error("Stage transfer belongs to another graph");
    const Key sender{d.operation_id,d.src_rank},consumer{d.operation_id,d.Consumer()};
    Add(sender,"request_submit_first",transfer.SendStartNs().value(),true);
    Add(sender,"request_submit_last",transfer.SendStartNs().value());
    Add(sender,"initiator_resources_complete",transfer.TransactionDoneNs().value());
    Add(consumer,"data_ready",transfer.Completion().DataReadyNs().value());
}
void OperationStages::Local(const LocalDelivery& delivery) {
    const auto& d=delivery.Descriptor();const auto row=delivery.Result();
    if(d.graph_id!=graph_) throw std::runtime_error("Stage local delivery belongs to another graph");
    const Key sender{d.operation_id,d.src_rank},consumer{d.operation_id,d.dst_rank};
    Add(sender,"local_publish_first",row.at("ready_ns"),true);
    Add(sender,"local_publish_last",row.at("ready_ns"));
    Add(sender,"local_resources_complete",row.at("send_callback_ns"));
    Add(consumer,"data_ready",row.at("ready_ns"));
}
Json OperationStages::Rows() const {
    Json result=Json::array();
    for(const auto& [key,op]:operations_) for(const auto& [name,stage]:op.stages)
        result.push_back({{"graph_id",graph_},{"operation_id",key.first},{"rank_id",key.second},
            {"local_record_id",op.id},{"stage",name},{"time_ns",stage.time},{"event_count",stage.count}});
    return result;
}
}
