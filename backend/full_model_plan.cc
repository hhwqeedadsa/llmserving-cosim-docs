#include "graph_resources.hh"
#include "layer_plan.hh"

#include <algorithm>
#include <set>
#include <sstream>
#include <stdexcept>

namespace moe_ub
{
namespace
{
using Json = nlohmann::json;
void Require(bool ok, const std::string& text)
{
    if (!ok) throw std::runtime_error("Full model: " + text);
}
void Fields(const Json& value, const std::string& names)
{
    std::istringstream stream(names); std::string name; size_t count = 0;
    Require(value.is_object(), "expected object");
    while (stream >> name) { Require(value.contains(name), "missing " + name); ++count; }
    Require(value.size() == count, "unexpected fields: " + names);
}
uint64_t Number(const Json& value, uint64_t minimum = 0)
{
    Require(value.is_number_unsigned() && value.get<uint64_t>() >= minimum &&
            value.get<uint64_t>() <= uint64_t(INT64_MAX), "invalid integer");
    return value.get<uint64_t>();
}
uint64_t Product(uint64_t a, uint64_t b)
{
    Require(!a || b <= uint64_t(INT64_MAX) / a, "stage cost overflow");
    return a * b;
}
Json Common(Json input)
{
    for (auto key : {"layer_id", "tokens", "compute_profile_ns", "schema_version", "expert_placement", "top_k", "operator_profile"})
        input.erase(key);
    return input;
}
Json Tokens(const Json& layer)
{
    auto tokens = layer.at("input").at("tokens");
    for (auto& token : tokens) { token.erase("layer_id"); token.erase("expert_ids"); }
    return tokens;
}
const std::string common_costs = "attention_norm mla_prologue mla_output attention_residual ffn_norm ffn_residual";
}

void ValidateFullModelProfile(const Json& model, const Json& profile, const Json& ranks)
{
    Fields(model, "schema_version name architecture source hidden_size vocab_size max_position_embeddings tp pp attention dtypes tie_word_embeddings layers weight_storage kv_cache");
    Require(model.at("schema_version") == "moe-ub-model/2" && model.at("architecture") == "bias_free_mla_rmsnorm_gated_ffn" &&
            Number(model.at("tp"), 1) == 1 && Number(model.at("pp"), 1) == 1, "unsupported model");
    Require(model.at("name").is_string() && !model.at("name").get<std::string>().empty(), "empty model name");
    Fields(model.at("source"), "kind description");
    Require((model.at("source").at("kind") == "synthetic" || model.at("source").at("kind") == "declared_reference") &&
            model.at("source").at("description").is_string() && !model.at("source").at("description").get<std::string>().empty(), "invalid source");
    for (auto key : {"hidden_size", "vocab_size", "max_position_embeddings"}) Number(model.at(key), 1);
    Fields(model.at("attention"), "num_heads q_lora_rank kv_lora_rank qk_nope_head_dim qk_rope_head_dim v_head_dim");
    for (const auto& value : model.at("attention")) Number(value, 1);
    Require(Number(model.at("attention").at("qk_rope_head_dim")) % 2 == 0, "odd RoPE dimension");
    Fields(model.at("dtypes"), "compute kv dispatch combine");
    Require(model.at("dtypes") == Json({{"compute", "bfloat16"}, {"kv", "bfloat16"}, {"dispatch", "int8"}, {"combine", "bfloat16"}}), "CANN dtype mismatch");
    Require(model.at("tie_word_embeddings").is_boolean(), "invalid tied flag");
    Require(model.at("kv_cache") == Json({{"layout", "absorbed_latent_rope"}, {"quantization", "none"}, {"padding_bytes_per_token", 0}}), "invalid KV layout");
    const auto& storage = model.at("weight_storage");
    Fields(storage, "mla dense_ffn routed_experts shared_experts embedding lm_head norm router router_correction");
    for (const auto& [name, row] : storage.items())
    {
        Fields(row, "dtype quantization scale_dtype zero_point padding_bytes");
        Require(Number(row.at("zero_point")) == 0 && Number(row.at("padding_bytes")) == 0, "unsupported storage padding/zero point");
        if (row.at("dtype") == "int8")
            Require((name == "mla" || name == "dense_ffn" || name == "routed_experts" || name == "shared_experts") &&
                    row.at("quantization") == "symmetric_per_output_channel" && row.at("scale_dtype") == "float32", "unsupported quantization");
        else
            Require((row.at("dtype") == "float32" || row.at("dtype") == "float16" || row.at("dtype") == "bfloat16") &&
                    row.at("quantization") == "none" && row.at("scale_dtype").is_null(), "unsupported floating storage");
    }
    Require(!model.at("tie_word_embeddings").get<bool>() || storage.at("embedding") == storage.at("lm_head"), "tied storage mismatch");
    const auto& blocks = model.at("layers");
    Require(blocks.is_array() && !blocks.empty() && blocks.size() <= 128, "invalid layer count");
    Fields(profile, "schema_version basis model_structure per_token_ns layers");
    auto structure = model;
    for (auto& row : structure["layers"]) row.erase("expert_placement");
    Require(profile.at("schema_version") == "moe-ub-full-model-cost/1" && profile.at("basis") == "declared_reference" &&
            profile.at("model_structure") == structure, "cost domain differs from model");
    Fields(profile.at("per_token_ns"), "embedding final_norm lm_head sampling");
    for (const auto& value : profile.at("per_token_ns")) Number(value, 1);
    Require(profile.at("layers").is_array() && profile.at("layers").size() == blocks.size(), "cost coverage differs");
    for (size_t i = 0; i < blocks.size(); ++i)
    {
        const auto& row = blocks[i]; const bool moe = row.at("kind") == "moe";
        Require(moe || row.at("kind") == "dense", "unknown block kind");
        Fields(row, std::string("layer_id kind intermediate_size") + (moe ? " num_experts top_k expert_placement shared_experts shared_intermediate_size router_correction_bias" : ""));
        Require(Number(row.at("layer_id")) == i, "nonconsecutive model layers"); Number(row.at("intermediate_size"), 1);
        if (moe)
        {
            Require(Number(row.at("top_k"), 1) <= Number(row.at("num_experts"), 1) && row.at("expert_placement").is_array() &&
                    row.at("expert_placement").size() == Number(row.at("num_experts")), "invalid experts");
            for (const auto& rank : row.at("expert_placement"))
                Require(rank.is_number_unsigned() && std::find(ranks.begin(), ranks.end(), rank) != ranks.end(), "expert outside ranks");
            Require((Number(row.at("shared_experts")) == 0) == (Number(row.at("shared_intermediate_size")) == 0) &&
                    row.at("router_correction_bias").is_boolean(), "invalid shared expert/router fields");
        }
        const auto& cost = profile.at("layers")[i];
        Fields(cost, std::string("layer_id per_token_ns attention_per_context_token_ns") + (moe ? " routed_expert_per_input_ns" : ""));
        Require(Number(cost.at("layer_id")) == i, "cost layer mismatch"); Number(cost.at("attention_per_context_token_ns"), 1);
        Fields(cost.at("per_token_ns"), common_costs + (moe ? " router dispatch_quant_pack combine_unpack_merge shared_expert shared_routed_add" : " dense_ffn"));
        for (const auto& [name, value] : cost.at("per_token_ns").items())
        {
            const bool absent = (name == "shared_expert" || name == "shared_routed_add") && row.at("shared_experts") == 0;
            const auto number = Number(value, absent ? 0 : 1);
            Require(!absent || number == 0, "absent shared expert has cost");
        }
        if (moe) Number(cost.at("routed_expert_per_input_ns"), 1);
    }
}

LayerPlan LoadFullModelPlan(const std::filesystem::path& directory, Json data)
{
    LayerPlan result{std::move(data), {}, {}, {}};
    const auto& p = result.data;
    const auto& model = p.at("model_spec"); const auto& profile = p.at("full_model_profile"); const auto& layers = p.at("layers");
    Require(layers.is_array() && !layers.empty() && layers.size() <= 128, "missing communication layers");
    const auto& first = layers[0]; const auto& ranks = first.at("rank_ids");
    ValidateFullModelProfile(model, profile, ranks);
    Json ids = Json::array(), moe_ids = Json::array();
    for (const auto& row : model.at("layers"))
    {
        ids.push_back(row.at("layer_id"));
        if (row.at("kind") == "moe") moe_ids.push_back(row.at("layer_id"));
    }
    Require(layers.size() == moe_ids.size(), "missing MoE layer");
    // Reconstruct every field, but compare large tables as they are produced.
    // The input tree has one owner; validated children return descriptors only.
    Json expected = Json::object();
    const std::set<std::string> replaced = {"input_provenance", "compute_inputs", "contributions", "compute_evaluation",
        "communication_buffers", "operations", "transfers", "rank_graphs", "expert_input_counts", "layers"};
    for (const auto& [key, value] : first.items()) if (!replaced.contains(key)) expected[key] = value;
    expected["schema_version"] = "moe-ub-full-model-plan/1";
    expected["model_spec"] = model; expected["full_model_profile"] = profile;
    expected["executed_layer_ids"] = ids; expected["communication_layer_ids"] = moe_ids;
    expected["input"] = Common(first.at("input")); expected["input"]["schema_version"] = "moe-ub-full-model-input/1";
    expected["input"]["executed_layer_ids"] = ids;
    for (auto key : {"operations", "expert_input_counts"}) expected[key] = Json::array();
    Require(p.at("rank_graphs").is_array() && p.at("rank_graphs").size() == ranks.size(), "composition mismatch: rank_graphs");
    Require(p.at("transfers").is_array(), "composition mismatch: transfers");
    std::map<uint64_t, LayerPlan> children;
    std::map<uint64_t, const Json*> child_data;
    std::set<std::string> operations; std::map<std::string, std::set<uint64_t>> identities;
    for (size_t i = 0; i < layers.size(); ++i)
    {
        const auto& layer = layers[i]; const auto& input = layer.at("input");
        const auto lid = Number(input.at("layer_id"));
        Require(input.at("layer_id") == moe_ids[i] && layer.at("schema_version") == "moe-ub-execution-plan/3" &&
                input.at("template") == "cann_a3_v850_fullmesh_v1" && input.at("role") == "D" && !input.contains("operator_profile"), "invalid CANN layer");
        const auto& row = model.at("layers")[lid]; const auto& cost = profile.at("layers")[lid]; const auto& per = cost.at("per_token_ns");
        Require(Common(input) == Common(first.at("input")) && Tokens(layer) == Tokens(first), "Token continuity/configuration mismatch");
        Require(input.at("hidden_dim") == model.at("hidden_size") && input.at("compute_dtypes") == model.at("dtypes") &&
                input.at("expert_placement") == row.at("expert_placement") && input.at("top_k") == row.at("top_k"), "model/layer mismatch");
        Require(input.at("compute_profile_ns") == Json({{"attention_route_pack", per.at("dispatch_quant_pack")},
            {"expert_per_input", cost.at("routed_expert_per_input_ns")}, {"expert_local_merge", 0},
            {"combine_unpack_merge", per.at("combine_unpack_merge")}, {"next_layer", 0}}), "duplicated/mismatched child cost");
        children.emplace(lid, ValidateLayerPlan(layer, false));
        child_data.emplace(lid, &layer);
        for (const auto& op : layer.at("operations"))
        {
            Require(operations.insert(op.at("operation_id").get<std::string>()).second, "duplicate operation");
            expected["operations"].push_back(op);
        }
        for (const auto& transfer : layer.at("transfers"))
            for (auto key : {"transfer_id", "ub_task_id", "comm_tag"})
                if (transfer.contains(key)) Require(identities[key].insert(Number(transfer.at(key))).second, "duplicate transfer identity");
    }
    identities.clear();
    const auto tokens = Tokens(first); const uint64_t ready = Number(first.at("ready_ns"));
    for (const auto& token : tokens) Require(Number(token.at("token_index")) < Number(model.at("max_position_embeddings")), "context exceeds model");
    std::map<std::pair<uint64_t, int>, uint64_t> offsets;
    for (size_t ri = 0; ri < ranks.size(); ++ri)
    {
        const int rank = ranks[ri]; uint64_t n = 0, context = 0, total = ready, expert_inputs = 0;
        Json contexts = Json::array();
        for (const auto& token : tokens) if (token.at("src_rank") == rank)
        {
            const auto length = Number(token.at("token_index")) + 1;
            Require(length <= uint64_t(INT64_MAX) - context, "context sum overflow");
            ++n; context += length; contexts.push_back(length);
        }
        Json graph = {{"rank_id", rank}, {"nodes", Json::array()}, {"layer_phases", Json::array()}, {"model_stages", Json::array()}};
        auto& nodes = graph["nodes"]; std::vector<uint64_t> previous;
        auto comp = [&](const std::string& stage, uint64_t work, std::vector<uint64_t> deps, Json lid = nullptr) {
            std::sort(deps.begin(), deps.end()); deps.erase(std::unique(deps.begin(), deps.end()), deps.end());
            Require(work <= uint64_t(INT64_MAX) - total, "computation time overflow"); total += work;
            Json node_id = nullptr;
            if (work)
            {
                node_id = nodes.size() + 1;
                nodes.push_back({{"node_id", node_id}, {"rank_id", rank}, {"kind", "COMP"}, {"name", stage}, {"model_stage", stage},
                    {"layer_id", lid}, {"duration_ns", work}, {"resource_id", "gpu_compute"}, {"deps", deps}});
            }
            graph["model_stages"].push_back({{"stage", stage}, {"layer_id", lid}, {"node_id", node_id}, {"duration_ns", work},
                {"entry_deps", deps}, {"batch_size", n}, {"context_tokens", contexts}});
            return work ? std::vector<uint64_t>{node_id.get<uint64_t>()} : deps;
        };
        previous = comp("embedding", Product(n, Number(profile.at("per_token_ns").at("embedding"))), previous);
        for (const auto& row : model.at("layers"))
        {
            const uint64_t lid = Number(row.at("layer_id")); const auto& cost = profile.at("layers")[lid]; const auto& per = cost.at("per_token_ns");
            for (auto stage : {"attention_norm", "mla_prologue"}) previous = comp(stage, Product(n, Number(per.at(stage))), previous, lid);
            previous = comp("mla_attention", Product(context, Number(cost.at("attention_per_context_token_ns"))), previous, lid);
            for (auto stage : {"mla_output", "attention_residual", "ffn_norm"}) previous = comp(stage, Product(n, Number(per.at(stage))), previous, lid);
            if (row.at("kind") == "dense") previous = comp("dense_ffn", Product(n, Number(per.at("dense_ffn"))), previous, lid);
            else
            {
                const auto branch = previous; const auto routed = comp("router", Product(n, Number(per.at("router"))), branch, lid);
                const auto& checked = *child_data.at(lid); const auto& child = checked.at("rank_graphs")[ri];
                const uint64_t base = nodes.size(); offsets[{lid, rank}] = base;
                std::set<uint64_t> used; std::vector<uint64_t> terminal;
                for (const auto& node : child.at("nodes")) for (const auto& dep : node.at("deps")) used.insert(dep.get<uint64_t>());
                for (auto node : child.at("nodes"))
                {
                    const auto id = Number(node.at("node_id")); node["node_id"] = id + base; node["layer_id"] = lid;
                    if (node.at("deps").empty()) node["deps"] = routed;
                    else for (auto& dep : node["deps"]) dep = dep.get<uint64_t>() + base;
                    if (!used.contains(id)) terminal.push_back(id + base);
                    if (node.at("kind") == "COMP")
                    {
                        const auto duration = Number(node.at("duration_ns"));
                        Require(duration <= uint64_t(INT64_MAX) - total, "child computation overflow"); total += duration;
                        if (id == child.at("phase_nodes").at("prepare")) { node["name"] = "dispatch-quant-pack"; node["model_stage"] = "dispatch_quant_pack"; }
                    }
                    nodes.push_back(node);
                }
                if (terminal.empty()) terminal = routed;
                Json phase = {{"layer_id", lid}, {"entry_deps", routed}, {"terminal_nodes", terminal}, {"phase_nodes", child.at("phase_nodes")}};
                for (auto& value : phase["phase_nodes"]) if (!value.is_null()) value = value.get<uint64_t>() + base;
                if (child.contains("local_prepare_nodes"))
                {
                    phase["local_prepare_nodes"] = child.at("local_prepare_nodes");
                    for (auto& value : phase["local_prepare_nodes"]) if (!value.is_null()) value = value.get<uint64_t>() + base;
                }
                graph["layer_phases"].push_back(phase);
                auto shared = comp("shared_expert", Product(Product(n, Number(row.at("shared_experts"))), Number(per.at("shared_expert"))), branch, lid);
                terminal.insert(terminal.end(), shared.begin(), shared.end());
                previous = comp("shared_routed_add", Product(n, Number(per.at("shared_routed_add"))), terminal, lid);
                expert_inputs += checked.at("expert_input_counts")[ri].get<uint64_t>();
            }
            previous = comp("ffn_residual", Product(n, Number(per.at("ffn_residual"))), previous, lid);
        }
        for (auto stage : {"final_norm", "lm_head", "sampling"}) previous = comp(stage, Product(n, Number(profile.at("per_token_ns").at(stage))), previous);
        graph["terminal_nodes"] = previous;
        Require(p.at("rank_graphs")[ri] == graph, "composition mismatch: rank_graphs");
        for (const auto& node : nodes) result.nodes.emplace(std::make_pair(rank, node.at("node_id").get<uint64_t>()), node);
        expected["expert_input_counts"].push_back(expert_inputs);
    }
    size_t transfer_index = 0;
    for (const auto& layer : layers)
    {
        const auto lid = Number(layer.at("input").at("layer_id"));
        for (auto transfer : layer.at("transfers"))
        {
            transfer["send_node_id"] = transfer.at("send_node_id").get<uint64_t>() + offsets.at({lid, transfer.at("src_rank").get<int>()});
            transfer["recv_node_id"] = transfer.at("recv_node_id").get<uint64_t>() + offsets.at({lid, transfer.at("dst_rank").get<int>()});
            Require(transfer_index < p.at("transfers").size() && p.at("transfers")[transfer_index] == transfer,
                    "composition mismatch: transfers");
            ++transfer_index;
        }
        for (auto& descriptor : children.at(lid).descriptors)
        {
            descriptor.send_node_id += offsets.at({lid, descriptor.src_rank}); descriptor.recv_node_id += offsets.at({lid, descriptor.dst_rank});
            result.descriptors.push_back(std::move(descriptor));
        }
        for (auto& descriptor : children.at(lid).local_descriptors)
        {
            descriptor.send_node_id += offsets.at({lid, descriptor.src_rank}); descriptor.recv_node_id += offsets.at({lid, descriptor.dst_rank});
            result.local_descriptors.push_back(std::move(descriptor));
        }
        children.erase(lid);
    }
    Require(transfer_index == p.at("transfers").size(), "composition mismatch: transfers");
    const auto usage = resources::Actual(p);
    const auto budget = resources::Budget(expected.at("input"));
    resources::Check(usage, budget);
    if (!budget.is_null()) expected["resource_usage"] = usage;
    for (const auto& [key, value] : expected.items())
        if (key != "compute_source" && key != "route_source") Require(p.at(key) == value, "composition mismatch: " + key);
    std::map<uint64_t, const Json*> transfers;
    for (const auto& transfer : p.at("transfers")) transfers.emplace(transfer.at("transfer_id"), &transfer);
    for (const auto& graph : p.at("rank_graphs"))
        CheckPlanGraph(directory / ("layer." + std::to_string(graph.at("rank_id").get<int>()) + ".et"), graph, transfers);
    return result;
}
}
