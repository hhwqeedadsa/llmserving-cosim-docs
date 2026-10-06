#pragma once
#include "record_store.hh"
#include "extern/helper/json/json.hpp"
#include <memory>
#include <set>
#include <stdexcept>

namespace moe_ub
{
inline nlohmann::json RecordReference(const RecordFile& file)
{
    return {{"name", file.name}, {"records", file.records}, {"raw_bytes", file.raw_bytes},
        {"compressed_bytes", file.compressed_bytes}, {"crc32", file.crc32}};
}
inline void ValidateRecordPolicy(const nlohmann::json& policy)
{
    if (!policy.is_object() || policy.size() != 4 || policy.at("schema_version") != "moe-ub-record-storage/1")
        throw std::runtime_error("Invalid record storage policy");
    for (const auto* key : {"max_compressed_bytes", "max_buffer_bytes", "max_record_bytes"})
        if (!policy.at(key).is_number_unsigned() || policy.at(key).get<uint64_t>() == 0 || policy.at(key).get<uint64_t>() > INT64_MAX)
            throw std::runtime_error(std::string("Invalid record storage limit: ") + key);
    if (policy.at("max_buffer_bytes").get<uint64_t>() > 1073741824 ||
        policy.at("max_record_bytes").get<uint64_t>() > 67108864 ||
        policy.at("max_buffer_bytes").get<uint64_t>() < policy.at("max_record_bytes").get<uint64_t>() + 65536)
        throw std::runtime_error("Record buffer must fit a record and chunk within the host limit");
}

// Existing small runs retain the original arrays. Explicit storage policy runs
// emit a versioned result whose large arrays reference ordered gzip JSONL files.
// Graph files are listed in submission order even if groups finish out of order.
class ResultRecords
{
    using Json = nlohmann::json;
  public:
    ResultRecords(Json& report, Json policy) : report_(report), policy_(std::move(policy))
    {
        if (!policy_.is_null())
        {
            ValidateRecordPolicy(policy_);
            store_ = std::make_shared<RecordStore>("records", policy_.at("max_compressed_bytes"),
                policy_.at("max_buffer_bytes"), policy_.at("max_record_bytes"));
            for (const auto* field : {"wqe_starts", "wqe_completions", "target_writes", "native_starts", "native_completions"}) Register(field, "");
            for (const auto* field : {"transfers", "local_deliveries", "node_execution", "endpoint_ops",
                                     "operation_registry", "transfer_registry", "planned_transfers", "plan_summary"}) ordered_[field];
        }
    }
    bool Bounded() const { return bool(store_); }
    std::shared_ptr<RecordStore> Store() const { return store_; }
    void BeginGraph(const std::string& graph)
    {
        if (!store_) return;
        if (!graphs_.emplace(graph, graphs_.size()).second) throw std::runtime_error("Repeated record graph");
        for (const auto* field : {"transfers", "local_deliveries", "node_execution", "endpoint_ops",
                                 "operation_registry", "transfer_registry", "planned_transfers", "plan_summary"})
            Register(field, graph);
    }
    void Add(const std::string& field, const Json& row, const std::string& graph = "")
    {
        if (store_)
        {
            const auto name = Name(field, graph);
            if (!registered_.count(name)) throw std::runtime_error("Unregistered result record field");
            store_->Append(name, row.dump()); ++counts_[field];
        }
        else report_.at(field).push_back(row);
    }
    uint64_t Count(const std::string& field) const
    {
        if (!store_) return report_.at(field).size();
        const auto found = counts_.find(field); return found == counts_.end() ? 0 : found->second;
    }
    void SealPlannedTransfers(const std::string& graph, size_t expected)
    {
        if (!store_) throw std::runtime_error("Planned archive requires bounded records");
        const auto name = Name("planned_transfers", graph);
        const auto file = store_->Seal(name);
        if (file.records != expected) throw std::runtime_error("Planned transfer archive count mismatch");
        ReadRecords(store_->Directory(), file, [](const std::string&) {}, policy_.at("max_record_bytes"));
        sealed_[name] = file;
    }
    void FinishGraph(const std::string& graph)
    {
        if (!store_) return;
        for (const auto* field : {"transfers", "local_deliveries", "node_execution", "endpoint_ops",
                                 "operation_registry", "transfer_registry", "planned_transfers", "plan_summary"})
            sealed_[Name(field, graph)] = store_->Seal(Name(field, graph));
    }
    void Finish()
    {
        if (!store_) return;
        // A normal empty service still has three empty chronological streams.
        // Registering a logical field does not allocate a physical file until
        // its first row or explicit seal.
        for (const auto& [field, names] : ordered_)
            for (const auto& name : names) store_->Seal(name);
        const auto files = store_->SealAll();
        for (const auto& file : files) sealed_[file.name] = file;
        Json references = Json::object();
        for (const auto& [field, names] : ordered_)
        {
            Json parts = Json::array(); uint64_t count = 0;
            for (const auto& name : names) { parts.push_back(RecordReference(sealed_.at(name))); count += sealed_.at(name).records; }
            if (count != Count(field)) throw std::runtime_error("Result record count mismatch");
            report_.erase(field); references[field] = {{"records", count}, {"files", std::move(parts)}};
        }
        Json archives = Json::array();
        for (const auto& file : files)
            if (!registered_.count(file.name)) archives.push_back(RecordReference(file));
        report_["schema_version"] = "moe-ub-service-result/2";
        report_["record_storage"] = {{"schema_version", "moe-ub-result-records/1"}, {"directory", "records"},
            {"policy", policy_}, {"fields", std::move(references)}, {"auxiliary_files", std::move(archives)},
            {"compressed_bytes", store_->Bytes()}, {"peak_buffered_payload_bytes", store_->PeakBufferedBytes()}};
    }
  private:
    std::string Name(const std::string& field, const std::string& graph) const
    { return field + (graph.empty() ? "" : "-g" + std::to_string(graphs_.at(graph))) + ".jsonl.gz"; }
    void Register(const std::string& field, const std::string& graph)
    {
        const auto name = Name(field, graph);
        if (!registered_.insert(name).second) throw std::runtime_error("Repeated record field");
        ordered_[field].push_back(name);
    }
    Json& report_;
    Json policy_;
    std::shared_ptr<RecordStore> store_;
    std::map<std::string, size_t> graphs_;
    std::map<std::string, uint64_t> counts_;
    std::map<std::string, std::vector<std::string>> ordered_;
    std::map<std::string, RecordFile> sealed_;
    std::set<std::string> registered_;
};
}
