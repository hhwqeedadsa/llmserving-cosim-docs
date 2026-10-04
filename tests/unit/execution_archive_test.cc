#include "execution_archive.hh"
#include "identity_ranges.hh"
#include <iostream>
#include <random>
#include <set>

void Check(bool ok) { if (!ok) throw std::runtime_error("execution archive assertion failed"); }
template<class F> void Reject(F call)
{
    bool failed = false; try { call(); } catch (const std::exception&) { failed = true; } Check(failed);
}
moe_ub::LayerPlan Plan(std::string graph)
{
    using Json = nlohmann::json;
    moe_ub::LayerPlan result; result.data = {{"graph_id", graph}};
    result.nodes.emplace(std::make_pair(0, 1u), Json({{"kind", "COMP"}, {"duration_ns", 3}, {"deps", Json::array()}}));
    result.nodes.emplace(std::make_pair(0, 2u), Json({{"kind", "COMP"}, {"duration_ns", 2}, {"deps", {1u}}}));
    return result;
}
std::string Event(std::string graph, bool start, unsigned node, unsigned time)
{
    return std::string(start ? "issue" : "callback") + ",sys->id=0, tick=" + std::to_string(time) +
        ", node->id=" + std::to_string(node) + ", node->name=test, node->type=3, graph_id=" + graph;
}
int main()
{
    moe_ub::IdentityRanges ranges; std::set<uint64_t> reference; std::mt19937 generator(17);
    for (unsigned i = 0; i < 10000; ++i)
    {
        const uint64_t n = generator() % 3000;
        Check(ranges.count(n) == bool(reference.count(n)));
        Check(ranges.insert(n) == reference.insert(n).second);
    }
    for (uint64_t value : {uint64_t(0), UINT64_MAX, UINT64_MAX - 1, uint64_t(1)})
        Check(ranges.insert(value) == reference.insert(value).second);
    Check(ranges.size() == reference.size());
    moe_ub::IdentityRanges ordered;
    for (unsigned i = 0; i < 100000; ++i) Check(ordered.insert(i));
    Check(ordered.Intervals() == 1 && !ordered.insert(55000));
    auto first = Plan("g0"), second = Plan("g1");
    moe_ub::ExecutionArchive archive; archive.Register(first); archive.Register(second);
    for (const auto* graph : {"g0", "g1"})
    {
        archive.Consume(Event(graph, true, 1, 0)); archive.Consume(Event(graph, false, 1, 3));
    }
    archive.Consume(Event("g1", true, 2, 3)); archive.Consume(Event("g1", false, 2, 5));
    Check(archive.Take(second).at({0, 2}).end_ns == 5 && archive.ActiveGraphs() == 1 && archive.ResidentNodes() == 1);
    Reject([&] { archive.Consume(Event("g1", true, 1, 10)); });
    Reject([&] { archive.Consume(Event("g0", false, 1, 3)); });
    Reject([&] { archive.Take(first); });
    archive.Consume(Event("g0", true, 2, 3)); archive.Consume(Event("g0", false, 2, 5));
    Check(archive.Take(first).size() == 2 && archive.ResidentNodes() == 0 && archive.EventsRead() == 8);
    for (const bool bad_duration : {false, true})
    {
        moe_ub::ExecutionArchive bad; bad.Register(first);
        bad.Consume(Event("g0", true, 1, 0)); bad.Consume(Event("g0", false, 1, bad_duration ? 4 : 3));
        bad.Consume(Event("g0", true, 2, bad_duration ? 4 : 2)); bad.Consume(Event("g0", false, 2, bad_duration ? 6 : 4));
        Reject([&] { bad.Take(first); });
    }
    for (const auto* malformed : {"issue,sys->id=-1, tick=0, node->id=1, graph_id=g0",
         "issue,sys->id=0, tick=999999999999999999999999, node->id=1, graph_id=g0",
         "issue,sys->id=0, tick=0, node->id=1, missing-graph", "unknown"})
    {
        moe_ub::ExecutionArchive bad; bad.Register(first); Reject([&] { bad.Consume(malformed); });
    }
    std::cout << "PASS exact ID intervals, out-of-order graphs, retired/duplicate/malformed events and dependency checks\n";
}
