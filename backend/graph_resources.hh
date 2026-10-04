#pragma once
#include "extern/helper/json/json.hpp"
#include <map>
#include <set>
#include <stdexcept>

namespace moe_ub::resources
{
using Json = nlohmann::json;
inline void Require(bool ok, const std::string& message)
{
    if (!ok) throw std::runtime_error("Graph resources: " + message);
}
inline void Validate(const Json& b)
{
    Require(b.is_object() && b.size() == 7 && b.at("schema_version") == "moe-ub-graph-resources/1", "invalid budget schema");
    for (auto key : {"max_graph_transfers", "max_graph_nodes", "max_plan_bytes", "max_run_graphs", "max_run_transfers", "max_run_nodes"})
        Require(b.at(key).is_number_unsigned() && b.at(key).get<uint64_t>() > 0 && b.at(key).get<uint64_t>() <= INT64_MAX, std::string("invalid ")+key);
    Require(b.at("max_graph_transfers") <= b.at("max_run_transfers") && b.at("max_run_transfers").get<uint64_t>() < 499999900 &&
            b.at("max_graph_nodes") <= b.at("max_run_nodes"), "graph/run or tag budget mismatch");
}
inline Json Budget(const Json& input)
{
    if (!input.contains("resource_budget")) return nullptr;
    const auto b = input.at("resource_budget"); Validate(b); return b;
}
inline void Check(const Json& usage, const Json& budget)
{
    if (budget.is_null()) Require(usage.at("transfers").get<uint64_t>() <= 4096, "exceeds legacy 4096 transfer budget");
    else
    {
        Validate(budget);
        for (const std::string key : {"transfers", "nodes"})
            Require(usage.at(key) <= budget.at("max_graph_"+key), "graph budget exceeded: "+key);
    }
}
inline Json Actual(const Json& p)
{
    uint64_t nodes=0, fabric=0;
    for (const auto& g : p.at("rank_graphs")) nodes += g.at("nodes").size();
    for (const auto& t : p.at("transfers")) fabric += t.contains("ub_task_id");
    return {{"transfers",p.at("transfers").size()},{"fabric_transfers",fabric},
        {"local_transfers",p.at("transfers").size()-fabric},{"nodes",nodes}};
}
inline Json Cann(const Json& c)
{
    std::map<int,std::string> chips;
    if (c.contains("deployment")) for (const auto& r : c.at("deployment").at("ranks")) chips[r.at("rank_id")]=r.at("chip_id");
    auto near=[&](int a,int b) { return !chips.empty() && chips.at(a)==chips.at(b); };
    uint64_t fabric=0,local=0;
    const uint64_t h=c.at("hidden_dim"), chunk=c.at("chunk_bytes");
    Require(chunk>0 && h<=8192,"invalid count dimensions");
    std::set<int> sources,experts,dispatch,combine;
    for (const int src : c.at("rank_ids")) for (const int dst : c.at("expert_placement"))
        if (src!=dst) { if (near(src,dst)) ++local; else ++fabric; }
    for (const auto& t : c.at("tokens"))
    {
        const int src=t.at("src_rank"); sources.insert(src);
        for (const size_t e : t.at("expert_ids"))
        {
            const int dst=c.at("expert_placement").at(e); experts.insert(dst);
            if (src==dst) continue;
            if (near(src,dst)) { local+=3;dispatch.insert(src);combine.insert(dst); }
            else fabric+=((h+31)/32*32+44+chunk-1)/chunk+(h*2+chunk-1)/chunk+1;
        }
    }
    uint64_t compute=c.at("rank_ids").size()+2*sources.size()+2*experts.size();
    if (c.at("compute_profile_ns").at("next_layer")!=0) compute+=sources.size();
    if (c.contains("deployment"))
    {
        const auto& cost=c.at("deployment").at("local_prepare_ns_per_vector");
        if (cost.at("dispatch")!=0) compute+=dispatch.size();
        if (cost.at("combine")!=0) compute+=combine.size();
    }
    return {{"transfers",fabric+local},{"fabric_transfers",fabric},{"local_transfers",local},{"nodes",2*(fabric+local)+compute}};
}
inline Json AddRun(const Json& used, const Json& graph, const Json& budget)
{
    Json next=used;
    for (const std::string key : {"graphs","transfers","nodes"})
    {
        uint64_t a=used.at(key), b=key=="graphs" ? 1 : graph.at(key).get<uint64_t>();
        const uint64_t limit=budget.at("max_run_"+key);
        Require(a<=limit && b<=limit-a,"run budget exceeded: "+key);
        next[key]=a+b;
    }
    return next;
}
}
