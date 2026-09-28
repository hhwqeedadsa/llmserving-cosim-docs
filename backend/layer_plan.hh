#pragma once

#include "ub_write_transfer.hh"
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
};
LayerPlan LoadLayerPlan(const std::filesystem::path& directory);
// Consume the original ASTRA execution records after flushing its logger.
// This is a bounded diagnostic export, not an inference from network packets.
nlohmann::json ReadLayerExecution(const LayerPlan& plan, const std::filesystem::path& log, bool graph_identity = false);
nlohmann::json LayerEndpointOperations(const LayerPlan& plan, const nlohmann::json& execution);
nlohmann::json LayerOperationRegistry(const LayerPlan& plan);
}
