#include "layer_plan.hh"
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
                const std::map<uint64_t, Json>& transfers)
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
            const auto& t = transfers.at(n.at("transfer_id").get<uint64_t>());
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
    try { CheckGraph(path, graph, transfers); }
    catch (const std::exception& error) { throw std::runtime_error(path.string() + ": " + error.what()); }
}
LayerPlan LoadLayerPlan(const std::filesystem::path& directory)
{
    std::ifstream stream(directory / "plan.json");
    Require(bool(stream), "missing plan.json");
    LayerPlan plan{Json::parse(stream), {}, {}};
    const auto& p = plan.data;
    if (p.at("communication_template") == "kv_direct_v1") return LoadKvPlan(directory, p);
    Require(p.at("schema_version") == "moe-ub-execution-plan/1" &&
            p.at("rank_to_ub_node") == p.at("rank_ids") &&
            p.at("communication_template") == "direct_rank_dedup_v1" &&
            p.at("sender_completion") == "transaction_done", "unsupported schema, mapping or template");
    const auto& ranks = p.at("rank_ids");
    Require(ranks.is_array() && ranks.size() == 4, "require four explicit group members");
    std::set<int> members;
    int previous = -1;
    for (const auto& value : ranks)
    {
        const int rank = Number(value, "rank_id", 7);
        Require(rank > previous && members.insert(rank).second, "unordered or repeated rank");
        previous = rank;
    }
    Require(p.at("input").at("rank_ids") == ranks && p.at("input").at("rank_to_ub_node") == ranks,
            "input rank mapping mismatch");
    const auto slots = Number(p.at("send_slots_per_rank"), "send slots", 2);
    const auto& input = p.at("input");
    std::map<int, uint64_t> local_tokens, expert_inputs;
    for (int rank : members) { local_tokens[rank] = 0; expert_inputs[rank] = 0; }
    const auto& tokens = input.at("tokens");
    const auto& placement = input.at("expert_placement");
    const bool prefill = input.at("role") == "P";
    Require(prefill || input.at("role") == "D", "invalid execution role");
    Require(tokens.is_array() && tokens.size() >= 1 && tokens.size() <= (prefill ? 16 : 4) &&
            placement.is_array() && placement.size() == 8, "invalid Token/placement set");
    for (const auto& value : placement) Require(members.contains(Number(value, "expert rank", 7)), "expert outside group");
    for (const auto& token : tokens)
    {
        const int rank = Number(token.at("src_rank"), "Token rank", 7);
        Require(members.contains(rank), "nonmember local Token");
        Require(++local_tokens[rank] == 1 || prefill, "repeated local decode Token");
        Require(token.at("phase") == (prefill ? "prefill" : "decode"), "Token phase mismatch");
        const auto& experts = token.at("expert_ids");
        Require(experts.is_array() && experts.size() == 2 && experts[0] != experts[1], "invalid Top-K route");
        for (const auto& expert : experts) ++expert_inputs[placement.at(Number(expert, "expert", 7)).get<int>()];
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
    Require(slots > 0 && p.at("send_slots_per_rank") == input.at("send_slots_per_rank"), "send slot configuration mismatch");
    const auto chunk_bytes = Number(input.at("chunk_bytes"), "chunk_bytes", UINT32_MAX);
    Require(chunk_bytes > 0, "zero chunk limit");
    Number(p.at("ready_ns"), "ready_ns", INT64_MAX - 1000000);
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
    for (const auto& token : tokens)
    {
        const auto src = token.at("src_rank").get<uint64_t>();
        std::set<uint64_t> destinations;
        for (const auto& expert : token.at("expert_ids")) destinations.insert(placement.at(expert.get<size_t>()).get<uint64_t>());
        destinations.erase(src);
        for (auto dst : destinations)
        {
            ++expected_pairs[{"dispatch", src, dst}];
            ++expected_pairs[{"combine", dst, src}];
        }
    }
    for (const auto& t : p.at("transfers"))
    {
        const auto id = Number(t.at("transfer_id"), "transfer_id");
        const auto task = Number(t.at("ub_task_id"), "ub_task_id", UINT32_MAX);
        const auto tag = Number(t.at("comm_tag"), "comm_tag", 499999999);
        const auto bytes = Number(t.at("payload_bytes"), "payload_bytes", UINT32_MAX);
        const auto src = Number(t.at("src_rank"), "src_rank", 7), dst = Number(t.at("dst_rank"), "dst_rank", 7);
        const auto operation = t.at("operation_id").get<std::string>();
        Require(id > 0 && tag > 0 && bytes > 0 && src != dst && members.contains(src) && members.contains(dst) && transfers.emplace(id, t).second &&
                tasks.insert(task).second && tags.insert(tag).second, "invalid/duplicate transfer identity or size");
        Require(operations.contains(operation) && operations.at(operation) == t.at("kind") &&
                t.at("business_payload_bytes") == bytes && t.at("application_metadata_bytes") == 0 &&
                t.at("task_byte_range") == Json::array({0, bytes}) &&
                t.at("priority") == 7 && t.at("transport_profile_id") == "urma-write-rtp-default", "invalid provenance/profile");
        const auto chunk = Number(t.at("chunk_index"), "chunk_index", 4095);
        const auto total = Number(t.at("pair_payload_bytes"), "pair_payload_bytes", INT64_MAX);
        const auto& range = t.at("pair_byte_range");
        Require(range.is_array() && range.size() == 2, "invalid pair range");
        const auto begin = Number(range[0], "pair begin", total), end = Number(range[1], "pair end", total);
        Require(total > 0 && end >= begin && end - begin == bytes && bytes <= chunk_bytes,
                "invalid chunk range/size");
        chunks[{operation, src, dst}].push_back(t);
        plan.descriptors.push_back({id, task, graph_id,
            Number(t.at("send_node_id"), "send_node_id"), Number(t.at("recv_node_id"), "recv_node_id"),
            static_cast<int>(src), static_cast<int>(dst), static_cast<int>(tag), bytes, operation,
            static_cast<uint32_t>(chunk), begin, total});
    }
    for (const auto& [key, parts] : chunks)
    {
        const auto& first = parts.front();
        const auto kind = first.at("kind").get<std::string>();
        const auto hidden = Number(input.at("hidden_dim"), "hidden_dim", INT64_MAX);
        const auto element = Number(input.at(kind + "_element_bytes"), "element bytes", 16);
        const auto& sources = first.at("sources");
        Require(hidden > 0 && element > 0 && sources.is_array() && !sources.empty() &&
                sources.size() <= (prefill ? 16 : 4) && hidden <= uint64_t(INT64_MAX) / element / sources.size(), "invalid vector sources/size");
        const auto expected = hidden * element * sources.size();
        actual_pairs[{kind, first.at("src_rank").get<uint64_t>(), first.at("dst_rank").get<uint64_t>()}] = sources.size();
        uint64_t offset = 0, index = 0;
        for (const auto& part : parts)
        {
            const auto size = std::min(chunk_bytes, expected - offset);
            Require(size > 0 && part.at("chunk_index") == index++ && part.at("payload_bytes") == size &&
                    part.at("pair_payload_bytes") == expected && part.at("pair_byte_range") == Json::array({offset, offset + size}) &&
                    part.at("sources") == sources, "invalid chunk order, tail, range or source");
            offset += size;
        }
        Require(offset == expected, "incomplete directed vector");
    }
    Require(actual_pairs == expected_pairs, "transfer set/bytes differ from Token routes");
    Require(p.at("rank_graphs").size() == 4, "missing rank graph");
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
            seen.insert(id); local.emplace(id, n); plan.nodes.emplace(std::make_pair(rank, id), n);
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
            Require(duration > 0 && (!count || duration <= uint64_t(INT64_MAX) / count), "invalid compute profile");
            if (!count)
            {
                Require(phase.at(field).is_null(), "absent work must not have a compute node");
                return deps;
            }
            const auto id = Number(phase.at(field), "phase node");
            Require(local.contains(id) && local.at(id).at("kind") == "COMP" && phase_ids.insert(id).second &&
                    local.at(id).at("duration_ns") == duration * count && Ids(local.at(id).at("deps")) == deps,
                    "compute phase work/duration/dependency mismatch");
            return {id};
        };
        std::map<std::string, std::pair<int, uint64_t>> previous_send;
        auto communicate = [&](const char* kind, const std::set<uint64_t>& deps) {
            auto completed = deps;
            for (const auto& [node_id, n] : local)
            {
                if (n.at("kind") == "COMP") continue;
                const auto& t = transfers.at(n.at("transfer_id").get<uint64_t>());
                if (t.at("kind") != kind) continue;
                Require(Ids(n.at("deps")) == deps, "communication template dependency mismatch");
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
        const auto path = directory / ("layer." + std::to_string(rank) + ".et");
        try { CheckGraph(path, graph, transfers); }
        catch (const std::exception& error)
        {
            throw std::runtime_error(path.string() + ": " + error.what());
        }
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
        if (node.at("kind") == "COMP") Require(record["end_ns"].get<uint64_t>() - record["start_ns"].get<uint64_t>() == node.at("duration_ns"), "compute duration mismatch");
        for (const auto& dep : node.at("deps"))
            Require(record["start_ns"] >= events.at({key.first, dep.get<uint64_t>()}).at("end_ns"), "execution violated local dependency");
        record["rank_id"] = key.first; record["node_id"] = key.second; record["kind"] = node.at("kind");
        result.push_back(record);
    }
    return result;
}
Json LayerEndpointOperations(const LayerPlan& plan, const Json& execution)
{
    std::map<std::pair<int, uint64_t>, Json> nodes;
    for (const auto& event : execution) nodes[{event.at("rank_id"), event.at("node_id")}] = event;
    Json endpoints = Json::array();
    if (plan.data.at("communication_template") == "kv_direct_v1")
    {
        for (const int rank : plan.data.at("rank_ids"))
        {
            uint64_t end = plan.data.at("ready_ns");
            for (const auto& event : execution)
                if (event.at("rank_id") == rank) end = std::max(end, event.at("end_ns").get<uint64_t>());
            endpoints.push_back({{"rank_id", rank}, {"local_record_id", 1}, {"kind", "KV_TRANSFER"},
                {"start_local_ns", plan.data.at("ready_ns")}, {"end_local_ns", end}});
        }
        return endpoints;
    }
    for (const auto& graph : plan.data.at("rank_graphs"))
    {
        int rank = graph.at("rank_id");
        const auto& p = graph.at("phase_nodes");
        auto end = [&](const char* phase, uint64_t fallback) {
            return p.at(phase).is_null() ? fallback :
                nodes.at({rank, p.at(phase).get<uint64_t>()}).at("end_ns").get<uint64_t>();
        };
        auto communication_end = [&](const char* kind, uint64_t earliest) {
            for (const auto& transfer : plan.data.at("transfers"))
                if (transfer.at("kind") == kind)
                    for (const auto& side : {"send", "recv"})
                        if (transfer.at(std::string(side) == "send" ? "src_rank" : "dst_rank") == rank)
                            earliest = std::max(earliest, nodes.at({rank, transfer.at(std::string(side) + "_node_id").get<uint64_t>()})
                                .at("end_ns").get<uint64_t>());
            return earliest;
        };
        const auto prepared = end("prepare", plan.data.at("ready_ns"));
        const auto dispatched = communication_end("dispatch", prepared);
        const auto merged = end("local_merge", end("expert", dispatched));
        const auto finished = end("finish", communication_end("combine", merged));
        endpoints.push_back({{"rank_id", rank}, {"local_record_id", 1}, {"kind", "dispatch"},
            {"start_local_ns", prepared}, {"end_local_ns", dispatched}});
        endpoints.push_back({{"rank_id", rank}, {"local_record_id", 2}, {"kind", "combine"},
            {"start_local_ns", merged}, {"end_local_ns", finished}});
    }
    return endpoints;
}
Json LayerOperationRegistry(const LayerPlan& plan)
{
    Json result = plan.data.at("operations");
    for (auto& operation : result)
    {
        operation["graph_id"] = plan.data.at("graph_id");
        operation["endpoint_records"] = Json::array();
        for (const int rank : plan.data.at("rank_ids"))
            operation["endpoint_records"].push_back({{"rank_id", rank},
                {"local_record_id", operation.at("kind") == "combine" ? 2 : 1}});
    }
    return result;
}
}
