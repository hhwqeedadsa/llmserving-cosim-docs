// Offline resource measurement: the production JSON/Chakra loader, no network.
#include "layer_plan.hh"
#include <chrono>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <tuple>

namespace
{
void CompareRetention(const std::filesystem::path& directory, const moe_ub::LayerPlan& actual)
{
    auto expected = moe_ub::LoadLayerPlan(directory);
    auto require = [](bool ok) { if (!ok) throw std::runtime_error("Execution retention differs from complete load"); };
    if (actual.execution_retention)
    {
        for (auto& layer : expected.data["layers"]) { layer.erase("rank_graphs"); layer.erase("transfers"); }
        for (auto& graph : expected.data["rank_graphs"]) graph.erase("nodes");
    }
    require(actual.data == expected.data && actual.validated_usage == expected.validated_usage &&
            actual.nodes.size() == expected.nodes.size() && actual.descriptors.size() == expected.descriptors.size() &&
            actual.local_descriptors.size() == expected.local_descriptors.size());
    for (const auto& [key, node] : expected.nodes)
    {
        const auto& other = actual.nodes.at(key);
        require(std::tie(node.kind, node.duration_ns, node.deps, node.resource_id, node.event, node.event_transfer) ==
                std::tie(other.kind, other.duration_ns, other.deps, other.resource_id, other.event, other.event_transfer));
    }
    auto fields = [](const auto& d) { return std::tie(d.transfer_id, d.task_id, d.graph_id, d.send_node_id, d.recv_node_id,
        d.src_rank, d.dst_rank, d.tag, d.bytes, d.operation_id, d.chunk_index, d.pair_byte_offset, d.pair_payload_bytes,
        d.src_ub_node, d.dst_ub_node); };
    for (size_t i = 0; i < expected.descriptors.size(); ++i)
    {
        const auto& a = actual.descriptors[i]; const auto& b = expected.descriptors[i];
        require(fields(a) == fields(b) && bool(a.payload_regions) == bool(b.payload_regions));
        if (a.payload_regions)
        {
            require(a.payload_regions->size() == b.payload_regions->size());
            for (size_t j = 0; j < a.payload_regions->size(); ++j)
            {
                const auto& x = a.payload_regions->at(j); const auto& y = b.payload_regions->at(j);
                require(std::tie(x.begin, x.end, x.kind) == std::tie(y.begin, y.end, y.kind));
            }
        }
    }
    auto local = [](const auto& d) { return std::tie(d.transfer_id, d.graph_id, d.send_node_id, d.recv_node_id,
        d.src_rank, d.dst_rank, d.tag, d.bytes, d.operation_id); };
    for (size_t i = 0; i < expected.local_descriptors.size(); ++i)
        require(local(actual.local_descriptors[i]) == local(expected.local_descriptors[i]));
}
}

int main(int argc, char** argv)
{
    try
    {
        const bool parse_only = argc == 3 && std::string(argv[2]) == "--parse-only";
        const bool compare = argc == 3 && std::string(argv[2]) == "--compare-retention";
        const bool execution = compare || (argc == 3 && std::string(argv[2]) == "--execution-retention");
        if (argc != 2 && !parse_only && !execution)
            throw std::runtime_error("Usage: cosim-plan-load-probe GRAPH_DIRECTORY [--parse-only|--execution-retention|--compare-retention]");
        const auto start = std::chrono::steady_clock::now();
        if (parse_only)
        {
            // Diagnostic separation of DOM allocation from semantic rebuilding.
            // A successful parse is explicitly NOT a validated plan or execution.
            std::cout << "PHASE json-dom-only-no-semantic-validation" << std::endl;
            std::ifstream stream(std::filesystem::path(argv[1]) / "plan.json");
            if (!stream) throw std::runtime_error("Cannot open plan.json");
            const auto data = nlohmann::json::parse(stream);
            const auto seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
            std::cout << nlohmann::json({{"schema_version", "moe-ub-json-parse-probe/1"},
                {"root_fields", data.size()}, {"parse_seconds", seconds},
                {"semantic_validation", false}, {"network_initialized", false},
                {"simulation_executed", false}}).dump() << std::endl;
            return 0;
        }
        std::cout << "PHASE production-plan-and-chakra-load" << std::endl;
        const auto plan = moe_ub::LoadLayerPlan(argv[1], execution ? moe_ub::PlanRetention::Execution : moe_ub::PlanRetention::Complete);
        const auto seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        nlohmann::json result = {{"schema_version", "moe-ub-plan-load-probe/1"},
            {"plan_schema", plan.data.at("schema_version")}, {"nodes", plan.nodes.size()},
            {"fabric_transfers", plan.descriptors.size()}, {"local_transfers", plan.local_descriptors.size()},
            {"ranks", plan.data.at("rank_ids").size()}, {"load_seconds", seconds},
            {"network_initialized", false}, {"simulation_executed", false}};
        if (execution)
        {
            size_t retained_nodes = 0, child_tables = 0;
            for (const auto& graph : plan.data.at("rank_graphs"))
                if (graph.contains("nodes")) retained_nodes += graph.at("nodes").size();
            if (plan.data.contains("layers"))
                for (const auto& layer : plan.data.at("layers"))
                    child_tables += layer.contains("rank_graphs") + layer.contains("transfers");
            result["retention"] = {{"execution", plan.execution_retention}, {"rank_node_json_records", retained_nodes},
                {"child_graph_transfer_tables", child_tables}, {"validated_usage", plan.validated_usage}};
        }
        if (compare) { CompareRetention(argv[1], plan); result["retention"]["complete_load_equal"] = true; }
        std::cout << result.dump() << std::endl;
    }
    catch (const std::exception& error)
    {
        std::cerr << "Plan load probe failed: " << error.what() << std::endl;
        return 1;
    }
}
