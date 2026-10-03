#pragma once
#include "layer_plan.hh"
#include "ns3/ub-app.h"
#include "ns3/ub-controller.h"
#include <optional>

namespace moe_ub
{
nlohmann::json BackendCatalog();
class BackendConfig
{
  public:
    explicit BackendConfig(nlohmann::json input);
    static std::optional<BackendConfig> Load(const std::filesystem::path& network);
    const nlohmann::json& Data() const { return input_; }
    void Apply() const;
    void CheckPlan(const nlohmann::json& plan) const;
    void WriteEffective(const std::vector<ns3::Ptr<ns3::UbController>>& controllers,
                        const std::vector<ns3::Ptr<ns3::UbApp>>& apps) const;
  private:
    nlohmann::json input_;
};
}
