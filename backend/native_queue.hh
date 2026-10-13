#pragma once
#include "result_records.hh"
#include "ub_write_transfer.hh"
#include "ns3/node-list.h"
#include "ns3/ub-controller.h"
#include "ns3/ub-function.h"
#include "ns3/ub-ldst-api.h"
#include "ns3/ub-ldst-instance.h"
#include "ns3/ub-ldst-thread.h"
#include "ns3/simulator.h"
#include "ns3/uinteger.h"

namespace moe_ub
{
// Internal truth only. The listener copies state at existing issue branches;
// it does not schedule work, copy packets, consume RNG, or change resource use.
class NativeQueueRecords
{
    using Json = nlohmann::json;
  public:
    static void Validate(const Json& p, const std::vector<int>& ranks, uint64_t end)
    {
        const bool audit=p.value("schema_version","")=="moe-ub-native-queue-config/2";
        if (!p.is_object() || p.size()!=(audit?6:5) || (!audit && p.at("schema_version")!="moe-ub-native-queue-config/1") ||
            !p.at("rank_ids").is_array() || p.at("rank_ids").empty())
            throw std::runtime_error("Invalid native queue capture policy");
        std::set<int> selected;
        for (const auto& rank:p.at("rank_ids"))
            if (!rank.is_number_integer() || !selected.insert(rank.get<int>()).second ||
                std::find(ranks.begin(),ranks.end(),rank.get<int>())==ranks.end())
                throw std::runtime_error("Invalid native queue rank selection");
        for (auto key:{"end_ns","max_records","max_snapshot_items"})
            if (!p.at(key).is_number_unsigned() || p.at(key).get<uint64_t>()==0 || p.at(key).get<uint64_t>()>INT64_MAX)
                throw std::runtime_error("Invalid native queue limit");
        if(audit && (!p.at("max_request_records").is_number_unsigned() ||
                     p.at("max_request_records").get<uint64_t>()==0 || p.at("max_request_records").get<uint64_t>()>INT64_MAX))
            throw std::runtime_error("Invalid native request audit budget");
        if (p.at("end_ns").get<uint64_t>()>end || p.at("max_snapshot_items").get<uint64_t>()>65536 ||
            p.at("max_records").get<uint64_t>()<2*selected.size())
            throw std::runtime_error("Native queue coverage exceeds capture budget");
    }
    NativeQueueRecords(ResultRecords& records, Json policy, const std::map<int,uint32_t>& nodes)
        : records_(records), policy_(std::move(policy))
    {
        if(policy_.contains("max_request_records")) for(const auto& [rank,id]:nodes) {
            auto api=ns3::NodeList::GetNode(id)->GetObject<ns3::UbController>()->GetUbFunction()->GetUbLdstApi();
            if(!api->TraceConnect("RequestCreated",std::to_string(id),ns3::MakeCallback(&NativeQueueRecords::Request,this)))
                throw std::runtime_error("Missing native request creation trace");
            apis_.emplace(id,api);
        }
        for (int rank:policy_.at("rank_ids")) {
            auto node=ns3::NodeList::GetNode(nodes.at(rank)); rank_by_node_[node->GetId()]=rank;
            auto instance=node->GetObject<ns3::UbLdstInstance>();
            for (uint32_t id=0;id<instance->GetThreadCount();++id) {
                auto thread=instance->GetLdstThread(id); threads_.push_back(thread);
                if (!thread->TraceConnectWithoutContext("QueueState",ns3::MakeCallback(&NativeQueueRecords::Observe,this)))
                    throw std::runtime_error("Missing native queue trace");
                Observe(thread,"initial",nullptr,0,0);
            }
        }
    }
    ~NativeQueueRecords()
    {
        for(const auto& [id,api]:apis_)
            api->TraceDisconnect("RequestCreated",std::to_string(id),ns3::MakeCallback(&NativeQueueRecords::Request,this));
        for (auto thread:threads_)
            thread->TraceDisconnectWithoutContext("QueueState",ns3::MakeCallback(&NativeQueueRecords::Observe,this));
    }
    void Register(const std::vector<WriteDescriptor>& descriptors)
    {
        for (const auto& d:descriptors) if (d.operation!=TransferOperation::Write &&
                (rank_by_node_.count(d.SourceNode()) || (!apis_.empty() && d.operation==TransferOperation::Store))) {
            if ((rank_by_node_.count(d.SourceNode()) && ++queue_identities_>policy_.at("max_records").get<uint64_t>()) ||
                (!apis_.empty() && d.operation==TransferOperation::Store &&
                 ++store_identities_>policy_.at("max_request_records").get<uint64_t>()))
                throw std::runtime_error("Native queue identity budget exceeded");
            if (!identities_.emplace(std::make_pair(d.SourceNode(),d.task_id),Json{{"graph_id",d.graph_id},
                {"transfer_id",d.transfer_id},{"send_node_id",d.send_node_id},{"src_rank",d.src_rank},
                {"dst_rank",d.dst_rank},{"operation_id",d.operation_id},
                {"producer",d.native_producer?Json(*d.native_producer):Json(nullptr)}}).second)
                throw std::runtime_error("Duplicate native queue task identity");
        }
    }
    Json Finish()
    {
        const uint64_t now=ns3::Simulator::Now().GetNanoSeconds();
        const auto end=std::min<uint64_t>(now,policy_.at("end_ns").get<uint64_t>());
        Json workers=Json::array();
        for (auto thread:threads_) {
            workers.push_back({{"rank_id",rank_by_node_.at(thread->GetNodeId())},{"node_id",thread->GetNodeId()},
                {"thread_id",thread->GetThreadId()}});
            if (now<=end) { Observe(thread,"final",nullptr,0,0); continue; }
            auto row=last_.at({thread->GetNodeId(),thread->GetThreadId()});
            row["pending"]=last_pool_.at(thread->GetNodeId());
            row["event"]="final"; row["time_ns"]=end; row["affected"]=nullptr; Append(std::move(row));
        }
        Json result={{"schema_version","moe-ub-native-queue-capture/1"},{"policy",policy_},{"start_ns",0},
            {"end_ns",end},{"complete",true},{"dropped_records",0},{"records",count_},{"workers",workers},
            {"access_scope","internal_truth_not_observed"},{"state_phase","after_queue_issue_block_or_response_resource_release"}};
        if(!apis_.empty()) result["request_audit"]={{"schema_version","moe-ub-native-request-audit/1"},
            {"scope","all_native_STORE_requests_entire_execution"},{"records",request_count_},
            {"end_ns",now},{"complete",true},{"dropped_records",0}};
        return result;
    }
  private:
    void Request(std::string context,ns3::Ptr<const ns3::Packet>,uint32_t task,uint64_t offset,
                 uint32_t bytes,ns3::UbMemOperationType type)
    {
        if(type!=ns3::UbMemOperationType::STORE) return;
        if(request_count_>=policy_.at("max_request_records").get<uint64_t>())
            throw std::runtime_error("Native request audit budget exceeded; no complete audit");
        const auto node=uint32_t(std::stoul(context));
        records_.Add("native_requests",{{"sequence",++request_count_},{"time_ns",ns3::Simulator::Now().GetNanoSeconds()},
            {"node_id",node},{"identity",identities_.at({node,task})},{"operation","STORE"},
            {"offset",offset},{"bytes",bytes}});
    }
    Json Task(ns3::Ptr<const ns3::UbLdstTaskSegment> segment) const
    {
        if (!segment) return nullptr;
        const auto key=std::make_pair(segment->GetSrc(),uint64_t(segment->GetTaskId()));
        return {{"task_id",segment->GetTaskId()},{"segment_id",segment->GetTaskSegmentId()},
            {"thread_id",segment->GetThreadId()},{"operation",segment->GetType()==ns3::UbMemOperationType::LOAD?"LOAD":"STORE"},
            {"dst_node",segment->GetDest()},{"segment_offset",segment->GetTaskOffset()},
            {"segment_bytes",segment->GetSize()},{"remaining_bytes",segment->GetBytesLeft()},
            {"producer",segment->GetProducer()?Json(*segment->GetProducer()):Json(nullptr)},
            {"identity",identities_.at(key)}};
    }
    void Append(Json row)
    {
        if (count_>=policy_.at("max_records").get<uint64_t>())
            throw std::runtime_error("Native queue record budget exceeded; no complete capture");
        row["sequence"]=++count_; records_.Add("native_queue",row);
    }
    void Observe(ns3::Ptr<const ns3::UbLdstThread> thread,std::string event,
                 ns3::Ptr<const ns3::UbLdstTaskSegment> segment,uint64_t offset,uint32_t bytes)
    {
        const uint64_t now=ns3::Simulator::Now().GetNanoSeconds();
        if (now>policy_.at("end_ns").get<uint64_t>()) return;
        auto node=ns3::NodeList::GetNode(thread->GetNodeId());
        auto api=node->GetObject<ns3::UbController>()->GetUbFunction()->GetUbLdstApi();
        ns3::UintegerValue pool; api->GetAttribute("RequestIdCount",pool);
        Json queue=Json::array(),pending=Json::array();
        auto add=[&](auto& rows,Json item){
            if (queue.size()+pending.size()>=policy_.at("max_snapshot_items").get<uint64_t>())
                throw std::runtime_error("Native queue snapshot budget exceeded; no partial state");
            rows.push_back(std::move(item));
        };
        for (bool load:{false,true}) for (auto task:thread->GetQueueSnapshot(load)) add(queue,Task(task));
        for (const auto& request:api->GetRequestSnapshot()) {
            auto item=Task(request.segment);
            item["request_id"]=request.requestId;item["offset"]=request.offset;item["bytes"]=request.bytes;
            add(pending,std::move(item));
        }
        Json affected=Task(segment);
        if (!affected.is_null()) { affected["offset"]=offset;affected["bytes"]=bytes; }
        Json row={{"rank_id",rank_by_node_.at(thread->GetNodeId())},{"node_id",thread->GetNodeId()},
            {"store_queue_policy",thread->GetStoreQueuePolicy()},
            {"last_store_producer",thread->GetLastStoreProducer()?Json(*thread->GetLastStoreProducer()):Json(nullptr)},
            {"thread_id",thread->GetThreadId()},{"time_ns",now},{"event",event},{"affected",affected},
            {"store_inflight",thread->GetInFlight(false)},{"load_inflight",thread->GetInFlight(true)},
            {"store_capacity",thread->GetCapacity(false)},{"load_capacity",thread->GetCapacity(true)},
            {"request_pool_capacity",pool.Get()},{"queue",std::move(queue)},{"pending",std::move(pending)}};
        last_pool_[thread->GetNodeId()]=row.at("pending");
        last_[{thread->GetNodeId(),thread->GetThreadId()}]=row;Append(std::move(row));
    }
    ResultRecords& records_;Json policy_;uint64_t count_=0;
    uint64_t request_count_=0;
    uint64_t queue_identities_=0,store_identities_=0;
    std::map<uint32_t,ns3::Ptr<ns3::UbLdstApi>> apis_;
    std::map<uint32_t,int> rank_by_node_;
    std::vector<ns3::Ptr<ns3::UbLdstThread>> threads_;
    std::map<std::pair<uint32_t,uint64_t>,Json> identities_;
    std::map<std::pair<uint32_t,uint32_t>,Json> last_;
    std::map<uint32_t,Json> last_pool_;
};
}
