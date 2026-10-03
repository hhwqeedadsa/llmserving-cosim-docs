#include "graph_sequence.hh"
#include <fstream>
#include <regex>
#include <set>
#include <stdexcept>

namespace moe_ub
{
namespace
{
void Require(bool condition, const std::string& message)
{
    if (!condition) throw std::runtime_error("Graph sequence: " + message);
}
}
std::vector<LayerPlan> LoadGraphSequence(const std::filesystem::path& directory)
{
    using Json = nlohmann::json;
    std::ifstream file(directory / "sequence.json");
    Require(bool(file), "missing sequence.json");
    auto manifest = Json::parse(file);
    Require(manifest == Json({{"schema_version", "moe-ub-sequence/1"}, {"steps", {"0", "1"}}}),
            "require two ordered fixed steps");
    std::vector<LayerPlan> plans{LoadLayerPlan(directory / "0"), LoadLayerPlan(directory / "1")};
    std::set<std::string> graphs, waves, operations;
    std::set<uint64_t> ids, tasks, tags;
    for (const auto& plan : plans)
    {
        const auto& p = plan.data;
        auto graph = p.at("graph_id").get<std::string>();
        Require(std::regex_match(graph, std::regex("[A-Za-z0-9_.-]+")) && graphs.insert(graph).second &&
            waves.insert(p.at("wave_id").get<std::string>()).second, "reused/invalid graph or wave identity");
        Require(p.at("ready_ns") == 0 && p.at("input").at("ready_ns") == 0 &&
                p.at("input").at("graph_id") == graph && p.at("input").at("wave_id") == p.at("wave_id"),
                "fixed step identity/ready mismatch");
        for (const auto& op : p.at("operations"))
            Require(op.at("wave_id") == p.at("wave_id") && operations.insert(op.at("operation_id").get<std::string>()).second,
                    "reused operation identity or wave mismatch");
        for (const auto& d : plan.descriptors)
            Require(ids.insert(d.transfer_id).second && tasks.insert(d.task_id).second && tags.insert(d.tag).second,
                    "reused transfer, UB task or comm_tag");
    }
    const auto& first = plans[0].data.at("input");
    const auto& second = plans[1].data.at("input");
    Require(first.size() == second.size(), "different step schema");
    for (const auto& [key, value] : first.items())
        if (key != "graph_id" && key != "wave_id" && key != "tokens")
            Require(value == second.at(key), "changed shared configuration: " + key);
    Require(first.at("tokens").size() == second.at("tokens").size(), "changed fixed-step Token count");
    for (size_t i = 0; i < first.at("tokens").size(); ++i)
    {
        const auto& old = first.at("tokens")[i]; const auto& next = second.at("tokens")[i];
        Require(old.at("token_index").is_number_unsigned() && old.at("token_index").get<uint64_t>() < INT64_MAX &&
                next.at("token_index") == old.at("token_index").get<uint64_t>() + 1, "not next decode step");
        for (const auto& [key, value] : old.items())
            if (key != "token_index" && key != "expert_ids") Require(value == next.at(key), "changed Token identity");
    }
    return plans;
}

void InstallNextGraph(const LayerPlan& plan, const std::filesystem::path& directory,
    const std::vector<std::unique_ptr<AstraSim::Sys>>& systems,
    const std::vector<std::unique_ptr<UbNetworkAdapter>>& adapters,
    const std::vector<UbWriteTransfer*>& transfers)
{
    Require(systems.size() == adapters.size(), "missing rank objects");
    const auto ranks = plan.data.at("rank_ids").get<std::vector<int>>();
    std::map<int, size_t> rank_index;
    for (size_t i = 0; i < systems.size(); ++i)
        Require(systems[i] && adapters[i] && rank_index.emplace(systems[i]->id, i).second,
                "missing or duplicate rank object");
    for (int rank : ranks) Require(rank_index.contains(rank), "missing group rank object");
    const auto checked = LoadLayerPlan(directory);
    Require(checked.data == plan.data, "plan changed since validation");
    const auto graph = plan.data.at("graph_id").get<std::string>();
    std::vector<std::unique_ptr<Chakra::ETFeeder>> prepared;
    for (int rank : ranks)
    {
        Require(systems[rank_index.at(rank)]->workload->can_load_graph() && !systems[rank_index.at(rank)]->workload->has_graph_id(graph),
                "rank " + std::to_string(rank) + " has an in-flight graph or reused identity");
        adapters[rank_index.at(rank)]->CheckTransfers(transfers);
        prepared.push_back(std::make_unique<Chakra::ETFeeder>(
            (directory / ("layer." + std::to_string(rank) + ".et")).string()));
    }
    // Nothing above starts tasks or replaces an existing feeder.
    size_t index = 0;
    for (int rank : ranks)
        systems[rank_index.at(rank)]->workload->load_graph(std::move(prepared[index++]), graph);
    for (int rank : ranks) adapters[rank_index.at(rank)]->RegisterTransfers(transfers);
}
}
