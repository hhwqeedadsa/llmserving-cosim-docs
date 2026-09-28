#include "ub_network_adapter.hh"
#include "astra_logging.hh"

#include "astra-sim/system/Sys.hh"
#include "et_def.pb.h"
#include "extern/helper/json/json.hpp"
#include "ns3/simulator.h"
#include "ns3/ub-app.h"
#include "protoio.hh"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <link.h>
#include <memory>
#include <set>
#include <stdexcept>

namespace
{
using Json = nlohmann::json;
namespace fs = std::filesystem;

Json ReadJson(const fs::path& path)
{
    std::ifstream stream(path);
    if (!stream)
    {
        throw std::runtime_error("Cannot open input: " + path.string());
    }
    return Json::parse(stream);
}

uint64_t ReadNs(const Json& input, const char* field)
{
    if (!input.contains(field) || !input.at(field).is_number_unsigned())
    {
        throw std::invalid_argument(std::string(field) + " must be a nonnegative integer");
    }
    const auto value = input.at(field).get<uint64_t>();
    if (value > static_cast<uint64_t>(std::numeric_limits<int64_t>::max()))
    {
        throw std::overflow_error(std::string(field) + " exceeds ns-3 time range");
    }
    return value;
}

void WriteGraph(uint64_t duration_ns)
{
    ProtoOutputStream output("compute.0.et");
    ChakraProtoMsg::GlobalMetadata metadata;
    metadata.set_version("m1-compute/1");
    output.write(metadata);
    ChakraProtoMsg::Node node;
    node.set_id(1);
    node.set_name("synthetic-comp");
    node.set_type(ChakraProtoMsg::COMP_NODE);
    // The locked LLMServingSim Chakra fork stores nanoseconds in this legacy field.
    node.set_duration_micros(duration_ns);
    auto* cpu = node.add_attr();
    cpu->set_name("is_cpu_op");
    cpu->set_bool_val(false);
    auto* ops = node.add_attr();
    ops->set_name("num_ops");
    ops->set_int64_val(0);
    auto* tensor = node.add_attr();
    tensor->set_name("tensor_size");
    tensor->set_uint64_val(0);
    output.write(node);
}

int RecordLibrary(dl_phdr_info* info, std::size_t, void* data)
{
    if (info->dlpi_name && info->dlpi_name[0])
    {
        static_cast<Json*>(data)->push_back(info->dlpi_name);
    }
    return 0;
}
}

int main(int argc, char** argv)
{
    try
    {
        if (argc != 4)
        {
            throw std::invalid_argument("Usage: cosim-compute INPUT.json SYSTEM.json NEW_RUN_DIR");
        }
        const auto input = ReadJson(fs::absolute(argv[1]));
        const auto system_config = ReadJson(fs::absolute(argv[2]));
        const auto run_dir = fs::absolute(argv[3]);
        std::set<std::string> keys;
        for (auto it = input.begin(); it != input.end(); ++it)
        {
            keys.insert(it.key());
        }
        if (input.at("schema_version") != "moe-ub-compute/1" ||
            keys != std::set<std::string>{"schema_version", "start_ns", "duration_ns"})
        {
            throw std::invalid_argument("Unsupported compute input schema or fields");
        }
        const auto start_ns = ReadNs(input, "start_ns");
        const auto duration_ns = ReadNs(input, "duration_ns");
        if (duration_ns > static_cast<uint64_t>(std::numeric_limits<int64_t>::max()) - start_ns)
        {
            throw std::overflow_error("COMP completion exceeds ns-3 time range");
        }
        if (system_config.at("roofline-enabled") != 0 || system_config.at("replay-only") != 0)
        {
            throw std::invalid_argument("M1.1 requires roofline and replay-only to be disabled");
        }
        if (fs::exists(run_dir) || !fs::create_directory(run_dir))
        {
            throw std::invalid_argument("The run directory must be new and its parent must exist");
        }
        fs::current_path(run_dir);
        std::ofstream("input.json") << input.dump(2) << '\n';
        std::ofstream("system.json") << system_config.dump(2) << '\n';
        WriteGraph(duration_ns);
        ns3::Time::SetResolution(ns3::Time::NS);
        moe_ub::InitializeAstraLogging();

        Json report = {
            {"schema_version", "moe-ub-compute-result/1"},
            {"compute_source", "synthetic_fixed_duration"},
            {"calibration_status", "unvalidated_reference"},
            {"duration_ns", duration_ns},
            {"completion_times_ns", Json::array()},
            {"loaded_libraries", Json::array()},
            {"ub_type", ns3::UbApp::GetTypeId().GetName()}
        };
        moe_ub::UbNetworkAdapter adapter(0, [&](uint64_t now_ns) {
            report["completion_times_ns"].push_back(now_ns);
        });
        {
            auto system = std::make_unique<AstraSim::Sys>(
                0, "compute", "empty", "system.json",
                std::vector<AstraSim::AstraMemoryAPI*>{}, &adapter,
                std::vector<int>{1}, std::vector<int>{1}, 1.0, 1.0, false);
            auto* workload = system->workload;
            const auto node = workload->et_feeder->lookupNode(1);
            report["chakra_runtime_ns"] = node->runtime();
            report["initial_in_flight"] = workload->hw_resource->num_in_flight_gpu_comp_ops;
            ns3::Simulator::Schedule(ns3::NanoSeconds(static_cast<int64_t>(start_ns)), [&]() {
                report["start_ns"] = ns3::Simulator::Now().GetNanoSeconds();
                report["astra_start_ns"] = AstraSim::Sys::boostedTick();
                workload->fire();
                report["in_flight_after_issue"] = workload->hw_resource->num_in_flight_gpu_comp_ops;
                report["pending_events_after_issue"] = system->pending_events;
            });
            ns3::Simulator::Run();
            report["finish_ns"] = ns3::Simulator::Now().GetNanoSeconds();
            report["astra_finish_ns"] = AstraSim::Sys::boostedTick();
            report["finished"] = workload->is_finished;
            report["remaining_nodes"] = workload->et_feeder->hasNodesToIssue();
            report["final_in_flight"] = {
                workload->hw_resource->num_in_flight_cpu_ops,
                workload->hw_resource->num_in_flight_gpu_comp_ops,
                workload->hw_resource->num_in_flight_gpu_comm_ops};
            report["gpu_ops"] = workload->hw_resource->num_gpu_ops;
            report["gpu_time_ns"] = workload->hw_resource->tics_gpu_ops;
            report["pending_events"] = system->pending_events;
            report["event_buckets"] = system->event_queue.size();
            report["scheduled_callbacks"] = adapter.ScheduledEvents();
            report["executed_callbacks"] = adapter.ExecutedCallbacks();
            dl_iterate_phdr(RecordLibrary, &report["loaded_libraries"]);
        }
        ns3::Simulator::Destroy();
        moe_ub::ShutdownAstraLogging();
        std::ofstream result("result.json");
        result << report.dump(2) << '\n';
        result.close();
        if (!result)
        {
            throw std::runtime_error("Cannot write result.json");
        }
        if (report["finished"] != true || report["completion_times_ns"].size() != 1 ||
            report["remaining_nodes"] != false || report["pending_events"] != 0 ||
            report["final_in_flight"] != Json::array({0, 0, 0}))
        {
            throw std::runtime_error("COMP did not finish cleanly; inspect result.json");
        }
        std::cout << "Result: " << (run_dir / "result.json") << '\n';
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << error.what() << '\n';
        ns3::Simulator::Destroy();
        moe_ub::ShutdownAstraLogging();
        return 1;
    }
}
