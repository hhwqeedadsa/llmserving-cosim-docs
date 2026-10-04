#pragma once
#include "execution_timing.hh"
#include "layer_plan.hh"
#include "record_store.hh"
#include <charconv>
#include <limits>
#include <string_view>

namespace moe_ub
{
// Consume each sealed ASTRA archive once. Only active graphs have timing state;
// records from an unregistered/retired graph are errors, never silently skipped.
class ExecutionArchive
{
  public:
    void Register(const LayerPlan& plan)
    {
        const auto graph = plan.data.at("graph_id").get<std::string>();
        Require(active_.emplace(graph, State{plan.nodes.size(), {}}).second, "graph already active");
    }
    void Read(const std::filesystem::path& directory, const RecordFile& file)
    {
        ReadRecords(directory, file, [this](const std::string& row) { Consume(row); });
    }
    void Consume(std::string_view row)
    {
        const bool start = row.substr(0, 6) == "issue,";
        Require(start || row.substr(0, 9) == "callback,", "unknown event kind");
        row.remove_prefix(start ? 6 : 9);
        const auto rank = Number(row, "sys->id=", ", tick=");
        const auto time = Number(row, "", ", node->id=");
        const auto node = Number(row, "", ",");
        Require(rank < INT32_MAX && time <= INT64_MAX, "event rank or time out of range");
        const auto marker = row.rfind(", graph_id=");
        Require(marker != std::string_view::npos, "missing graph identity");
        const std::string graph(row.substr(marker + 11));
        auto state = active_.find(graph); Require(state != active_.end(), "unknown or retired graph " + graph);
        auto& events = state->second.times;
        const auto key = std::make_pair(int(rank), node);
        auto found = events.find(key);
        if (found == events.end())
        {
            Require(events.size() < state->second.nodes, "more event nodes than the declared graph");
            found = events.emplace(key, NodeTimes{}).first;
        }
        auto& event = found->second;
        if (start)
        {
            Require(!event.started && !event.finished, "duplicate or late issue");
            event.start_ns = time; event.started = true;
        }
        else
        {
            Require(event.started && !event.finished && time >= event.start_ns, "duplicate, early or missing issue callback");
            event.end_ns = time; event.finished = true;
        }
        ++events_read_;
    }
    ExecutionTimes Take(const LayerPlan& plan)
    {
        const auto graph = plan.data.at("graph_id").get<std::string>();
        auto found = active_.find(graph); Require(found != active_.end(), "graph not registered");
        const auto& events = found->second.times;
        Require(events.size() == plan.nodes.size(), "incomplete graph execution");
        for (const auto& [key, node] : plan.nodes)
        {
            const auto& event = events.at(key);
            Require(event.started && event.finished, "incomplete node execution");
            if (node.kind == "COMP") Require(event.end_ns - event.start_ns == node.duration_ns, "compute duration mismatch");
            for (const auto dep : node.deps)
                Require(event.start_ns >= events.at({key.first, dep}).end_ns, "execution violated local dependency");
        }
        auto result = std::move(found->second.times); active_.erase(found); return result;
    }
    size_t ActiveGraphs() const { return active_.size(); }
    size_t ResidentNodes() const
    {
        size_t count = 0; for (const auto& [name, state] : active_) count += state.times.size(); return count;
    }
    uint64_t EventsRead() const { return events_read_; }
  private:
    static void Require(bool ok, const std::string& text)
    { if (!ok) throw std::runtime_error("Execution archive: " + text); }
    static uint64_t Number(std::string_view& row, std::string_view prefix, std::string_view suffix)
    {
        Require(row.substr(0, prefix.size()) == prefix, "invalid event field"); row.remove_prefix(prefix.size());
        uint64_t value = 0; const auto parsed = std::from_chars(row.data(), row.data() + row.size(), value);
        Require(parsed.ec == std::errc{} && parsed.ptr != row.data(), "invalid unsigned event number");
        row.remove_prefix(parsed.ptr - row.data());
        Require(row.substr(0, suffix.size()) == suffix, "invalid event separator"); row.remove_prefix(suffix.size());
        return value;
    }
    struct State { size_t nodes; ExecutionTimes times; };
    std::map<std::string, State> active_;
    uint64_t events_read_ = 0;
};
}
