#include "astra_logging.hh"
#include "layer_plan.hh"
#include "graph_sequence.hh"
#ifdef COSIM_LIFECYCLE_TEST
#include "astra-sim/system/WorkloadLayerHandlerData.hh"
#include "astra-sim/system/SendPacketEventHandlerData.hh"
#include "astra-sim/system/RecvPacketEventHandlerData.hh"
#endif
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
void WqeStart(Json* report, const std::map<uint32_t, size_t>* indices, uint32_t node, uint32_t jetty, uint32_t task)
{
    report->at("wqe_starts").push_back({{"node", node}, {"jetty", jetty}, {"task", task},
        {"time_ns", ns3::Simulator::Now().GetNanoSeconds()}});
    auto active = report->at("active_wqes").at(indices->at(node)).get<unsigned>() + 1;
    (*report)["active_wqes"][indices->at(node)] = active;
    (*report)["peak_wqes"][indices->at(node)] = std::max(active, report->at("peak_wqes").at(indices->at(node)).get<unsigned>());
}
void WqeEnd(Json* report, const std::map<uint32_t, size_t>* indices, uint32_t node, uint32_t jetty, uint32_t task)
{
    report->at("wqe_completions").push_back({{"node", node}, {"jetty", jetty}, {"task", task},
        {"time_ns", ns3::Simulator::Now().GetNanoSeconds()}});
    auto active = report->at("active_wqes").at(indices->at(node)).get<unsigned>();
    Require(active > 0, "Unexpected WQE completion");
    (*report)["active_wqes"][indices->at(node)] = active - 1;
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
        Require(argc == 6, "Usage: " + fs::path(argv[0]).filename().string() +
                " PLAN_OR_SEQUENCE_DIR SYSTEM.json UB_CASE OBSERVATION.json|- NEW_RUN_DIR");
        auto plan_dir = fs::absolute(argv[1]);
        // Validate every graph before creating the run directory or any network.
#ifdef COSIM_SEQUENCE
        constexpr bool sequence = true;
#else
        constexpr bool sequence = false;
#endif
        auto plans = sequence ? moe_ub::LoadGraphSequence(plan_dir) :
            std::vector<moe_ub::LayerPlan>{moe_ub::LoadLayerPlan(plan_dir)};
        const auto& plan = plans.front();
        const auto ranks = plan.data.at("rank_ids").get<std::vector<int>>();
        const auto nodes = plan.data.at("rank_to_ub_node").get<std::vector<uint32_t>>();
        std::map<int, size_t> rank_index;
        std::map<uint32_t, size_t> node_index;
        for (size_t i = 0; i < ranks.size(); ++i)
        {
            rank_index.emplace(ranks[i], i);
            node_index.emplace(nodes[i], i);
        }
        const auto send_slots = plan.data.at("send_slots_per_rank").get<unsigned>();
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
        for (size_t step = 0; step < plans.size(); ++step)
        {
            const auto source = sequence ? plan_dir / std::to_string(step) : plan_dir;
            const auto destination = sequence ? fs::path(std::to_string(step)) : fs::path(".");
            if (sequence) fs::create_directory(destination);
            for (int rank : ranks)
                fs::copy_file(source / ("layer." + std::to_string(rank) + ".et"),
                              destination / ("layer." + std::to_string(rank) + ".et"));
            std::ofstream(destination / "plan.json") << plans[step].data.dump(2) << '\n';
        }
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
        // Validate every member, including idle ranks, before graph activation.
        for (uint32_t node_id : nodes)
        {
            Require(node_id < ns3::NodeList::GetNNodes(), "Mapped UB endpoint does not exist");
            auto node = ns3::NodeList::GetNode(node_id);
            Require(node->GetObject<ns3::UbController>() != nullptr,
                    "Mapped UB node is not a compute endpoint");
        }
        utils->CreateTopo("network/topology.csv");
        utils->AddRoutingTable("network/routing_table.csv");
        utils->CreateTp("network/transport_channel.csv");
        utils->TopoTraceConnect();
        Json report = {{"schema_version", "moe-ub-layer-result/1"}, {"graph_id", plan.data.at("graph_id")},
            {"calibration_status", "unvalidated_reference"}, {"sender_completion", "transaction_done"},
            {"graph_completions", Json::array()}, {"activation_order", Json::array()},
            {"wqe_starts", Json::array()}, {"wqe_completions", Json::array()},
            {"target_writes", Json::array()}, {"transfers", Json::array()},
            {"rank_ids", ranks}, {"rank_to_ub_node", nodes},
            {"active_wqes", std::vector<unsigned>(ranks.size(), 0)}, {"peak_wqes", std::vector<unsigned>(ranks.size(), 0)},
            {"initial_tp_counts", Json::array()}, {"rank_resources", Json::array()},
            {"loaded_libraries", Json::array()}, {"later_event_executed", false}, {"test_stop_ns", stop}};
        size_t current_step = 0;
        if (sequence)
        {
            report["schema_version"] = "moe-ub-sequence-result/1";
            report["graph_loads"] = Json::array();
            report["step_submissions"] = Json::array();
            report["boundary_resources"] = Json::array();
            report["network_initializations"] = 1;
        }
        std::vector<ns3::Ptr<ns3::UbController>> controllers;
        std::vector<ns3::Ptr<ns3::UbApp>> apps;
        for (int rank : ranks)
        {
            auto node = ns3::NodeList::GetNode(nodes.at(rank_index.at(rank)));
            auto controller = node->GetObject<ns3::UbController>();
            Require(controller != nullptr, "Rank needs a UB endpoint");
            controllers.push_back(controller);
            report["initial_tp_counts"].push_back(controller->GetTransportCountForTest());
            auto app = ns3::CreateObject<ns3::UbApp>();
            node->AddApplication(app); apps.push_back(app); utils->ClientTraceConnect(nodes.at(rank_index.at(rank)));
            Require(app->TraceConnectWithoutContext("WqeTaskStartsNotify", ns3::MakeBoundCallback(WqeStart, &report, &node_index)) &&
                    app->TraceConnectWithoutContext("WqeTaskCompletesNotify", ns3::MakeBoundCallback(WqeEnd, &report, &node_index)) &&
                    controller->GetUbTransaction()->TraceConnectWithoutContext("RemoteWriteCommitted",
                        ns3::MakeBoundCallback(TargetWrite, &report["target_writes"])), "Cannot attach layer execution traces");
        }
        moe_ub::InitializeAstraLogging();
        {
            std::vector<std::unique_ptr<moe_ub::UbWriteTransfer>> transfers;
            std::vector<const moe_ub::UbWriteTransfer*> export_registry;
            std::vector<std::vector<moe_ub::UbWriteTransfer*>> step_transfers(plans.size());
            std::vector<moe_ub::WriteDescriptor> descriptors;
            for (size_t step = 0; step < plans.size(); ++step)
            for (const auto& d : plans[step].descriptors)
            {
                transfers.push_back(std::make_unique<moe_ub::UbWriteTransfer>(d, apps.at(rank_index.at(d.src_rank))));
                export_registry.push_back(transfers.back().get());
                step_transfers[step].push_back(transfers.back().get()); descriptors.push_back(d);
            }
            std::unique_ptr<moe_ub::PortObservation> observation;
            if (config) observation = std::make_unique<moe_ub::PortObservation>(*config, descriptors, controllers);
            std::vector<std::unique_ptr<moe_ub::UbNetworkAdapter>> adapters;
            std::vector<std::unique_ptr<AstraSim::Sys>> systems;
            std::function<void()> advance;
            auto identity = [&]() { return plans.at(current_step).data.at("graph_id"); };
            auto activate = [&]() {
                for (int rank : ranks)
                {
                    Json event = {{"rank_id", rank}, {"time_ns", ns3::Simulator::Now().GetNanoSeconds()}};
                    if (sequence) event["graph_id"] = identity();
                    report["activation_order"].push_back(event);
                    if (sequence) systems[rank_index.at(rank)]->workload->activate_graph();
                    else systems[rank_index.at(rank)]->workload->fire();
                }
            };
            for (int rank : ranks)
            {
                adapters.push_back(std::make_unique<moe_ub::UbNetworkAdapter>(rank, [&, rank](uint64_t now) {
                    Json done = {{"rank_id", rank}, {"time_ns", now}};
                    if (sequence) done["graph_id"] = systems[rank_index.at(rank)]->workload->graph_id();
                    report["graph_completions"].push_back(done);
                    if (sequence && report["graph_completions"].size() == ranks.size())
                        ns3::Simulator::ScheduleNow([&]() { advance(); });
                }, sequence ? std::vector<moe_ub::UbWriteTransfer*>{} : step_transfers[0]));
                systems.push_back(std::make_unique<AstraSim::Sys>(rank, sequence ? "0/layer" : "layer", "empty", "system.json",
                    std::vector<AstraSim::AstraMemoryAPI*>{}, adapters.back().get(),
                    std::vector<int>{ranks.back() + 1}, std::vector<int>{1}, 1.0, 1.0, false));
                systems.back()->workload->hw_resource->configure_p2p_send_slots(send_slots);
                if (sequence)
                {
                    systems.back()->workload->bind_graph(plan.data.at("graph_id"));
                    report["graph_loads"].push_back({{"rank_id", rank}, {"graph_id", identity()}, {"time_ns", 0}});
                }
            }
            if (sequence)
                for (auto& adapter : adapters) adapter->RegisterTransfers(step_transfers[0]);
            report["loaded_ranks_before_activation"] = systems.size();
            if (sequence) report["step_submissions"].push_back({{"graph_id", identity()}, {"time_ns", ready}});
            advance = [&]() {
                const auto now = ns3::Simulator::Now().GetNanoSeconds();
                for (int rank : ranks)
                {
                    Require(systems[rank_index.at(rank)]->workload->can_load_graph() &&
                            apps[rank_index.at(rank)]->GetTransferExecutor().GetPendingCount() == 0,
                            "Cannot advance before all graph work has retired");
                    Require(controllers[rank_index.at(rank)] == ns3::NodeList::GetNode(nodes.at(rank_index.at(rank)))->GetObject<ns3::UbController>() &&
                            apps[rank_index.at(rank)]->GetNode() == ns3::NodeList::GetNode(nodes.at(rank_index.at(rank))), "Network object changed");
                    report["boundary_resources"].push_back({{"rank_id", rank}, {"time_ns", now},
                        {"old_graph_id", identity()}, {"pending_events", systems[rank_index.at(rank)]->pending_events},
                        {"pending_tasks", apps[rank_index.at(rank)]->GetTransferExecutor().GetPendingCount()},
                        {"same_network_objects", true}, {"iteration", systems[rank_index.at(rank)]->workload->iteration}});
                }
                for (auto* transfer : step_transfers[0])
                    Require(!transfer->Completion().Active() && !transfer->ObservingTarget(), "Old transfer still active");
#ifdef COSIM_LIFECYCLE_TEST
                // Corrupt only this test run's final rank, then restore the exact
                // bytes after proving the group has not been partially installed.
                const auto last_graph = "1/layer." + std::to_string(ranks.back()) + ".et";
                std::ifstream graph_input(last_graph, std::ios::binary);
                const std::string saved((std::istreambuf_iterator<char>(graph_input)), {});
                graph_input.close();
                std::ofstream(last_graph, std::ios::binary).put(char(0x80));
                bool rejected = false;
                try { moe_ub::InstallNextGraph(plans[1], "1", systems, adapters, step_transfers[1]); }
                catch (const std::exception& e) { rejected = true; report["atomic_rejection"] = e.what(); }
                Require(rejected, "Malformed final rank was accepted");
                std::ofstream(last_graph, std::ios::binary).write(saved.data(), saved.size());
                for (const auto& sys : systems)
                    Require(sys->workload->graph_id() == identity() && sys->workload->can_load_graph(), "Partial graph install");
                rejected = false;
                try { moe_ub::InstallNextGraph(plans[0], "0", systems, adapters, step_transfers[0]); }
                catch (const std::exception& e) { rejected = true; report["reuse_rejection"] = e.what(); }
                Require(rejected, "Retired graph identity was accepted");
#endif
                moe_ub::InstallNextGraph(plans[1], "1", systems, adapters, step_transfers[1]);
                current_step = 1;
                plans[1].data["ready_ns"] = now;
                plans[1].data["input"]["ready_ns"] = now;
                std::ofstream("1/plan.json") << plans[1].data.dump(2) << '\n';
                report["step_submissions"].push_back({{"graph_id", identity()}, {"time_ns", now}});
                for (int rank : ranks)
                    report["graph_loads"].push_back({{"rank_id", rank}, {"graph_id", identity()}, {"time_ns", now}});
                activate();
#ifdef COSIM_LIFECYCLE_TEST
                // Fresh copies of retired callback identities exercise the same
                // Workload / Sys dispatch paths without borrowing freed memory.
                for (const auto& node : plan.data.at("rank_graphs")[0].at("nodes"))
                {
                    auto kind = node.at("kind").get<std::string>();
                    if (node.at("node_id") != 1 && kind == "COMP") continue;
                    if (kind != "COMP" && node.at("name").get<std::string>().find("dispatch-") != 0) continue;
                    const auto delay = kind == "COMP" ? 10 : plan.data.at("input").at("compute_profile_ns").at("attention_route_pack").get<uint64_t>() + 1;
                    ns3::Simulator::Schedule(ns3::NanoSeconds(delay), [&, node, kind]() {
                        auto* context = new AstraSim::WorkloadLayerHandlerData;
                        context->graph_id = plan.data.at("graph_id"); context->node_id = node.at("node_id");
                        if (kind == "COMP") systems[0]->workload->call(AstraSim::EventType::General, context);
                        else
                        {
                            for (const auto& d : plan.descriptors)
                                if (d.transfer_id == node.at("transfer_id")) context->comm_tag = d.tag;
                            if (kind == "SEND")
                            {
                                auto* event = new AstraSim::SendPacketEventHandlerData;
                                event->sys_id = ranks.front(); event->event = AstraSim::EventType::PacketSent;
                                event->callable = systems[0]->workload; event->wlhd = context;
                                AstraSim::Sys::handleEvent(event);
                            }
                            else
                            {
                                auto* event = new AstraSim::RecvPacketEventHandlerData;
                                event->sys_id = ranks.front(); event->event = AstraSim::EventType::PacketReceived;
                                event->workload = systems[0]->workload; event->wlhd = context;
                                AstraSim::Sys::handleEvent(event);
                            }
                        }
                    });
                }
#endif
            };
#ifdef COSIM_LIFECYCLE_TEST
            bool unactivated_rejected = false;
            try { systems[0]->workload->fire(); }
            catch (const std::exception& e) { unactivated_rejected = true; report["unactivated_rejection"] = e.what(); }
            Require(unactivated_rejected, "Unactivated graph fired");
#endif
            ns3::Simulator::Schedule(ns3::NanoSeconds(ready), activate);
#ifdef COSIM_LIFECYCLE_TEST
            ns3::Simulator::Schedule(ns3::NanoSeconds(ready + 1), [&]() {
                unsigned rejected = 0;
                try { moe_ub::InstallNextGraph(plans[1], "1", systems, adapters, step_transfers[1]); }
                catch (const std::exception& e) { ++rejected; report["busy_rejection"] = e.what(); }
                try { systems[0]->workload->activate_graph(); }
                catch (const std::exception& e) { ++rejected; report["activation_rejection"] = e.what(); }
                Require(rejected == 2, "Expected busy replacement and duplicate activation rejection");
                for (const auto& sys : systems) Require(sys->workload->graph_id() == identity(), "Partial replacement");
            });
#endif
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
                    {"src_rank", d.src_rank}, {"dst_rank", d.dst_rank},
                    {"src_ub_node", d.SourceNode()}, {"dst_ub_node", d.DestinationNode()}, {"bytes", d.bytes},
                    {"submitted_tasks", transfer->SubmittedTasks()}, {"send_callbacks", done.SendCallbacks()},
                    {"receive_callbacks", done.ReceiveCallbacks()}, {"send_start_ns", *transfer->SendStartNs()},
                    {"receive_start_ns", *transfer->ReceiveStartNs()}, {"data_ready_ns", *done.DataReadyNs()},
                    {"transaction_done_ns", *done.TransactionDoneNs()}, {"send_done_ns", *done.SendDoneNs()},
                    {"receive_done_ns", *done.ReceiveDoneNs()}, {"committed_bytes", done.CommittedBytes()},
                    {"committed_segments", done.CommittedSegments()}, {"context_active", done.Active()}});
                if (sequence) report["transfers"].back()["graph_id"] = d.graph_id;
            }
            for (int rank : ranks)
            {
                const auto* workload = systems[rank_index.at(rank)]->workload;
                const auto* hardware = workload->hw_resource;
                Json state = {{"rank_id", rank}, {"finished", workload->is_finished},
                    {"remaining_nodes", workload->et_feeder->hasNodesToIssue()},
                    {"cpu_slots", hardware->num_in_flight_cpu_ops}, {"compute_slots", hardware->num_in_flight_gpu_comp_ops},
                    {"send_slots", hardware->num_in_flight_gpu_comm_ops}, {"pending_events", systems[rank_index.at(rank)]->pending_events},
                    {"pending_tasks", apps[rank_index.at(rank)]->GetTransferExecutor().GetPendingCount()},
                    {"tp_count", controllers[rank_index.at(rank)]->GetTransportCountForTest()}};
                Require(state["finished"] == true && state["remaining_nodes"] == false &&
                        state["cpu_slots"] == 0 && state["compute_slots"] == 0 && state["send_slots"] == 0 &&
                        state["pending_events"] == 0 && state["pending_tasks"] == 0, "Unfinished rank " + std::to_string(rank));
                Require(hardware->active_p2p_sends() == 0, "Unreleased point-to-point send slot");
                if (sequence)
                {
                    state["stale_callbacks"] = workload->stale_callbacks();
                    state["graph_id"] = workload->graph_id(); state["iteration"] = workload->iteration;
                }
                report["rank_resources"].push_back(state);
            }
            for (const auto& event : report["wqe_starts"])
            {
                auto ctrl = controllers.at(node_index.at(event.at("node").get<unsigned>()));
                auto jetty = event.at("jetty").get<uint32_t>();
                Require(!ctrl->GetUbFunction()->IsJettyExists(jetty) && !ctrl->GetUbTransaction()->HasJettyState(jetty), "Leaked source Jetty resources");
            }
            Require(report["graph_completions"].size() == ranks.size() * plans.size() && report["wqe_starts"].size() == transfers.size() &&
                    report["wqe_completions"].size() == transfers.size() && report["active_wqes"] == Json(std::vector<unsigned>(ranks.size(), 0)) &&
                    report["csv_completed_tasks"] == 0, "Layer completion invariant failed");
            for (size_t i = 0; i < ranks.size(); ++i)
            {
                const auto peak = report["peak_wqes"][i].get<unsigned>();
                const bool has_send = std::any_of(descriptors.begin(), descriptors.end(),
                    [&](const auto& d) { return d.src_rank == ranks[i]; });
                Require(peak <= send_slots && (peak > 0) == has_send,
                        "Layer SEND activity or slot limit mismatch");
            }
            moe_ub::FlushAstraLogging();
            Json endpoints = Json::array(), operations = Json::array();
            report["node_execution"] = Json::array();
            for (size_t step = 0; step < plans.size(); ++step)
            {
                auto execution = moe_ub::ReadLayerExecution(plans[step], "log/log.log", sequence);
                auto local = moe_ub::LayerEndpointOperations(plans[step], execution);
                auto semantic = moe_ub::LayerOperationRegistry(plans[step]);
                for (auto& event : execution)
                {
                    if (sequence) event["graph_id"] = plans[step].data.at("graph_id");
                    report["node_execution"].push_back(event);
                }
                for (auto& record : local)
                {
                    record["local_record_id"] = record.at("local_record_id").get<unsigned>() + 2 * step;
                    endpoints.push_back(record);
                }
                for (auto& operation : semantic)
                {
                    for (auto& record : operation["endpoint_records"])
                        record["local_record_id"] = record.at("local_record_id").get<unsigned>() + 2 * step;
                    operations.push_back(operation);
                }
            }
            if (observation) observation->Export(export_registry, endpoints, operations);
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
