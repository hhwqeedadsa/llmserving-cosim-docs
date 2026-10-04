#include "graph_resources.hh"
#include "layer_plan.hh"
#include "operator_profile.hh"
#include "packed_plan_json.hh"
#include "et_def.pb.h"

#include <fstream>
#include <limits>
#include <regex>
#include <set>
#include <stdexcept>

namespace moe_ub
{
namespace
{
using Json = nlohmann::json;
void Require(bool ok, const std::string& message)
{
    if (!ok) throw std::runtime_error("Layer plan: " + message);
}
uint64_t Number(const Json& value, const std::string& field, uint64_t maximum = UINT64_MAX)
{
    Require(value.is_number_unsigned() && value.get<uint64_t>() <= maximum, "invalid unsigned " + field);
    return value.get<uint64_t>();
}
std::set<uint64_t> Ids(const Json& values)
{
    Require(values.is_array(), "expected dependency array");
    std::set<uint64_t> result;
    for (const auto& value : values) Require(result.insert(Number(value, "dependency")).second, "duplicate dependency");
    return result;
}
template<class Message> bool ReadMessage(std::istream& input, Message& message)
{
    int first = input.get();
    if (first == EOF) { Require(input.eof(), "graph read error"); return false; }
    uint32_t length = 0;
    for (unsigned i = 0; i < 5; ++i)
    {
        const int byte = i == 0 ? first : input.get();
        Require(byte != EOF && (i != 4 || byte < 16), "invalid graph message length");
        length |= static_cast<uint32_t>(byte & 127) << (7 * i);
        if (!(byte & 128))
        {
            Require(length > 0 && length <= 1024 * 1024, "graph message size outside bounded fixture");
            std::string body(length, '\0');
            input.read(body.data(), length);
            Require(input.gcount() == length && message.ParseFromString(body), "truncated or malformed graph message");
            return true;
        }
    }
    throw std::runtime_error("Layer plan: oversized graph varint");
}
void CheckGraph(const std::filesystem::path& path, const Json& graph,
                const std::map<uint64_t, const Json*>& transfers)
{
    std::ifstream input(path, std::ios::binary);
    Require(bool(input), "missing graph " + path.string());
    ChakraProtoMsg::GlobalMetadata metadata;
    Require(ReadMessage(input, metadata) && metadata.version() == "moe-ub-execution-plan/1", "graph metadata mismatch");
    for (const auto& n : graph.at("nodes"))
    {
        ChakraProtoMsg::Node node;
        Require(ReadMessage(input, node), "missing graph node");
        Require(node.id() == n.at("node_id") && node.name() == n.at("name") && node.ctrl_deps_size() == 0 &&
                std::vector<uint64_t>(node.data_deps().begin(), node.data_deps().end()) == n.at("deps").get<std::vector<uint64_t>>(),
                "graph node identity/dependencies differ from plan");
        std::map<std::string, ChakraProtoMsg::AttributeProto> attrs;
        for (const auto& attr : node.attr()) Require(attrs.emplace(attr.name(), attr).second, "duplicate graph attribute");
        Require(attrs.contains("is_cpu_op") && attrs.at("is_cpu_op").has_bool_val() &&
                !attrs.at("is_cpu_op").bool_val(), "graph resource mismatch");
        if (n.at("kind") == "COMP")
            Require(node.type() == ChakraProtoMsg::COMP_NODE && node.duration_micros() == n.at("duration_ns") &&
                    attrs.size() == 1, "compute graph differs from plan");
        else
        {
            const auto& t = *transfers.at(n.at("transfer_id").get<uint64_t>());
            Require(node.type() == (n.at("kind") == "SEND" ? ChakraProtoMsg::COMM_SEND_NODE : ChakraProtoMsg::COMM_RECV_NODE) &&
                    node.duration_micros() == 0 && attrs.size() == 5, "communication graph type mismatch");
            Require(attrs.contains("comm_size") && attrs.at("comm_size").has_int64_val() &&
                    attrs.at("comm_size").int64_val() == t.at("payload_bytes").get<int64_t>(), "graph payload differs from plan");
            for (const auto& [name, field] : std::vector<std::pair<std::string, std::string>>{
                     {"comm_src", "src_rank"}, {"comm_dst", "dst_rank"}, {"comm_tag", "comm_tag"}})
                Require(attrs.contains(name) && attrs.at(name).has_int32_val() &&
                        attrs.at(name).int32_val() == t.at(field).get<int>(), "graph matching key differs from plan");
        }
    }
    ChakraProtoMsg::Node extra;
    Require(!ReadMessage(input, extra), "extra graph node");
}
}
void CheckPlanGraph(const std::filesystem::path& path, const Json& graph,
                    const std::map<uint64_t, Json>& transfers)
{
    std::map<uint64_t, const Json*> references;
    for (const auto& [id, transfer] : transfers) references.emplace(id, &transfer);
    CheckPlanGraph(path, graph, references);
}
void CheckPlanGraph(const std::filesystem::path& path, const Json& graph,
                    const std::map<uint64_t, const Json*>& transfers)
{
    try { CheckGraph(path, graph, transfers); }
    catch (const std::exception& error) { throw std::runtime_error(path.string() + ": " + error.what()); }
}
uint64_t ValidateBatchLimits(const Json& limits)
{
    Require(limits.is_object() && limits.size() == 2, "invalid batch limits");
    const auto sequences = Number(limits.at("max_num_seqs"), "batch sequence budget", 4096);
    const auto tokens = Number(limits.at("max_num_batched_tokens"), "batch Token budget", 4096);
    Require(sequences > 0 && tokens > 0, "zero batch budget");
    return std::min(sequences, tokens);
}
void ValidateDeployment(const Json& deployment, const Json& ranks)
{
    Require(deployment.is_object() && deployment.size() == 3 &&
            deployment.at("network_scope") == "cross_chip", "invalid deployment/scope");
    const auto& entries = deployment.at("ranks");
    Require(entries.is_array() && entries.size() == ranks.size(), "incomplete physical ownership");
    std::set<std::string> dies;
    std::map<std::string, std::string> chips;
    for (size_t i = 0; i < entries.size(); ++i)
    {
        const auto& entry = entries[i];
        Require(entry.is_object() && entry.size() == 4 && entry.at("rank_id") == ranks[i],
                "physical rank order/fields mismatch");
        Number(entry.at("rank_id"), "physical rank", INT32_MAX - 1);
        for (const auto* key : {"die_id", "chip_id", "server_id"})
            Require(entry.at(key).is_string() && !entry.at(key).get<std::string>().empty(), "empty physical identity");
        Require(dies.insert(entry.at("die_id").get<std::string>()).second, "duplicate global die identity");
        const auto chip = entry.at("chip_id").get<std::string>(), server = entry.at("server_id").get<std::string>();
        const auto [it, added] = chips.emplace(chip, server);
        Require(added || it->second == server, "global chip belongs to multiple servers");
    }
    const auto& profile = deployment.at("local_prepare_ns_per_vector");
    Require(profile.is_object() && profile.size() == 2, "invalid local prepare profile");
    for (const auto* kind : {"dispatch", "combine"}) Number(profile.at(kind), "local prepare time", INT64_MAX);
}

LayerPlan LoadLayerPlan(const std::filesystem::path& directory, PlanRetention retention)
{
    Json resource_budget = nullptr;
    if (std::filesystem::exists(directory / "graph-resources.json"))
    {
        Require(std::filesystem::file_size(directory / "graph-resources.json") <= 4096, "oversized resource budget file");
        std::ifstream budget_file(directory / "graph-resources.json"); resource_budget=Json::parse(budget_file);
        resources::Validate(resource_budget);
        Require(std::filesystem::file_size(directory / "plan.json") <= resource_budget.at("max_plan_bytes").get<uint64_t>(), "plan exceeds max_plan_bytes");
    }
    std::ifstream stream(directory / "plan.json");
    Require(bool(stream), "missing plan.json");
    PackedPlanJson parsed;
    if (retention == PlanRetention::Execution) parsed = ParsePackedPlanJson(stream);
    else parsed.data = Json::parse(stream);
    auto& p = parsed.data;
    if (p.at("schema_version") != "moe-ub-full-model-plan/1") parsed.Restore();
    Require(resources::Budget(p.at("input")) == resource_budget, "plan/resource budget sidecar mismatch");
    if (!resource_budget.is_null()) resources::Check(resources::Actual(p),resource_budget);
    if (p.at("schema_version") == "moe-ub-full-model-plan/1")
        return LoadFullModelPlan(directory, std::move(p), std::move(parsed.layers));
    if (p.at("schema_version") == "moe-ub-multilayer-plan/1" || p.at("schema_version") == "moe-ub-multilayer-plan/2")
        return LoadMultilayerPlan(directory, p);
    if (p.at("communication_template") == "kv_direct_v1") return LoadKvPlan(directory, p);
    auto plan = ValidateLayerPlan(p);
    std::map<uint64_t, Json> transfers;
    for (const auto& t : p.at("transfers")) transfers.emplace(t.at("transfer_id"), t);
    for (const auto& graph : p.at("rank_graphs"))
        CheckPlanGraph(directory / ("layer." + std::to_string(graph.at("rank_id").get<int>()) + ".et"), graph, transfers);
    return plan;
}
LayerPlan ValidateLayerPlan(const Json& p, bool retain_data)
{
    const bool cann = p.at("communication_template") == "cann_a3_v850_fullmesh_v1";
    const bool batched = p.at("schema_version") == "moe-ub-execution-plan/3";
    const bool physical = p.at("schema_version") == "moe-ub-execution-plan/2" || (batched && p.contains("deployment"));
    Require((batched || physical || p.at("schema_version") == "moe-ub-execution-plan/1") &&
            (cann || p.at("communication_template") == "direct_rank_dedup_v1") &&
            p.at("sender_completion") == "transaction_done", "unsupported schema, mapping or template");
    const auto& ranks = p.at("rank_ids");
    Require(ranks.is_array() && !ranks.empty(), "require nonempty explicit group members");
    std::set<int> members;
    int previous = -1;
    for (const auto& value : ranks)
    {
        const int rank = Number(value, "rank_id", INT32_MAX - 1);
        Require(rank > previous && members.insert(rank).second, "unordered or repeated rank");
        previous = rank;
    }
    const auto& mapping = p.at("rank_to_ub_node");
    Require(mapping.is_array() && mapping.size() == ranks.size(), "incomplete UB mapping");
    std::map<int, uint32_t> endpoints;
    std::set<uint32_t> unique_endpoints;
    for (size_t i = 0; i < ranks.size(); ++i)
    {
        const auto node = Number(mapping[i], "UB node", UINT32_MAX);
        Require(unique_endpoints.insert(node).second, "duplicate UB endpoint");
        endpoints.emplace(ranks[i].get<int>(), node);
    }
    Require(p.at("input").at("rank_ids") == ranks && p.at("input").at("rank_to_ub_node") == mapping,
            "input rank mapping mismatch");
    const auto slots = Number(p.at("send_slots_per_rank"), "send slots", 2);
    const auto& input = p.at("input");
    Require(input.at("schema_version") == (batched ? "moe-ub-layer-fixture/3" : physical ? "moe-ub-layer-fixture/2" : "moe-ub-layer-fixture/1") &&
            input.contains("deployment") == physical && p.contains("deployment") == physical,
            "plan/input deployment schema mismatch");
    std::map<int, std::string> chips;
    if (physical)
    {
        ValidateDeployment(input.at("deployment"), ranks);
        Require(p.at("deployment") == input.at("deployment"), "plan/input deployment mismatch");
        for (const auto& entry : input.at("deployment").at("ranks"))
            chips.emplace(entry.at("rank_id").get<int>(), entry.at("chip_id").get<std::string>());
    }
    auto same_chip = [&](int src, int dst) { return physical && src != dst && chips.at(src) == chips.at(dst); };
    std::map<int, uint64_t> local_tokens, expert_inputs;
    for (int rank : members) { local_tokens[rank] = 0; expert_inputs[rank] = 0; }
    const auto& tokens = input.at("tokens");
    const auto& placement = input.at("expert_placement");
    const bool prefill = input.at("role") == "P";
    Require(prefill || input.at("role") == "D", "invalid execution role");
    Require(input.contains("batch_limits") == batched && p.contains("batch_limits") == batched &&
            input.contains("compute_dtypes") == batched && p.contains("compute_inputs") == batched,
            "batching fields/schema mismatch");
    uint64_t batch_limit = 1;
    if (batched)
    {
        Require(!prefill && p.at("batch_limits") == input.at("batch_limits"), "batch role/limits mismatch");
        batch_limit = ValidateBatchLimits(input.at("batch_limits"));
    }
    Require(tokens.is_array() && tokens.size() >= 1 && tokens.size() <= (batched ? 4096 : prefill ? 16 : ranks.size()) &&
            placement.is_array() && !placement.empty(), "invalid Token/placement set");
    for (const auto& value : placement) Require(members.contains(Number(value, "expert rank", INT32_MAX - 1)), "expert outside group");
    const auto top_k = Number(input.at("top_k"), "top_k", placement.size());
    Require(top_k > 0, "Top-K must be positive");
    const auto layer_id = Number(input.at("layer_id"), "layer_id", INT32_MAX);
    const auto hidden = Number(input.at("hidden_dim"), "hidden_dim", INT64_MAX);
    Require(hidden > 0 && input.at("template") == p.at("communication_template") &&
            Number(input.at("application_metadata_bytes"), "application metadata", 0) == 0,
            "invalid input vector/template/metadata");
    for (const auto* field : {"dispatch_element_bytes", "combine_element_bytes"})
    {
        const auto element = Number(input.at(field), field, 16);
        Require(element > 0 && hidden <= uint64_t(INT64_MAX) / element, "vector byte overflow");
    }
    std::set<std::tuple<std::string, std::string, uint64_t, uint64_t>> token_ids;
    std::set<std::string> batch_requests;
    std::map<uint64_t, uint64_t> per_expert;
    for (const auto& token : tokens)
    {
        const int rank = Number(token.at("src_rank"), "Token rank", INT32_MAX - 1);
        Require(members.contains(rank), "nonmember local Token");
        ++local_tokens[rank];
        Require(prefill || local_tokens[rank] <= batch_limit, "repeated local decode Token or exceeded batch budget");
        Require(token.at("phase") == (prefill ? "prefill" : "decode"), "Token phase mismatch");
        const auto& experts = token.at("expert_ids");
        Require(experts.is_array() && experts.size() == top_k, "invalid Top-K route");
        std::set<uint64_t> selected;
        for (const auto& expert : experts)
        {
            const auto id = Number(expert, "expert", placement.size() - 1);
            Require(selected.insert(id).second, "duplicate expert choice");
            ++expert_inputs[placement.at(id).get<int>()];
            ++per_expert[id];
        }
        const auto request = token.at("request_id").get<std::string>();
        Require(!batched || batch_requests.insert(request).second, "decode request repeated in this iteration");
        Require(!request.empty() && Number(token.at("layer_id"), "Token layer_id", INT32_MAX) == layer_id &&
                token_ids.emplace(request, token.at("phase").get<std::string>(),
                    Number(token.at("token_index"), "Token index", INT64_MAX), layer_id).second,
                "invalid or duplicate Token identity");
    }
    Json batches = Json::array(), counts = Json::array();
    for (int rank : members)
    {
        Json requests = Json::array();
        for (const auto& token : tokens)
            if (token.at("src_rank") == rank && std::find(requests.begin(), requests.end(), token.at("request_id")) == requests.end())
                requests.push_back(token.at("request_id"));
        batches.push_back({{"rank_id", rank}, {"request_ids", requests}});
        counts.push_back(expert_inputs[rank]);
    }
    Require(p.at("instance_batches") == batches && p.at("expert_input_counts") == counts,
            "Token participation/expert counts mismatch");
    if (batched)
    {
        const auto& dtypes = input.at("compute_dtypes");
        const std::map<std::string, unsigned> sizes = {{"float16",2},{"bfloat16",2},{"float32",4},{"fp8",1},{"int8",1}};
        Require(dtypes.is_object() && dtypes.size() == 4, "invalid compute dtypes");
        for (const std::string key : {"compute", "kv", "dispatch", "combine"})
        {
            Require(dtypes.at(key).is_string(), "non-string compute dtype");
            const auto type = dtypes.at(key).get<std::string>();
            Require(sizes.contains(type) && (key == "kv" || type != "fp8") &&
                    (type != "int8" || (cann && key == "dispatch")), "unsupported compute dtype");
            if (key == "dispatch" || key == "combine")
                Require(input.at(key + "_element_bytes") == sizes.at(type), "compute dtype/vector mismatch");
        }
        Json shapes = Json::array();
        for (int rank : members)
        {
            Json requests = Json::array(), experts = Json::array();
            for (const auto& token : tokens)
                if (token.at("src_rank") == rank)
                {
                    const auto index = Number(token.at("token_index"), "batch context", INT64_MAX - 1);
                    requests.push_back({{"request_id", token.at("request_id")}, {"token_index",index},
                        {"context_tokens",index+1}, {"query_tokens",1}});
                }
            for (size_t e = 0; e < placement.size(); ++e)
                if (placement[e] == rank) experts.push_back({{"expert_id",e}, {"input_tokens",per_expert[e]}});
            shapes.push_back({{"rank_id",rank}, {"requests",requests}, {"batch_size",requests.size()}, {"expert_inputs",experts}});
        }
        const bool profiled = input.contains("operator_profile");
        const Json expected = {{"basis",profiled ? "exact_operator_profile_with_reference_residual" : "linear_reference_per_token_and_expert_input"},
            {"context_sensitive",profiled}, {"expert_distribution_sensitive",profiled},
            {"hidden_dim",hidden}, {"dtypes",dtypes}, {"ranks",shapes}};
        Require(p.at("compute_inputs") == expected, "batch compute shapes differ from Tokens/experts");
    }
    Require(input.contains("operator_profile") == p.contains("compute_evaluation"), "profile evaluation missing or unexpected");
    std::map<int, Json> measured;
    if (input.contains("operator_profile"))
    {
        const auto evaluation = EvaluateOperatorProfile(input);
        Require(evaluation == p.at("compute_evaluation"), "operator profile evaluation mismatch");
        for (const auto& row : evaluation.at("ranks")) measured[row.at("rank_id").get<int>()] = row.at("operators");
    }
    Require(slots > 0 && p.at("send_slots_per_rank") == input.at("send_slots_per_rank"), "send slot configuration mismatch");
    const auto chunk_bytes = Number(input.at("chunk_bytes"), "chunk_bytes", UINT32_MAX);
    Require(chunk_bytes > 0, "zero chunk limit");
    const auto ready = Number(p.at("ready_ns"), "ready_ns", INT64_MAX - 1000000);
    Require(input.at("ready_ns") == ready && input.at("graph_id") == p.at("graph_id") &&
            input.at("wave_id") == p.at("wave_id"), "input graph identity/ready mismatch");
    if (cann) return ValidateCannPlan(p, retain_data);
    LayerPlan plan{retain_data ? p : Json{}, {}, {}, {}};
    for (int rank : members)
    {
        uint64_t total = 0;
        for (const auto* field : {"attention_route_pack", "expert_per_input", "expert_local_merge",
                                 "combine_unpack_merge", "next_layer"})
        {
            const std::string name(field);
            const auto duration = Number(input.at("compute_profile_ns").at(field), field, INT64_MAX - ready);
            Require(duration > 0 || name == "next_layer", "nonpositive compute profile");
            const auto count = name == "expert_per_input" ? expert_inputs[rank] :
                name == "expert_local_merge" ? uint64_t(expert_inputs[rank] != 0) : local_tokens[rank];
            Require(!count || duration <= (uint64_t(INT64_MAX) - ready - total) / count,
                    "rank computation duration overflow");
            uint64_t work = count * duration;
            if (!measured.empty())
            {
                if (name == "expert_per_input") work = measured.at(rank).at("expert_ffn").at("duration_ns");
                if (name == "attention_route_pack")
                {
                    const uint64_t attention = measured.at(rank).at("attention_decode").at("duration_ns");
                    Require(attention <= uint64_t(INT64_MAX)-ready-total-work, "profile duration overflow");
                    work += attention;
                }
            }
            Require(work <= uint64_t(INT64_MAX)-ready-total, "profile total overflow");
            total += work;
        }
        if (physical)
            for (const std::string kind : {"dispatch", "combine"})
            {
                uint64_t count = 0;
                for (const auto& token : tokens)
                {
                    const int src = token.at("src_rank");
                    std::set<int> destinations;
                    for (const auto& expert : token.at("expert_ids")) destinations.insert(placement.at(expert.get<size_t>()).get<int>());
                    for (int dst : destinations)
                        if (same_chip(src, dst) && (kind == "dispatch" ? src : dst) == rank) ++count;
                }
                const auto duration = Number(input.at("deployment").at("local_prepare_ns_per_vector").at(kind), "local prepare", INT64_MAX);
                Require(!count || duration <= (uint64_t(INT64_MAX) - ready - total) / count,
                        "local preparation duration overflow");
                total += count * duration;
            }
    }
    const auto graph_id = p.at("graph_id").get<std::string>();
    Require(!graph_id.empty(), "empty graph id");
    std::map<std::string, std::string> operations;
    for (const auto& op : p.at("operations"))
    {
        const auto kind = op.at("kind").get<std::string>();
        Require((kind == "dispatch" || kind == "combine") && op.at("participants") == p.at("rank_ids") &&
                operations.emplace(op.at("operation_id").get<std::string>(), kind).second, "invalid operation registry");
    }
    Require(operations.size() == 2 && p.at("operations")[0].at("kind") == "dispatch" &&
            p.at("operations")[1].at("kind") == "combine", "require Dispatch and Combine operations");
    std::map<uint64_t, Json> transfers;
    std::set<uint64_t> tasks, tags;
    Require(p.at("transfers").is_array() && p.at("transfers").size() <= 4096, "invalid/oversized transfer set");
    std::map<std::tuple<std::string, uint64_t, uint64_t>, std::vector<Json>> chunks;
    std::map<std::tuple<std::string, uint64_t, uint64_t>, uint64_t> expected_pairs, actual_pairs;
    std::map<std::tuple<std::string, uint64_t, uint64_t>, Json> expected_sources;
    Json contributions = Json::array();
    for (size_t record = 0; record < tokens.size(); ++record)
    {
        const auto& token = tokens[record];
        const auto src = token.at("src_rank").get<uint64_t>();
        std::map<uint64_t, std::set<uint64_t>> destinations;
        for (const auto& expert : token.at("expert_ids"))
            destinations[placement.at(expert.get<size_t>()).get<uint64_t>()].insert(expert.get<uint64_t>());
        if (physical)
            for (const auto& [dst, experts] : destinations)
                contributions.push_back({{"source_record_index", record}, {"src_rank", src}, {"dst_rank", dst},
                    {"expert_ids", experts}, {"locality", src == dst ? "same_rank" : same_chip(src, dst) ? "same_chip" : "cross_chip"},
                    {"dispatch_bytes", hidden * input.at("dispatch_element_bytes").get<uint64_t>()},
                    {"combine_bytes", hidden * input.at("combine_element_bytes").get<uint64_t>()}});
        destinations.erase(src);
        for (const auto& [dst, experts] : destinations)
        {
            const Json source = {{"request_id", token.at("request_id")}, {"phase", token.at("phase")},
                {"token_index", token.at("token_index")}, {"source_record_index", record},
                {"layer_id", token.at("layer_id")}, {"expert_ids", experts}};
            for (const auto& key : {std::make_tuple(std::string("dispatch"), src, dst),
                                   std::make_tuple(std::string("combine"), dst, src)})
            {
                ++expected_pairs[key];
                if (!expected_sources.contains(key)) expected_sources[key] = Json::array();
                expected_sources[key].push_back(source);
            }
        }
    }
    if (physical) Require(p.at("contributions") == contributions, "contribution locality/routes mismatch");
    for (const auto& t : p.at("transfers"))
    {
        const auto id = Number(t.at("transfer_id"), "transfer_id");
        const auto tag = Number(t.at("comm_tag"), "comm_tag", 499999999);
        const auto src = Number(t.at("src_rank"), "src_rank", INT32_MAX - 1), dst = Number(t.at("dst_rank"), "dst_rank", INT32_MAX - 1);
        Require(members.contains(src) && members.contains(dst), "delivery endpoint outside group");
        const bool local = same_chip(src, dst);
        const auto bytes = Number(t.at("payload_bytes"), "payload_bytes", local ? INT64_MAX : UINT32_MAX);
        uint64_t task = 0;
        if (local) Require(!t.contains("ub_task_id"), "local delivery has UB task");
        else { task = Number(t.at("ub_task_id"), "ub_task_id", UINT32_MAX);
               Require(tasks.insert(task).second, "duplicate UB task"); }
        const auto operation = t.at("operation_id").get<std::string>();
        Require(id > 0 && tag > 0 && bytes > 0 && src != dst && members.contains(src) && members.contains(dst) && transfers.emplace(id, t).second &&
                tags.insert(tag).second, "invalid/duplicate transfer identity or size");
        Require(operations.contains(operation) && operations.at(operation) == t.at("kind") &&
                t.at("business_payload_bytes") == bytes && t.at("application_metadata_bytes") == 0 &&
                t.at("task_byte_range") == (local ? Json(nullptr) : Json::array({0, bytes})) &&
                t.at("priority") == (local ? Json(nullptr) : Json(7)) &&
                t.at("transport_profile_id") == (local ? "same-chip-local-reference" : "urma-write-rtp-default"), "invalid provenance/profile");
        const auto chunk = Number(t.at("chunk_index"), "chunk_index", 4095);
        const auto total = Number(t.at("pair_payload_bytes"), "pair_payload_bytes", INT64_MAX);
        const auto& range = t.at("pair_byte_range");
        Require(range.is_array() && range.size() == 2, "invalid pair range");
        const auto begin = Number(range[0], "pair begin", total), end = Number(range[1], "pair end", total);
        Require(total > 0 && end >= begin && end - begin == bytes && (local ? chunk == 0 && begin == 0 && bytes == total : bytes <= chunk_bytes),
                "invalid chunk range/size");
        chunks[{operation, src, dst}].push_back(t);
        if (local) plan.local_descriptors.push_back({id, graph_id,
            Number(t.at("send_node_id"), "send_node_id"), Number(t.at("recv_node_id"), "recv_node_id"),
            static_cast<int>(src), static_cast<int>(dst), static_cast<int>(tag), bytes, operation});
        else plan.descriptors.push_back({id, task, graph_id,
            Number(t.at("send_node_id"), "send_node_id"), Number(t.at("recv_node_id"), "recv_node_id"),
            static_cast<int>(src), static_cast<int>(dst), static_cast<int>(tag), bytes, operation,
            static_cast<uint32_t>(chunk), begin, total, endpoints.at(src), endpoints.at(dst)});
    }
    for (const auto& [key, parts] : chunks)
    {
        const auto& first = parts.front();
        const auto kind = first.at("kind").get<std::string>();
        const auto hidden = Number(input.at("hidden_dim"), "hidden_dim", INT64_MAX);
        const auto element = Number(input.at(kind + "_element_bytes"), "element bytes", 16);
        const auto& sources = first.at("sources");
        Require(hidden > 0 && element > 0 && sources.is_array() && !sources.empty() &&
                sources.size() <= tokens.size() && hidden <= uint64_t(INT64_MAX) / element / sources.size(), "invalid vector sources/size");
        const auto pair_key = std::make_tuple(kind, first.at("src_rank").get<uint64_t>(),
                                             first.at("dst_rank").get<uint64_t>());
        Require(expected_sources.contains(pair_key) && sources == expected_sources.at(pair_key),
                "transfer sources differ from Token routes");
        const auto expected = hidden * element * sources.size();
        actual_pairs[{kind, first.at("src_rank").get<uint64_t>(), first.at("dst_rank").get<uint64_t>()}] = sources.size();
        uint64_t offset = 0, index = 0;
        for (const auto& part : parts)
        {
            const auto limit = first.at("transport_profile_id") == "same-chip-local-reference" ? expected : chunk_bytes;
            const auto size = std::min(limit, expected - offset);
            Require(size > 0 && part.at("chunk_index") == index++ && part.at("payload_bytes") == size &&
                    part.at("pair_payload_bytes") == expected && part.at("pair_byte_range") == Json::array({offset, offset + size}) &&
                    part.at("sources") == sources, "invalid chunk order, tail, range or source");
            offset += size;
        }
        Require(offset == expected, "incomplete directed vector");
    }
    Require(actual_pairs == expected_pairs, "transfer set/bytes differ from Token routes");
    Require(p.at("rank_graphs").is_array() && p.at("rank_graphs").size() == ranks.size(), "missing rank graph");
    std::map<std::pair<uint64_t, std::string>, unsigned> pairs;
    for (size_t index = 0; index < ranks.size(); ++index)
    {
        const int rank = ranks[index];
        const auto& graph = p.at("rank_graphs")[index];
        Require(graph.at("rank_id") == rank, "rank graph order/mapping mismatch");
        std::set<uint64_t> seen;
        std::map<uint64_t, Json> local;
        for (const auto& n : graph.at("nodes"))
        {
            const auto id = Number(n.at("node_id"), "node_id");
            Require(id > 0 && n.at("rank_id") == rank && !seen.contains(id), "duplicate node or wrong rank");
            for (auto dep : Ids(n.at("deps"))) Require(seen.contains(dep), "nonlocal, cyclic or unordered dependency");
            seen.insert(id); local.emplace(id, n);
            if (retain_data) plan.nodes.emplace(std::make_pair(rank, id), n);
            if (n.at("kind") == "COMP")
            {
                Require(Number(n.at("duration_ns"), "duration_ns", INT64_MAX - p.at("ready_ns").get<uint64_t>()) > 0,
                        "computation nodes require positive durations; omit absent work");
                Require(n.at("resource_id") == "gpu_compute", "unknown compute resource");
            }
            else
            {
                const auto kind = n.at("kind").get<std::string>();
                Require(kind == "SEND" || kind == "RECV", "unsupported node type");
                const auto id_transfer = Number(n.at("transfer_id"), "node transfer_id");
                Require(transfers.contains(id_transfer), "unknown node transfer");
                const auto& t = transfers.at(id_transfer);
                const auto side = kind == "SEND" ? "send" : "recv";
                Require(t.at(kind == "SEND" ? "src_rank" : "dst_rank") == rank &&
                        t.at(std::string(side) + "_node_id") == id, "mismatched SEND/RECV identity");
                ++pairs[{id_transfer, side}];
            }
        }
        const auto& phase = graph.at("phase_nodes");
        std::set<uint64_t> phase_ids;
        Require(phase.size() == 5, "invalid phase fields");
        auto compute = [&](const char* field, const char* profile, uint64_t count,
                           const std::set<uint64_t>& deps) -> std::set<uint64_t> {
            const auto duration = Number(input.at("compute_profile_ns").at(profile), "compute profile", INT64_MAX);
            Require((duration > 0 || std::string(field) == "next_layer") &&
                    (!count || duration <= uint64_t(INT64_MAX) / count), "invalid compute profile");
            uint64_t work = count * duration;
            if (!measured.empty())
            {
                if (std::string(field) == "expert") work = measured.at(rank).at("expert_ffn").at("duration_ns");
                if (std::string(field) == "prepare") work += measured.at(rank).at("attention_decode").at("duration_ns").get<uint64_t>();
            }
            if (!work)
            {
                Require(phase.at(field).is_null(), "absent work must not have a compute node");
                return deps;
            }
            const auto id = Number(phase.at(field), "phase node");
            Require(local.contains(id) && local.at(id).at("kind") == "COMP" && phase_ids.insert(id).second &&
                    local.at(id).at("duration_ns") == work && Ids(local.at(id).at("deps")) == deps,
                    "compute phase work/duration/dependency mismatch");
            return {id};
        };
        std::map<std::string, std::pair<int, uint64_t>> previous_send;
        auto communicate = [&](const char* kind, const std::set<uint64_t>& deps) {
            auto completed = deps;
            auto local_ready = deps;
            if (physical)
            {
                const auto& prepares = graph.at("local_prepare_nodes");
                Require(prepares.is_object() && prepares.size() == 2, "invalid local prepare fields");
                uint64_t count = 0;
                for (const auto& [id, t] : transfers)
                    if (t.at("src_rank") == rank && t.at("kind") == kind && t.at("transport_profile_id") == "same-chip-local-reference")
                        count += t.at("sources").size();
                const auto duration = Number(input.at("deployment").at("local_prepare_ns_per_vector").at(kind), "local profile", INT64_MAX);
                Require(!count || duration <= uint64_t(INT64_MAX) / count, "local prepare overflow");
                if (!count || !duration) Require(prepares.at(kind).is_null(), "unexpected local prepare work");
                else
                {
                    const auto id = Number(prepares.at(kind), "local prepare node");
                    Require(local.contains(id) && local.at(id).at("kind") == "COMP" && phase_ids.insert(id).second &&
                            local.at(id).at("duration_ns") == duration * count && Ids(local.at(id).at("deps")) == deps,
                            "local prepare duration/dependency mismatch");
                    local_ready = {id};
                }
            }
            for (const auto& [node_id, n] : local)
            {
                if (n.at("kind") == "COMP") continue;
                const auto& t = transfers.at(n.at("transfer_id").get<uint64_t>());
                if (t.at("kind") != kind) continue;
                const auto& expected_deps = n.at("kind") == "SEND" && t.at("transport_profile_id") == "same-chip-local-reference" ? local_ready : deps;
                Require(Ids(n.at("deps")) == expected_deps, "communication template dependency mismatch");
                completed.insert(node_id);
                if (n.at("kind") == "SEND")
                {
                    auto order = std::make_pair(t.at("dst_rank").get<int>(), t.at("chunk_index").get<uint64_t>());
                    Require(!previous_send.contains(kind) || previous_send.at(kind) < order, "SEND order is not by destination/chunk");
                    previous_send[kind] = order;
                }
            }
            return completed;
        };
        auto ready = compute("prepare", "attention_route_pack", local_tokens[rank], {});
        ready = communicate("dispatch", ready);
        ready = compute("expert", "expert_per_input", expert_inputs[rank], ready);
        ready = compute("local_merge", "expert_local_merge", expert_inputs[rank] ? 1 : 0, ready);
        ready = communicate("combine", ready);
        ready = compute("finish", "combine_unpack_merge", local_tokens[rank], ready);
        compute("next_layer", "next_layer", local_tokens[rank], ready);
        for (const auto& [id, n] : local)
            if (n.at("kind") == "COMP") Require(phase_ids.contains(id), "unexpected compute node");
    }
    for (const auto& [id, t] : transfers)
        Require(pairs[{id, "send"}] == 1 && pairs[{id, "recv"}] == 1, "missing/duplicate SEND/RECV pair");
    return plan;
}

Json ReadLayerExecution(const LayerPlan& plan, const std::filesystem::path& path, bool graph_identity)
{
    std::ifstream stream(path);
    Require(bool(stream), "missing ASTRA execution log");
    std::regex pattern("(issue|callback),sys->id=([0-9]+), tick=([0-9]+), node->id=([0-9]+),");
    std::map<std::pair<int, uint64_t>, Json> events;
    std::string line;
    while (std::getline(stream, line))
    {
        std::smatch match;
        if (!std::regex_search(line, match, pattern)) continue;
        if (graph_identity)
        {
            const auto marker = line.find(", graph_id=");
            Require(marker != std::string::npos, "missing execution graph identity");
            if (line.substr(marker + 11) != plan.data.at("graph_id").get<std::string>()) continue;
        }
        const auto key = std::make_pair(std::stoi(match[2]), std::stoull(match[4]));
        Require(plan.nodes.contains(key), "execution references unknown node");
        auto field = match[1] == "issue" ? "start_ns" : "end_ns";
        Require(!events[key].contains(field), "duplicate ASTRA execution event");
        events[key][field] = std::stoull(match[3]);
    }
    Json result = Json::array();
    for (const auto& [key, node] : plan.nodes)
    {
        auto record = events.at(key);
        Require(record.contains("start_ns") && record.contains("end_ns") && record["end_ns"] >= record["start_ns"], "incomplete node execution");
        if (node.kind == "COMP") Require(record["end_ns"].get<uint64_t>() - record["start_ns"].get<uint64_t>() == node.duration_ns, "compute duration mismatch");
        for (const auto dep : node.deps)
            Require(record["start_ns"] >= events.at({key.first, dep}).at("end_ns"), "execution violated local dependency");
        record["rank_id"] = key.first; record["node_id"] = key.second; record["kind"] = node.kind;
        result.push_back(record);
    }
    return result;
}
Json LayerEndpointOperations(const LayerPlan& plan, const Json& execution)
{
    ExecutionTimes times;
    for (const auto& event : execution)
        times[{event.at("rank_id"), event.at("node_id")}] =
            {event.at("start_ns"), event.at("end_ns"), true, true};
    return LayerEndpointOperations(plan, times);
}
Json LayerEndpointOperations(const LayerPlan& plan, const ExecutionTimes& execution)
{
    Json endpoints = Json::array();
    if (plan.data.at("communication_template") == "kv_direct_v1")
    {
        std::map<int, uint64_t> ends;
        for (const auto& [key, event] : execution) ends[key.first] = std::max(ends[key.first], event.end_ns);
        for (const int rank : plan.data.at("rank_ids"))
        {
            endpoints.push_back({{"rank_id", rank}, {"local_record_id", 1}, {"kind", "KV_TRANSFER"},
                {"start_local_ns", plan.data.at("ready_ns")},
                {"end_local_ns", std::max(ends[rank], plan.data.at("ready_ns").get<uint64_t>())}});
        }
        return endpoints;
    }
    // One pass over transfers, and one timing index for the whole graph. Status
    // messages keep their original phase dependencies; only the same dispatch/
    // combine data nodes used by the old endpoint definition enter these maxima.
    std::map<std::pair<std::string, int>, uint64_t> communication;
    for (const auto& transfer : plan.data.at("transfers"))
        if (transfer.at("kind") == "dispatch" || transfer.at("kind") == "combine")
            for (const bool send : {true, false})
            {
                const int rank = transfer.at(send ? "src_rank" : "dst_rank");
                auto& end = communication[{transfer.at("operation_id").get<std::string>(), rank}];
                end = std::max(end, execution.at({rank, transfer.at(send ? "send_node_id" : "recv_node_id").get<uint64_t>()}).end_ns);
            }
    const bool layered = plan.data.contains("layers");
    const size_t count = layered ? plan.data.at("layers").size() : 1;
    for (size_t index = 0; index < count; ++index)
    {
      const auto& layer = layered ? plan.data.at("layers")[index] : plan.data;
      std::map<std::string, std::string> operations;
      for (const auto& op : layer.at("operations")) operations[op.at("kind")] = op.at("operation_id");
      for (const auto& rank_graph : plan.data.at("rank_graphs"))
      {
        const auto& graph = layered ? rank_graph.at("layer_phases")[index] : rank_graph;
        int rank = rank_graph.at("rank_id");
        const auto& p = graph.at("phase_nodes");
        auto end = [&](const char* phase, uint64_t fallback) {
            return p.at(phase).is_null() ? fallback :
                execution.at({rank, p.at(phase).get<uint64_t>()}).end_ns;
        };
        auto communication_end = [&](const char* kind, uint64_t earliest) {
            const auto op = operations.find(kind);
            return op == operations.end() ? earliest : std::max(earliest, communication[{op->second, rank}]);
        };
        uint64_t ready = layer.at("ready_ns");
        if (graph.contains("entry_deps"))
            for (const auto& dep : graph.at("entry_deps"))
                ready = std::max(ready, execution.at({rank, dep.get<uint64_t>()}).end_ns);
        const auto prepared = end("prepare", ready);
        const auto dispatched = p.contains("dispatch_reorder") ? end("dispatch_reorder", prepared) : communication_end("dispatch", prepared);
        const auto merged = end("local_merge", end("expert", dispatched));
        const auto finished = end("finish", communication_end("combine", merged));
        endpoints.push_back({{"rank_id", rank}, {"local_record_id", 1 + 2 * index}, {"kind", "dispatch"},
            {"start_local_ns", prepared}, {"end_local_ns", dispatched}});
        endpoints.push_back({{"rank_id", rank}, {"local_record_id", 2 + 2 * index}, {"kind", "combine"},
            {"start_local_ns", merged}, {"end_local_ns", finished}});
      }
    }
    return endpoints;
}
Json LayerOperationRegistry(const LayerPlan& plan)
{
    Json result = plan.data.at("operations");
    for (auto& operation : result)
    {
        uint64_t offset = 0;
        if (plan.data.contains("executed_layer_ids"))
        {
            const auto& ids = plan.data.at(plan.data.contains("communication_layer_ids") ? "communication_layer_ids" : "executed_layer_ids");
            const auto found = std::find(ids.begin(), ids.end(), operation.at("layer_id"));
            Require(found != ids.end(), "operation outside executed layers");
            offset = 2 * std::distance(ids.begin(), found);
        }
        operation["graph_id"] = plan.data.at("graph_id");
        operation["endpoint_records"] = Json::array();
        for (const int rank : plan.data.at("rank_ids"))
            operation["endpoint_records"].push_back({{"rank_id", rank},
                {"local_record_id", offset + (operation.at("kind") == "combine" ? 2 : 1)}});
    }
    return result;
}
}
