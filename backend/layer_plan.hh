#pragma once

#include "ub_write_transfer.hh"
#include "local_delivery.hh"
#include "extern/helper/json/json.hpp"
#include <filesystem>
#include <map>
#include <utility>
#include <vector>

namespace moe_ub
{
struct LayerPlan
{
    nlohmann::json data;
    std::vector<WriteDescriptor> descriptors;
    std::map<std::pair<int, uint64_t>, nlohmann::json> nodes;
    std::vector<LocalDescriptor> local_descriptors;
};
void ValidateDeployment(const nlohmann::json& deployment, const nlohmann::json& ranks);
uint64_t ValidateBatchLimits(const nlohmann::json& limits);
LayerPlan LoadLayerPlan(const std::filesystem::path& directory);
LayerPlan ValidateLayerPlan(const nlohmann::json& data);
LayerPlan ValidateCannPlan(const nlohmann::json& data);
LayerPlan LoadMultilayerPlan(const std::filesystem::path& directory, const nlohmann::json& data);
LayerPlan LoadKvPlan(const std::filesystem::path& directory, const nlohmann::json& data);
void CheckPlanGraph(const std::filesystem::path& path, const nlohmann::json& graph,
                    const std::map<uint64_t, nlohmann::json>& transfers);
// Consume the original ASTRA execution records after flushing its logger.
// This is a bounded diagnostic export, not an inference from network packets.
nlohmann::json ReadLayerExecution(const LayerPlan& plan, const std::filesystem::path& log, bool graph_identity = false);
nlohmann::json LayerEndpointOperations(const LayerPlan& plan, const nlohmann::json& execution);
nlohmann::json LayerOperationRegistry(const LayerPlan& plan);
}
