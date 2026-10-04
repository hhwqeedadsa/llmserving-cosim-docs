#include "astra_logging.hh"
#include "astra-sim/common/Logging.hh"
#include <filesystem>
#include <iostream>
#include <stdexcept>

void Check(bool ok) { if (!ok) throw std::runtime_error("archive assertion failed"); }
int main()
{
    auto store = std::make_shared<moe_ub::RecordStore>("records", 16 * 1024 * 1024, 2 * 1024 * 1024);
    moe_ub::InitializeAstraLogging(); moe_ub::EnableAstraExecutionArchive(store);
    auto logger = AstraSim::LoggerFactory::get_logger("workload");
    const std::string node_name(640, 'x');
    const uint64_t count = 220000;
    auto text = [&](uint64_t i, const std::string& graph) {
        return "issue,sys->id=0, tick=" + std::to_string(i) + ", node->id=" + std::to_string(i) +
            ", node->name=" + node_name + ", node->type=3, graph_id=" + graph;
    };
    for (uint64_t i = 0; i < count; ++i) logger->debug(text(i, "g0"));
    const auto first = moe_ub::DrainAstraExecutionArchive();
    logger->debug("not-an-execution-record"); logger->debug(text(0, "g1"));
    const auto second = moe_ub::DrainAstraExecutionArchive();
    Check(first.records == count && first.raw_bytes > 110 * 1024 * 1024 && second.records == 1);
    uint64_t index = 0;
    moe_ub::ReadRecords("records", first, [&](const auto& row) { Check(row == text(index++, "g0")); });
    Check(index == count);
    moe_ub::ReadRecords("records", second, [&](const auto& row) { Check(row == text(0, "g1")); });
    Check(std::filesystem::exists("log/log.10.log"));
    moe_ub::ShutdownAstraLogging();
    auto limited = std::make_shared<moe_ub::RecordStore>("limited", 1, 2 * 1024 * 1024);
    moe_ub::InitializeAstraLogging(); moe_ub::EnableAstraExecutionArchive(limited);
    logger = AstraSim::LoggerFactory::get_logger("workload"); logger->debug(text(0, "g2"));
    bool rejected = false;
    try { moe_ub::DrainAstraExecutionArchive(); } catch (const std::exception&) { rejected = true; }
    Check(rejected); moe_ub::ShutdownAstraLogging();
    std::cout << "PASS all " << count << " events survive diagnostic rotation; sealed chunks and failures checked\n";
}
