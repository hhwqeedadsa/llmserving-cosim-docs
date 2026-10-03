#pragma once

#include <algorithm>
#include <map>
#include <set>
#include <stdexcept>
#include "extern/helper/json/json.hpp"

namespace moe_ub
{
// Recompute the exact operator lookup from Tokens, never from supplied node times.
inline nlohmann::json EvaluateOperatorProfile(const nlohmann::json& input)
{
    using J = nlohmann::json;
    auto check = [](bool ok, const char* message) {
        if (!ok) throw std::runtime_error(std::string("Operator profile: ") + message);
    };
    auto number = [&](const J& value, uint64_t low, uint64_t high) {
        check(value.is_number_unsigned() && value.get<uint64_t>() >= low && value.get<uint64_t>() <= high,
              "invalid integer");
        return value.get<uint64_t>();
    };
    const auto& p = input.at("operator_profile");
    check(input.at("schema_version") == "moe-ub-layer-fixture/3" && p.is_object() && p.size() == 7 &&
          p.at("schema_version") == "moe-ub-operator-profile/1" &&
          (p.at("basis") == "measured_operator" || p.at("basis") == "synthetic_test"), "invalid schema/basis");
    for (const auto* key : {"device", "software", "measurement"})
        check(p.at(key).is_object() && !p.at(key).empty(), "missing metadata");
    const auto& m = p.at("measurement");
    check(m.at("statistic") == "ceil_median_ns" && m.at("out_of_domain") == "reject" &&
          m.at("implementation") == "torch_eager_serial_gated_ffn_and_sdpa_v1", "unsupported measurement policy");
    if (p.at("basis") == "measured_operator")
    {
        check(m.at("timer") == "cuda_event" && m.at("weights") == "seeded_random_operator_tensors" &&
              m.at("script_sha256").is_string() && m.at("script_sha256").get<std::string>().size() == 64,
              "missing measured evidence");
        for (const auto* key : {"name", "compute_capability", "driver_version"})
            check(p.at("device").contains(key) && !p.at("device").at(key).empty(), "missing device evidence");
        for (const auto* key : {"torch", "cuda", "python"})
            check(p.at("software").contains(key) && !p.at("software").at(key).empty(), "missing software evidence");
    }
    const auto& d = p.at("domain");
    check(d.is_object() && d.size() == 7, "invalid domain");
    for (const auto* key : {"hidden_dim", "intermediate_dim", "num_heads", "num_kv_heads", "head_dim"})
        number(d.at(key), 1, INT32_MAX);
    check(d.at("tp") == 1 && d.at("num_heads").get<uint64_t>() % d.at("num_kv_heads").get<uint64_t>() == 0 &&
          d.at("hidden_dim").get<uint64_t>() == d.at("num_heads").get<uint64_t>() * d.at("head_dim").get<uint64_t>() &&
          d.at("hidden_dim") == input.at("hidden_dim") && (d.at("dtype") == "float16" || d.at("dtype") == "bfloat16") &&
          d.at("dtype") == input.at("compute_dtypes").at("compute") && d.at("dtype") == input.at("compute_dtypes").at("kv"),
          "execution dimensions/dtype mismatch");
    const auto& rows = p.at("rows");
    check(rows.is_array() && !rows.empty() && rows.size() <= 16384, "row budget exceeded");
    std::map<std::pair<std::string,std::string>, size_t> table;
    for (size_t i = 0; i < rows.size(); ++i)
    {
        const auto& row = rows[i]; const auto op = row.at("operator").get<std::string>();
        check(row.is_object() && row.size() == 4 && (op == "attention_decode" || op == "expert_ffn"), "invalid row");
        const auto& shape = row.at("shape");
        check(shape.is_array() && !shape.empty() && shape.size() <= 4096, "invalid shape");
        bool positive = false;
        for (const auto& value : shape) positive |= number(value, op == "attention_decode" ? 1 : 0, INT32_MAX) != 0;
        check(positive && table.emplace(std::make_pair(op, shape.dump()), i).second, "zero or duplicate profile shape");
        const auto& samples = row.at("samples_ns");
        check(samples.is_array() && samples.size() >= 3 && samples.size() <= 10000, "missing repeat samples");
        std::vector<uint64_t> sorted;
        for (const auto& value : samples) sorted.push_back(number(value, 1, 1000000000000ULL));
        std::sort(sorted.begin(), sorted.end());
        const auto median = (sorted[(sorted.size()-1)/2] + sorted[sorted.size()/2] + 1) / 2;
        check(number(row.at("duration_ns"), 1, 1000000000000ULL) == median, "statistic differs from samples");
    }
    std::map<int, uint64_t> experts;
    for (const auto& t : input.at("tokens"))
        for (const auto& expert : t.at("expert_ids")) ++experts[expert.get<int>()];
    J ranks = J::array();
    for (const int rank : input.at("rank_ids"))
    {
        J contexts = J::array(), counts = J::array(), selected = J::object();
        for (const auto& t : input.at("tokens"))
            if (t.at("src_rank") == rank) contexts.push_back(number(t.at("token_index"), 0, INT64_MAX-1)+1);
        for (size_t e = 0; e < input.at("expert_placement").size(); ++e)
            if (input.at("expert_placement")[e] == rank) counts.push_back(experts[e]);
        for (const auto& [op, shape] : std::vector<std::pair<std::string,J>>{{"attention_decode",contexts},{"expert_ffn",counts}})
        {
            const bool work = std::any_of(shape.begin(), shape.end(), [](const auto& n) { return n != 0; });
            J result = {{"shape",shape},{"row_index",nullptr},{"duration_ns",0}};
            if (work)
            {
                const auto key = std::make_pair(op, shape.dump());
                check(table.contains(key), "outside measured domain");
                const size_t index = table.at(key);
                result["row_index"] = index; result["duration_ns"] = rows[index].at("duration_ns");
            }
            selected[op] = result;
        }
        ranks.push_back({{"rank_id",rank},{"operators",selected}});
    }
    return {{"basis",p.at("basis")},{"policy","exact_shape_no_extrapolation"},{"extrapolation_count",0},
        {"domain",d},{"ranks",ranks},{"phase_policy","expert_replaced_attention_added_to_explicit_prepare_residual"},
        {"reference_phases",{"prepare_residual","expert_local_merge","combine_unpack_merge","local_prepare"}}};
}
}
