#pragma once

#include "ub_write_transfer.hh"
#include "local_delivery.hh"
#include "execution_timing.hh"
#include "extern/helper/json/json.hpp"
#include <filesystem>
#include <map>
#include <utility>
#include <vector>

namespace moe_ub
{
// Execution checking needs these values, not another JSON object per node.
struct ExecutionNode
{
    std::string kind;
    uint64_t duration_ns;
    std::vector<uint64_t> deps;
    ExecutionNode(const nlohmann::json& node)
        : kind(node.at("kind").get<std::string>()),
          duration_ns(kind == "COMP" ? node.at("duration_ns").get<uint64_t>() : 0),
          deps(node.at("deps").get<std::vector<uint64_t>>()) {}
};
struct LayerPlan
{
    struct EndpointNode { uint32_t operation; int rank; uint64_t node; };
    // Execution retention omits validated child tables and rank node JSON;
    // typed execution nodes/descriptors and the original file remain complete.
    nlohmann::json data;
    std::vector<WriteDescriptor> descriptors;
    std::map<std::pair<int, uint64_t>, ExecutionNode> nodes;
    std::vector<LocalDescriptor> local_descriptors;
    nlohmann::json validated_usage = nullptr;
    bool execution_retention = false;
    bool archived_transfer_data = false;
    std::vector<EndpointNode> endpoint_nodes;
};
// Caller first seals and verifies its lossless planned-transfer archive.
void CompactArchivedTransfers(LayerPlan& plan);
enum class PlanRetention { Complete, Execution };
void ValidateDeployment(const nlohmann::json& deployment, const nlohmann::json& ranks);
uint64_t ValidateBatchLimits(const nlohmann::json& limits);
LayerPlan LoadLayerPlan(const std::filesystem::path& directory, PlanRetention retention = PlanRetention::Complete);
// retain_data only controls returned storage; all semantic checks always run.
LayerPlan ValidateLayerPlan(const nlohmann::json& data, bool retain_data = true);
LayerPlan ValidateCannPlan(const nlohmann::json& data, bool retain_data = true);
LayerPlan ValidateNativePlan(const nlohmann::json& data, bool retain_data = true);
void ValidateFullModelProfile(const nlohmann::json& model, const nlohmann::json& profile, const nlohmann::json& ranks);
LayerPlan LoadFullModelPlan(const std::filesystem::path& directory, nlohmann::json data,
                          std::vector<std::string> packed_children = {});
LayerPlan LoadMultilayerPlan(const std::filesystem::path& directory, const nlohmann::json& data);
LayerPlan LoadKvPlan(const std::filesystem::path& directory, const nlohmann::json& data);
void CheckPlanGraph(const std::filesystem::path& path, const nlohmann::json& graph,
                    const std::map<uint64_t, nlohmann::json>& transfers);
void CheckPlanGraph(const std::filesystem::path& path, const nlohmann::json& graph,
                    const std::map<uint64_t, const nlohmann::json*>& transfers);
// Consume the original ASTRA execution records after flushing its logger.
// This is a bounded diagnostic export, not an inference from network packets.
nlohmann::json ReadLayerExecution(const LayerPlan& plan, const std::filesystem::path& log, bool graph_identity = false);
nlohmann::json LayerEndpointOperations(const LayerPlan& plan, const nlohmann::json& execution);
nlohmann::json LayerEndpointOperations(const LayerPlan& plan, const ExecutionTimes& execution);
nlohmann::json LayerOperationRegistry(const LayerPlan& plan);
}
