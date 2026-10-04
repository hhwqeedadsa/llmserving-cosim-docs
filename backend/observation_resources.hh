#pragma once
#include "extern/helper/json/json.hpp"
#include <stdexcept>

namespace moe_ub
{
inline void ValidateObservationBudget(const nlohmann::json& b)
{
    if (!b.is_object() || b.size()!=8 || b.at("schema_version")!="moe-ub-observation-resources/1")
        throw std::runtime_error("Invalid observation resource budget");
    for (auto key : {"max_ports","max_windows_per_port","max_total_windows","max_event_bytes_per_port",
                     "max_ledger_packets","max_registered_transfers","max_export_bytes"})
        if (!b.at(key).is_number_unsigned() || b.at(key).get<uint64_t>()==0 || b.at(key).get<uint64_t>()>INT64_MAX)
            throw std::runtime_error(std::string("Invalid observation budget: ")+key);
    if (b.at("max_windows_per_port")>b.at("max_total_windows")) throw std::runtime_error("Observation window budget mismatch");
}
}
