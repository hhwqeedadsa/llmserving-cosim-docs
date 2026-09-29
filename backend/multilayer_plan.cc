#include "layer_plan.hh"

#include <set>
#include <stdexcept>

namespace moe_ub
{
namespace
{
using Json = nlohmann::json;
void Require(bool condition, const std::string& message)
{
    if (!condition) throw std::runtime_error("Multilayer plan: " + message);
}
Json Common(Json input)
{
    for (const auto* key : {"layer_id", "tokens", "compute_profile_ns", "schema_version"}) input.erase(key);
    return input;
}
Json TokenKeys(const Json& layer)
{
    auto tokens = layer.at("input").at("tokens");
    for (auto& token : tokens) { token.erase("layer_id"); token.erase("expert_ids"); }
    return tokens;
}
}

LayerPlan LoadMultilayerPlan(const std::filesystem::path& directory, const Json& p)
{
    const auto& layers = p.at("layers");
    Require(layers.is_array() && layers.size() >= 2 && layers.size() <= 32, "require 2..32 layers");
    const auto& first = layers[0];
    const auto common = Common(first.at("input"));
    Json expected = first;
    expected["schema_version"] = "moe-ub-multilayer-plan/1";
    expected["layers"] = layers;
    expected["executed_layer_ids"] = Json::array();
    expected["input"] = common;
    expected["input"]["schema_version"] = "moe-ub-multilayer-input/1";
    expected["operations"] = Json::array(); expected["transfers"] = Json::array();
    expected["rank_graphs"] = Json::array(); expected["expert_input_counts"] = Json::array();
    expected.erase("input_provenance");
    std::map<int, std::vector<uint64_t>> previous;
    for (const int rank : first.at("rank_ids"))
    {
        expected["rank_graphs"].push_back({{"rank_id", rank}, {"nodes", Json::array()}, {"layer_phases", Json::array()}});
        expected["expert_input_counts"].push_back(0u);
        previous[rank] = {};
    }
    std::set<std::string> operations;
    std::map<std::string, std::set<uint64_t>> identities;
    LayerPlan result{p, {}, {}};
    uint64_t layer_index = 0;
    for (const auto& layer : layers)
    {
        const auto checked = ValidateLayerPlan(layer);
        const auto& input = layer.at("input");
        Require(input.at("layer_id").is_number_unsigned() && input.at("layer_id").get<uint64_t>() < 32,
                "invalid layer id");
        const auto id = input.at("layer_id").get<uint64_t>();
        Require(id == first.at("input").at("layer_id").get<uint64_t>() + layer_index++, "nonconsecutive layer order");
        Require(input.at("role") == "D" && input.at("compute_profile_ns").at("next_layer") == 0,
                "multilayer decode must omit synthetic next_layer work");
        Require(Common(input) == common && TokenKeys(layer) == TokenKeys(first), "shared configuration/Token continuity mismatch");
        for (const auto* key : {"graph_id", "wave_id", "rank_ids", "rank_to_ub_node", "ready_ns", "instance_batches",
                               "send_slots_per_rank", "communication_template", "sender_completion"})
            Require(layer.at(key) == first.at(key), std::string("identity/configuration mismatch: ") + key);
        expected["executed_layer_ids"].push_back(id);
        std::map<int, uint64_t> offsets;
        for (const auto& graph : expected.at("rank_graphs")) offsets[graph.at("rank_id")] = graph.at("nodes").size();
        for (const auto& op : layer.at("operations"))
        {
            Require(op.at("layer_id") == id && op.at("wave_id") == first.at("wave_id") &&
                    operations.insert(op.at("operation_id").get<std::string>()).second, "duplicate/mismatched operation");
            expected["operations"].push_back(op);
        }
        for (auto transfer : layer.at("transfers"))
        {
            for (const auto* key : {"transfer_id", "ub_task_id", "comm_tag"})
                Require(identities[key].insert(transfer.at(key).get<uint64_t>()).second, std::string("duplicate ") + key);
            transfer["send_node_id"] = transfer.at("send_node_id").get<uint64_t>() + offsets.at(transfer.at("src_rank"));
            transfer["recv_node_id"] = transfer.at("recv_node_id").get<uint64_t>() + offsets.at(transfer.at("dst_rank"));
            expected["transfers"].push_back(transfer);
        }
        for (auto descriptor : checked.descriptors)
        {
            descriptor.send_node_id += offsets.at(descriptor.src_rank);
            descriptor.recv_node_id += offsets.at(descriptor.dst_rank);
            result.descriptors.push_back(descriptor);
        }
        for (size_t i = 0; i < layer.at("rank_graphs").size(); ++i)
        {
            const auto& source = layer.at("rank_graphs")[i]; auto& target = expected["rank_graphs"][i];
            const int rank = source.at("rank_id"); const auto offset = offsets.at(rank);
            std::set<uint64_t> used;
            for (const auto& node : source.at("nodes"))
                for (const auto& dep : node.at("deps")) used.insert(dep.get<uint64_t>());
            const auto entry = previous.at(rank);
            std::vector<uint64_t> terminal;
            uint64_t number = 0;
            for (auto node : source.at("nodes"))
            {
                Require(node.at("node_id") == ++number, "noncontiguous layer node ids");
                node["node_id"] = number + offset; node["layer_id"] = id;
                if (node.at("deps").empty()) node["deps"] = entry;
                else for (auto& dep : node["deps"]) dep = dep.get<uint64_t>() + offset;
                if (!used.contains(number)) terminal.push_back(number + offset);
                target["nodes"].push_back(node);
                Require(result.nodes.emplace(std::make_pair(rank, number + offset), node).second, "duplicate node");
            }
            if (terminal.empty()) terminal = entry;
            auto phases = source.at("phase_nodes");
            for (auto& [name, value] : phases.items()) if (!value.is_null()) value = value.get<uint64_t>() + offset;
            target["layer_phases"].push_back({{"layer_id", id}, {"entry_deps", entry},
                {"terminal_nodes", terminal}, {"phase_nodes", phases}});
            previous[rank] = terminal;
            expected["expert_input_counts"][i] = expected["expert_input_counts"][i].get<uint64_t>() +
                layer.at("expert_input_counts")[i].get<uint64_t>();
        }
    }
    Require(expected.at("transfers").size() <= 4096, "more than 4096 transfers");
    expected["input"]["executed_layer_ids"] = expected.at("executed_layer_ids");
    for (const auto& [key, value] : expected.items())
        if (key != "compute_source" && key != "route_source")
            Require(p.at(key) == value, "composition mismatch: " + key);
    std::map<uint64_t, Json> transfers;
    for (const auto& transfer : p.at("transfers")) transfers.emplace(transfer.at("transfer_id"), transfer);
    for (const auto& graph : p.at("rank_graphs"))
    {
        uint64_t total = p.at("ready_ns");
        for (const auto& node : graph.at("nodes"))
            if (node.at("kind") == "COMP")
            {
                const auto duration = node.at("duration_ns").get<uint64_t>();
                Require(duration <= uint64_t(INT64_MAX) - total, "computation time overflow");
                total += duration;
            }
        CheckPlanGraph(directory / ("layer." + std::to_string(graph.at("rank_id").get<int>()) + ".et"), graph, transfers);
    }
    return result;
}
}
