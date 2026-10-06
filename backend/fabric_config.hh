#pragma once
#include "layer_plan.hh"
namespace moe_ub
{
void ValidateFabric(const nlohmann::json& fabric, const nlohmann::json& topology);
void CheckFabricPlan(const nlohmann::json& fabric, const nlohmann::json& plan);
nlohmann::json AuditLiveFabric(const nlohmann::json& fabric, bool native_memory = false);
}
