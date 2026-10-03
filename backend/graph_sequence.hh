#pragma once

#include "layer_plan.hh"
#include "ub_network_adapter.hh"
#include "astra-sim/system/Sys.hh"
#include <memory>

namespace moe_ub
{
std::vector<LayerPlan> LoadGraphSequence(const std::filesystem::path& directory);
void InstallNextGraph(const LayerPlan& plan, const std::filesystem::path& directory,
    const std::vector<std::unique_ptr<AstraSim::Sys>>& systems,
    const std::vector<std::unique_ptr<UbNetworkAdapter>>& adapters,
    const std::vector<UbWriteTransfer*>& transfers,
    const std::vector<LocalDelivery*>& local = {});
}
