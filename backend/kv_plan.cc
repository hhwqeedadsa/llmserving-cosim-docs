#include "layer_plan.hh"
#include <set>
#include <stdexcept>

namespace moe_ub
{
namespace
{
using Json = nlohmann::json;
void Require(bool ok, const std::string& message)
{
    if (!ok) throw std::runtime_error("KV plan: " + message);
}
uint64_t Number(const Json& value, uint64_t min, uint64_t max, const char* name)
{
    Require(value.is_number_unsigned() && value.get<uint64_t>() >= min && value.get<uint64_t>() <= max,
            std::string("invalid ") + name);
    return value.get<uint64_t>();
}
}
LayerPlan LoadKvPlan(const std::filesystem::path& directory, const Json& data)
{
    LayerPlan plan{data, {}, {}};
    const auto& c = data.at("input");
    Require(data.at("schema_version") == "moe-ub-execution-plan/1" && c.at("schema_version") == "moe-ub-kv-fixture/1" &&
            data.at("communication_template") == "kv_direct_v1" && data.at("sender_completion") == "transaction_done" &&
            c.at("kv_handoff_policy") == "between_iterations", "unsupported schema/template/policy");
    for (const char* key : {"graph_id", "wave_id", "service_id", "request_id"})
        Require(c.at(key).is_string() && !c.at(key).get<std::string>().empty(), "missing identity");
    Number(c.at("ready_ns"), 0, INT64_MAX-1000000, "ready time");
    Require(data.at("graph_id") == c.at("graph_id") && data.at("wave_id") == c.at("wave_id") &&
            data.at("ready_ns") == c.at("ready_ns"), "identity/time mismatch");
    Require(c.at("source_layout") == "layer-kv-token-head-dim-tp1" && c.at("destination_layout") == c.at("source_layout"),
            "incompatible KV layout");
    Require(c.at("kv_dtype") == "bfloat16" || c.at("kv_dtype") == "fp8", "unsupported KV dtype");
    const auto& model = c.at("model");
    Require(model.size() == 5 && model.at("cache_format") == "MHA", "unsupported cache format");
    Number(model.at("num_hidden_layers"), 1, 1, "layer count");
    const auto heads = Number(model.at("num_attention_heads"), 1, 32, "head count");
    Require(Number(model.at("num_key_value_heads"), 1, 32, "KV heads") == heads, "MHA head mismatch");
    const auto dim = Number(model.at("head_dim"), 1, 256, "head dimension");
    const auto& range = c.at("token_range");
    Require(range.is_array() && range.size() == 2, "invalid Token range");
    Number(range[0], 0, 0, "Token begin");
    const auto tokens = Number(range[1], 1, 16, "Token end");
    const auto bytes = tokens * heads * dim * (c.at("kv_dtype") == "bfloat16" ? 2 : 1);
    const auto chunk = Number(c.at("chunk_bytes"), 1, UINT32_MAX, "chunk bytes");
    const auto slots = Number(c.at("send_slots_per_rank"), 1, 2, "send slots");
    Require(data.at("send_slots_per_rank") == slots && data.at("kv_payload_bytes") == 2*bytes, "KV size/slots mismatch");
    std::set<int> members;
    for (const char* field : {"source_group", "destination_group"})
    {
        const auto& group = c.at(field);
        Require(group.size() == 2 && group.at("group_id").is_string() && !group.at("group_id").get<std::string>().empty() &&
                group.at("rank_ids").is_array() && group.at("rank_ids").size() == 4, "invalid group");
        int previous = -1;
        for (const auto& value : group.at("rank_ids"))
        {
            const int rank = Number(value, 0, 11, "group rank");
            Require(rank > previous && members.insert(rank).second, "overlapping/unordered group"); previous = rank;
        }
    }
    Require(members.size() == 8 && c.at("source_group").at("group_id") != c.at("destination_group").at("group_id"),
            "invalid P/D groups");
    const int src = Number(c.at("src_rank"), 0, 11, "source rank"), dst = Number(c.at("dst_rank"), 0, 11, "destination rank");
    const auto source_ranks = c.at("source_group").at("rank_ids").get<std::vector<int>>();
    const auto destination_ranks = c.at("destination_group").at("rank_ids").get<std::vector<int>>();
    Require(std::find(source_ranks.begin(), source_ranks.end(), src) != source_ranks.end() &&
            std::find(destination_ranks.begin(), destination_ranks.end(), dst) != destination_ranks.end(), "endpoint outside group");
    const Json ranks = std::vector<int>(members.begin(), members.end());
    Require(data.at("rank_ids") == ranks && data.at("rank_to_ub_node") == ranks, "invalid mapping");
    const auto wave = c.at("wave_id").get<std::string>(), graph = c.at("graph_id").get<std::string>();
    const auto op_id = wave + ".KV_TRANSFER";
    Json operation = {{"operation_id", op_id}, {"kind", "KV_TRANSFER"}, {"service_id", c.at("service_id")},
        {"role", "P/D"}, {"wave_id", wave}, {"layer_id", 0},
        {"group_id", c.at("source_group").at("group_id").get<std::string>() + "->" + c.at("destination_group").at("group_id").get<std::string>()},
        {"participants", ranks}};
    Require(data.at("operations") == Json::array({operation}), "KV operation mismatch");
    const auto count = 2*((bytes+chunk-1)/chunk);
    Require(count <= 4096 && data.at("transfers").is_array() && data.at("transfers").size() == count, "wrong KV transfer count");
    std::map<uint64_t, Json> transfers;
    std::set<uint64_t> tasks, tags;
    uint64_t ordinal = 0;
    for (int component = 0; component < 2; ++component)
        for (uint64_t offset = 0; offset < bytes; offset += chunk, ++ordinal)
        {
            const auto& t = data.at("transfers")[ordinal];
            const auto size = std::min(chunk, bytes-offset), begin = component*bytes+offset;
            const auto id = Number(t.at("transfer_id"), 1, UINT64_MAX, "transfer id");
            const auto task = Number(t.at("ub_task_id"), 1, UINT32_MAX, "UB task id");
            const auto tag = Number(t.at("comm_tag"), 1, 499999999, "tag");
            Require(transfers.emplace(id,t).second && tasks.insert(task).second && tags.insert(tag).second, "repeated KV identity");
            const std::string kind = component ? "V" : "K";
            const Json source = {{"request_id", c.at("request_id")}, {"component", kind}, {"layer_id", 0},
                {"token_range", range}, {"head_range", {0,heads}}, {"head_dim", dim}, {"kv_dtype", c.at("kv_dtype")},
                {"layout", c.at("source_layout")}};
            Require(t.at("sources") == Json::array({source}) && t.at("kv_component") == kind &&
                t.at("component_byte_range") == Json::array({offset,offset+size}) &&
                t.at("pair_byte_range") == Json::array({begin,begin+size}) && t.at("pair_payload_bytes") == 2*bytes,
                "KV component, source or range mismatch");
            Require(t.at("payload_bytes") == size && t.at("business_payload_bytes") == size &&
                t.at("application_metadata_bytes") == 0 && t.at("task_byte_range") == Json::array({0,size}) &&
                t.at("chunk_index") == ordinal && t.at("kind") == "KV_TRANSFER" && t.at("operation_id") == op_id &&
                t.at("src_rank") == src && t.at("dst_rank") == dst && t.at("send_node_id") == ordinal+1 &&
                t.at("recv_node_id") == ordinal+1 && t.at("priority") == 7 &&
                t.at("transport_profile_id") == "urma-write-rtp-default", "KV transfer descriptor mismatch");
            plan.descriptors.push_back({id, task, graph, ordinal+1, ordinal+1, src, dst,
                static_cast<int>(tag), size, op_id, static_cast<uint32_t>(ordinal), begin, 2*bytes});
        }
    Require(data.at("rank_graphs").size() == 8 && data.at("instance_batches").size() == 8, "missing handoff members");
    size_t member_index = 0;
    for (int rank : members)
    {
        const auto index = member_index++;
        const auto& g = data.at("rank_graphs")[index];
        Json requests = Json::array();
        if (rank == src || rank == dst) requests.push_back(c.at("request_id"));
        Require(data.at("instance_batches")[index] == Json({{"rank_id",rank},{"request_ids",requests}}), "batch mismatch");
        Require(g.at("rank_id") == rank && g.at("nodes").is_array() &&
            g.at("nodes").size() == ((rank == src || rank == dst) ? count : 0), "invalid member graph");
        for (size_t i = 0; i < g.at("nodes").size(); ++i)
        {
            const auto& n = g.at("nodes")[i]; const auto& t = data.at("transfers")[i];
            const bool send = rank == src;
            const auto name = "kv-" + t.at("kv_component").get<std::string>() + (send ? "-send-" : "-recv-") + std::to_string(i);
            Require(n == Json({{"node_id",i+1},{"rank_id",rank},{"kind",send ? "SEND" : "RECV"},
                {"name",name},{"transfer_id",t.at("transfer_id")},{"deps",Json::array()}}), "KV graph nodes/dependencies differ");
            plan.nodes.emplace(std::make_pair(rank,i+1),n);
        }
        CheckPlanGraph(directory / ("layer." + std::to_string(rank) + ".et"), g, transfers);
    }
    return plan;
}
}
