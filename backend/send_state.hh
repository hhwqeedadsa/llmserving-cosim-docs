#pragma once
#include "result_records.hh"
#include <map>
#include <tuple>
#include <vector>

namespace moe_ub {
// One current state per rank. No events, packet annotations or scheduler writes.
// Changes stream through the existing bounded store; same-time order is kept.
class SendStateRecords {
    using Json = nlohmann::json;
    using Values = std::tuple<uint64_t,uint32_t,uint32_t>;
    struct State { uint64_t sequence=0,time=0; Values values{0,0,0}; };
public:
    static void ValidatePolicy(const Json& policy, size_t ranks) {
        if(!policy.is_object() || policy.size()!=3 || policy.at("schema_version")!="moe-ub-send-state-config/1")
            throw std::runtime_error("Invalid send-state policy");
        for(const auto* key:{"max_records_per_rank","max_total_records"})
            if(!policy.at(key).is_number_unsigned() || policy.at(key).get<uint64_t>()<2 || policy.at(key).get<uint64_t>()>INT64_MAX)
                throw std::runtime_error("Invalid send-state record limit");
        if(policy.at("max_records_per_rank")>policy.at("max_total_records") ||
           policy.at("max_total_records").get<uint64_t>()/2<ranks)
            throw std::runtime_error("Send-state budget cannot cover all rank boundaries");
    }
    SendStateRecords(ResultRecords& records,const Json& policy,const std::vector<int>& ranks,uint64_t end)
        :records_(records),policy_(policy),end_(end) {
        ValidatePolicy(policy,ranks.size());
        if(!records.Bounded() || end==0 || end>INT64_MAX) throw std::runtime_error("Send-state capture requires bounded records and positive coverage");
        for(int rank:ranks) if(!state_.emplace(rank,State{}).second) throw std::runtime_error("Duplicate send-state rank");
    }
    void Observe(int rank,uint64_t time,uint64_t ready,uint32_t active,uint32_t capacity) {
        auto& state=state_.at(rank);
        if(finished_ || time<state.time || time>end_ || active>capacity || (!capacity && ready) || ready>INT64_MAX)
            throw std::runtime_error("Invalid observed send state");
        if(!state.sequence && (time!=0 || ready || active || capacity))
            throw std::runtime_error("Send-state initial snapshot must precede slot configuration");
        const Values values{ready,active,capacity};
        if(state.sequence && state.values==values) return;
        Emit(rank,time,values,state.sequence?"update":"initial");
    }
    Json Finish() {
        if(finished_) throw std::runtime_error("Send-state capture already finished");
        Json ranks=Json::array();
        for(const auto& [rank,state]:state_) {
            if(!state.sequence || std::get<0>(state.values) || std::get<1>(state.values))
                throw std::runtime_error("Send-state capture did not drain");
            Emit(rank,end_,state.values,"final");
            ranks.push_back({{"rank_id",rank},{"records",state.sequence}});
        }
        finished_=true;
        return {{"schema_version","moe-ub-send-state-capture/1"},{"policy",policy_},
            {"start_ns",0u},{"end_ns",end_},{"complete",true},{"dropped_records",0u},
            {"records",count_},{"ranks",ranks}};
    }
private:
    void Emit(int rank,uint64_t time,const Values& values,const char* kind) {
        auto& state=state_.at(rank);
        if(state.sequence>=policy_.at("max_records_per_rank").get<uint64_t>() || count_>=policy_.at("max_total_records").get<uint64_t>())
            throw std::runtime_error("Send-state record budget exceeded");
        records_.Add("send_state",{{"rank_id",rank},{"sequence",state.sequence+1},{"time_ns",time},
            {"event_kind",kind},{"ready_send_count",std::get<0>(values)},
            {"active_send_count",std::get<1>(values)},{"send_slot_capacity",std::get<2>(values)}});
        ++state.sequence;++count_;state.time=time;state.values=values;
    }
    ResultRecords& records_;
    Json policy_;
    uint64_t end_,count_=0;
    bool finished_=false;
    std::map<int,State> state_;
};
}
