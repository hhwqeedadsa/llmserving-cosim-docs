#include "astra_logging.hh"
#include "layer_plan.hh"
#include "port_observation.hh"
#include "ub_network_adapter.hh"

#include "astra-sim/system/Sys.hh"
#include "ns3/simulator.h"
#include "ns3/ub-function.h"
#include "ns3/ub-transfer-executor.h"
#include "ns3/ub-utils.h"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <link.h>
#include <memory>
#include <stdexcept>

namespace
{
using Json = nlohmann::json;
namespace fs = std::filesystem;
void Require(bool ok, const std::string& message)
{
    if (!ok) throw std::runtime_error(message);
}
Json ReadJson(const fs::path& path)
{
    std::ifstream stream(path);
    Require(bool(stream), "Cannot open " + path.string());
    return Json::parse(stream);
}
void WqeStart(Json* report, uint32_t node, uint32_t jetty, uint32_t task)
{
    report->at("wqe_starts").push_back({{"node", node}, {"jetty", jetty}, {"task", task},
        {"time_ns", ns3::Simulator::Now().GetNanoSeconds()}});
    auto active = report->at("active_wqes").at(node).get<unsigned>() + 1;
    (*report)["active_wqes"][node] = active;
    (*report)["peak_wqes"][node] = std::max(active, report->at("peak_wqes").at(node).get<unsigned>());
}
void WqeEnd(Json* report, uint32_t node, uint32_t jetty, uint32_t task)
{
    report->at("wqe_completions").push_back({{"node", node}, {"jetty", jetty}, {"task", task},
        {"time_ns", ns3::Simulator::Now().GetNanoSeconds()}});
    auto active = report->at("active_wqes").at(node).get<unsigned>();
    Require(active > 0, "Unexpected WQE completion");
    (*report)["active_wqes"][node] = active - 1;
}
void TargetWrite(Json* writes, ns3::Ptr<const ns3::UbWqeSegment> segment)
{
    writes->push_back({{"task", segment->GetTaskId()}, {"src", segment->GetSrc()},
        {"dst", segment->GetDest()}, {"jetty", segment->GetOriginJettyNum()},
        {"sequence", segment->GetRequestTassn()}, {"bytes", segment->GetSize()},
        {"time_ns", ns3::Simulator::Now().GetNanoSeconds()}});
}
int Library(dl_phdr_info* info, size_t, void* data)
{
    if (info->dlpi_name && info->dlpi_name[0]) static_cast<Json*>(data)->push_back(info->dlpi_name);
    return 0;
}
}
int main(int argc, char** argv)
{
    try
    {
        Require(argc == 6, "Usage: cosim-layer PLAN_DIR SYSTEM.json UB_CASE OBSERVATION.json|- NEW_RUN_DIR");
        auto plan_dir = fs::absolute(argv[1]);
        // Validate every graph before creating the run directory or any network.
        const auto plan = moe_ub::LoadLayerPlan(plan_dir);
        const auto system_config = ReadJson(fs::absolute(argv[2]));
        Require(system_config.at("roofline-enabled") == 0 && system_config.at("replay-only") == 0 &&
                system_config.at("trace-enabled") == 1, "Layer fixture requires original ASTRA execution and trace");
        auto network = fs::absolute(argv[3]);
        auto run_dir = fs::absolute(argv[5]);
        const uint64_t ready = plan.data.at("ready_ns"), stop = ready + 1000000;
        std::optional<moe_ub::ObservationConfig> config;
        if (std::string(argv[4]) != "-")
            config = moe_ub::ObservationConfig::Parse(ReadJson(fs::absolute(argv[4])), stop);
        const std::vector<std::string> files = {"network_attribute.txt", "node.csv", "topology.csv",
            "routing_table.csv", "transport_channel.csv"};
        for (const auto& name : files) Require(fs::is_regular_file(network / name), "Missing network file " + name);
        Require(!fs::exists(run_dir) && fs::create_directory(run_dir), "Run directory must be new with an existing parent");
        fs::current_path(run_dir);
        fs::create_directory("network");
        for (const auto& name : files) fs::copy_file(network / name, fs::path("network") / name);
        for (int rank = 0; rank < 4; ++rank)
            fs::copy_file(plan_dir / ("layer." + std::to_string(rank) + ".et"), "layer." + std::to_string(rank) + ".et");
        std::ofstream("plan.json") << plan.data.dump(2) << '\n';
        std::ofstream("system.json") << system_config.dump(2) << '\n';
        ns3::Time::SetResolution(ns3::Time::NS);
        ns3::RngSeedManager::SetSeed(10);
        auto* utils = utils::UbUtils::Get();
        utils->SetComponentsAttribute("network/network_attribute.txt");
        ns3::GlobalValue::Bind("UB_QUEUE_TRACE_ENABLE", ns3::BooleanValue(false));
        ns3::GlobalValue::Bind("UB_PARSE_TRACE_ENABLE", ns3::BooleanValue(false));
        ns3::Config::SetDefault("ns3::TpConnectionManager::RemoveUselessTp", ns3::BooleanValue(false));
        utils->CreateTraceDir();
        utils->CreateNode("network/node.csv");
        utils->CreateTopo("network/topology.csv");
        utils->AddRoutingTable("network/routing_table.csv");
        utils->CreateTp("network/transport_channel.csv");
        utils->TopoTraceConnect();
        Json report = {{"schema_version", "moe-ub-layer-result/1"}, {"graph_id", plan.data.at("graph_id")},
            {"calibration_status", "unvalidated_reference"}, {"sender_completion", "transaction_done"},
            {"graph_completions", Json::array()}, {"activation_order", Json::array()},
            {"wqe_starts", Json::array()}, {"wqe_completions", Json::array()},
            {"target_writes", Json::array()}, {"transfers", Json::array()},
            {"active_wqes", {0, 0, 0, 0}}, {"peak_wqes", {0, 0, 0, 0}},
            {"initial_tp_counts", Json::array()}, {"rank_resources", Json::array()},
            {"loaded_libraries", Json::array()}, {"later_event_executed", false}, {"test_stop_ns", stop}};
        std::vector<ns3::Ptr<ns3::UbController>> controllers;
        std::vector<ns3::Ptr<ns3::UbApp>> apps;
        for (int rank = 0; rank < 4; ++rank)
        {
            auto node = ns3::NodeList::GetNode(rank);
            auto controller = node->GetObject<ns3::UbController>();
            Require(controller != nullptr, "Rank needs a UB endpoint");
            controllers.push_back(controller);
            report["initial_tp_counts"].push_back(controller->GetTransportCountForTest());
            auto app = ns3::CreateObject<ns3::UbApp>();
            node->AddApplication(app); apps.push_back(app); utils->ClientTraceConnect(rank);
            Require(app->TraceConnectWithoutContext("WqeTaskStartsNotify", ns3::MakeBoundCallback(WqeStart, &report)) &&
                    app->TraceConnectWithoutContext("WqeTaskCompletesNotify", ns3::MakeBoundCallback(WqeEnd, &report)) &&
                    controller->GetUbTransaction()->TraceConnectWithoutContext("RemoteWriteCommitted",
                        ns3::MakeBoundCallback(TargetWrite, &report["target_writes"])), "Cannot attach layer execution traces");
        }
        moe_ub::InitializeAstraLogging();
        {
            std::vector<std::unique_ptr<moe_ub::UbWriteTransfer>> transfers;
            std::vector<moe_ub::UbWriteTransfer*> registry;
            std::vector<const moe_ub::UbWriteTransfer*> export_registry;
            for (const auto& d : plan.descriptors)
            {
                transfers.push_back(std::make_unique<moe_ub::UbWriteTransfer>(d, apps.at(d.src_rank)));
                registry.push_back(transfers.back().get()); export_registry.push_back(transfers.back().get());
            }
            std::unique_ptr<moe_ub::PortObservation> observation;
            if (config) observation = std::make_unique<moe_ub::PortObservation>(*config, plan.descriptors, controllers);
            std::vector<std::unique_ptr<moe_ub::UbNetworkAdapter>> adapters;
            std::vector<std::unique_ptr<AstraSim::Sys>> systems;
            for (int rank = 0; rank < 4; ++rank)
            {
                adapters.push_back(std::make_unique<moe_ub::UbNetworkAdapter>(rank, [&, rank](uint64_t now) {
                    report["graph_completions"].push_back({{"rank_id", rank}, {"time_ns", now}});
                }, registry));
                systems.push_back(std::make_unique<AstraSim::Sys>(rank, "layer", "empty", "system.json",
                    std::vector<AstraSim::AstraMemoryAPI*>{}, adapters.back().get(),
                    std::vector<int>{4}, std::vector<int>{1}, 1.0, 1.0, false));
            }
            report["loaded_ranks_before_activation"] = systems.size();
            for (int rank = 0; rank < 4; ++rank)
                ns3::Simulator::Schedule(ns3::NanoSeconds(ready), [&, rank]() {
                    report["activation_order"].push_back({{"rank_id", rank}, {"time_ns", ns3::Simulator::Now().GetNanoSeconds()}});
                    systems[rank]->workload->fire();
                });
            ns3::Simulator::Schedule(ns3::NanoSeconds(ready + 500000), [&]() { report["later_event_executed"] = true; });
            ns3::Simulator::Stop(ns3::NanoSeconds(stop));
            ns3::Simulator::Run();
            report["simulation_end_ns"] = ns3::Simulator::Now().GetNanoSeconds();
            report["csv_completed_tasks"] = ns3::UbTrafficGen::Get()->GetCompletedTaskCount();
            for (const auto& transfer : transfers)
            {
                const auto& d = transfer->Descriptor();
                const auto& done = transfer->Completion();
                Require(!done.Active() && done.SendCallbacks() == 1 && done.ReceiveCallbacks() == 1 &&
                        done.CommittedBytes() == d.bytes && !transfer->ObservingTarget(), "Incomplete layer transfer " + std::to_string(d.transfer_id));
                report["transfers"].push_back({{"transfer_id", d.transfer_id}, {"ub_task_id", d.task_id},
                    {"src_rank", d.src_rank}, {"dst_rank", d.dst_rank}, {"bytes", d.bytes},
                    {"submitted_tasks", transfer->SubmittedTasks()}, {"send_callbacks", done.SendCallbacks()},
                    {"receive_callbacks", done.ReceiveCallbacks()}, {"send_start_ns", *transfer->SendStartNs()},
                    {"receive_start_ns", *transfer->ReceiveStartNs()}, {"data_ready_ns", *done.DataReadyNs()},
                    {"transaction_done_ns", *done.TransactionDoneNs()}, {"send_done_ns", *done.SendDoneNs()},
                    {"receive_done_ns", *done.ReceiveDoneNs()}, {"committed_bytes", done.CommittedBytes()},
                    {"committed_segments", done.CommittedSegments()}, {"context_active", done.Active()}});
            }
            for (int rank = 0; rank < 4; ++rank)
            {
                const auto* workload = systems[rank]->workload;
                const auto* hardware = workload->hw_resource;
                Json state = {{"rank_id", rank}, {"finished", workload->is_finished},
                    {"remaining_nodes", workload->et_feeder->hasNodesToIssue()},
                    {"cpu_slots", hardware->num_in_flight_cpu_ops}, {"compute_slots", hardware->num_in_flight_gpu_comp_ops},
                    {"send_slots", hardware->num_in_flight_gpu_comm_ops}, {"pending_events", systems[rank]->pending_events},
                    {"pending_tasks", apps[rank]->GetTransferExecutor().GetPendingCount()},
                    {"tp_count", controllers[rank]->GetTransportCountForTest()}};
                Require(state["finished"] == true && state["remaining_nodes"] == false &&
                        state["cpu_slots"] == 0 && state["compute_slots"] == 0 && state["send_slots"] == 0 &&
                        state["pending_events"] == 0 && state["pending_tasks"] == 0, "Unfinished rank " + std::to_string(rank));
                report["rank_resources"].push_back(state);
            }
            for (const auto& event : report["wqe_starts"])
            {
                auto ctrl = controllers.at(event.at("node").get<unsigned>());
                auto jetty = event.at("jetty").get<uint32_t>();
                Require(!ctrl->GetUbFunction()->IsJettyExists(jetty) && !ctrl->GetUbTransaction()->HasJettyState(jetty), "Leaked source Jetty resources");
            }
            Require(report["graph_completions"].size() == 4 && report["wqe_starts"].size() == transfers.size() &&
                    report["wqe_completions"].size() == transfers.size() && report["active_wqes"] == Json::array({0,0,0,0}) &&
                    report["peak_wqes"] == Json::array({1,1,1,1}) && report["csv_completed_tasks"] == 0,
                    "Layer completion/one-send-slot invariant failed");
            moe_ub::FlushAstraLogging();
            report["node_execution"] = moe_ub::ReadLayerExecution(plan, "log/log.log");
            if (observation) observation->Export(export_registry,
                moe_ub::LayerEndpointOperations(plan, report["node_execution"]), moe_ub::LayerOperationRegistry(plan));
            dl_iterate_phdr(Library, &report["loaded_libraries"]);
        }
        utils->Destroy(); ns3::Simulator::Destroy(); moe_ub::ShutdownAstraLogging();
        std::ofstream output("result.json"); output << report.dump(2) << '\n'; output.close();
        Require(bool(output), "Cannot save layer result");
        std::cout << "Result: " << (run_dir / "result.json") << '\n';
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << error.what() << '\n';
        utils::UbUtils::Get()->Destroy(); ns3::Simulator::Destroy(); moe_ub::ShutdownAstraLogging();
        return 1;
    }
}
