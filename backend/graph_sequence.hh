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

// Closed load/prepare/install path for the service. Callers can inspect only a
// const plan; feeders are prepared before control returns to the caller. A
// mutable plan supplied to InstallNextGraph still requires full revalidation.
class LoadedGraph
{
  public:
    explicit LoadedGraph(const std::filesystem::path& directory);
    const LayerPlan& Plan() const;
    void Install(const std::vector<std::unique_ptr<AstraSim::Sys>>& systems,
        const std::vector<std::unique_ptr<UbNetworkAdapter>>& adapters,
        const std::vector<UbWriteTransfer*>& transfers,
        const std::vector<LocalDelivery*>& local = {});
    LayerPlan ReleasePlan();
  private:
    LayerPlan plan_;
    std::vector<std::unique_ptr<Chakra::ETFeeder>> prepared_;
    bool installed_ = false, released_ = false;
};
}
