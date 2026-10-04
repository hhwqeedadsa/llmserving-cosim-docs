// Offline resource measurement: the production JSON/Chakra loader, no network.
#include "layer_plan.hh"
#include <chrono>
#include <iostream>
#include <stdexcept>

int main(int argc, char** argv)
{
    try
    {
        if (argc != 2) throw std::runtime_error("Usage: cosim-plan-load-probe GRAPH_DIRECTORY");
        const auto start = std::chrono::steady_clock::now();
        std::cout << "PHASE production-plan-and-chakra-load" << std::endl;
        const auto plan = moe_ub::LoadLayerPlan(argv[1]);
        const auto seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        nlohmann::json result = {{"schema_version", "moe-ub-plan-load-probe/1"},
            {"plan_schema", plan.data.at("schema_version")}, {"nodes", plan.nodes.size()},
            {"fabric_transfers", plan.descriptors.size()}, {"local_transfers", plan.local_descriptors.size()},
            {"ranks", plan.data.at("rank_ids").size()}, {"load_seconds", seconds},
            {"network_initialized", false}, {"simulation_executed", false}};
        std::cout << result.dump() << std::endl;
    }
    catch (const std::exception& error)
    {
        std::cerr << "Plan load probe failed: " << error.what() << std::endl;
        return 1;
    }
}
