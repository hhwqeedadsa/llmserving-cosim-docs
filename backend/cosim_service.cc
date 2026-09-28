#include "astra_logging.hh"
#include "graph_sequence.hh"
#include "port_observation.hh"
#include "ns3/simulator.h"
#include "ns3/ub-function.h"
#include "ns3/ub-switch.h"
#include "ns3/ub-transfer-executor.h"
#include "ns3/ub-utils.h"

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <link.h>
#include <memory>
#include <regex>
#include <set>
#include <stdexcept>
#include <unistd.h>

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

// The native allocator has a recurring deadlock diagnostic even on an idle
// network. Observe actual link arrivals and queues to distinguish it from data.
class NetworkDrain
{
  public:
    NetworkDrain()
    {
        for (auto node = ns3::NodeList::Begin(); node != ns3::NodeList::End(); ++node)
            for (uint32_t i = 0; i < (*node)->GetNDevices(); ++i)
            {
                auto port = ns3::DynamicCast<ns3::UbPort>((*node)->GetDevice(i));
                Require(bool(port), "Service fixture requires UB ports");
                ports_.push_back(port);
                Require(port->TraceConnectWithoutContext("TraComEventNotify",
                            ns3::MakeCallback(&NetworkDrain::Started, this)) &&
                        port->TraceConnectWithoutContext("PktRcvNotify",
                            ns3::MakeCallback(&NetworkDrain::Arrived, this)), "Cannot observe network drain");
            }
    }
    ~NetworkDrain()
    {
        for (const auto& port : ports_)
        {
            port->TraceDisconnectWithoutContext("TraComEventNotify", ns3::MakeCallback(&NetworkDrain::Started, this));
            port->TraceDisconnectWithoutContext("PktRcvNotify", ns3::MakeCallback(&NetworkDrain::Arrived, this));
        }
    }
    bool Idle() const
    {
        if (started_ != arrived_) return false;
        for (const auto& port : ports_)
        {
            const auto sw = port->GetNode()->GetObject<ns3::UbSwitch>();
            if (port->IsBusy() || !port->GetUbQueue()->IsEmpty() ||
                sw->GetQueueManager()->GetTotalOutPortBufferUsed(port->GetIfIndex()) != 0) return false;
            if (const auto controller = port->GetNode()->GetObject<ns3::UbController>())
                for (const auto& [id, tp] : controller->GetTpnMap())
                    if (!tp->IsEmpty() || tp->GetActiveSendSegmentCount() != 0 ||
                        tp->GetOutstandingUnackedSegmentCount() != 0) return false;
        }
        return true;
    }
    uint64_t StartedPackets() const { return started_; }
    uint64_t ArrivedPackets() const { return arrived_; }
    void Finish(std::function<void()> callback)
    {
        finished_ = std::move(callback);
        Probe();
        if (!stopped_ && last_port_completion_ >= ns3::Simulator::Now())
            ProbeAfter(last_port_completion_ - ns3::Simulator::Now());
    }
  private:
    void Probe()
    {
        if (finished_ && !stopped_ && Idle()) { stopped_ = true; finished_(); }
    }
    void ProbeAfter(ns3::Time delay)
    {
        // Native TransmitComplete is inserted after the start trace. Check in a
        // subsequent same-time event so it has already released the port.
        ns3::Simulator::Schedule(delay, [this]() { ns3::Simulator::ScheduleNow([this]() { Probe(); }); });
    }
    void Started(ns3::Ptr<ns3::Packet>, ns3::Time completion)
    {
        ++started_;
        last_port_completion_ = std::max(last_port_completion_, ns3::Simulator::Now() + completion);
        if (finished_) ProbeAfter(completion);
    }
    void Arrived(ns3::Ptr<ns3::Packet>)
    {
        Require(++arrived_ <= started_, "Arrival without successful link transmission");
        if (finished_) ns3::Simulator::ScheduleNow([this]() { Probe(); });
    }
    std::vector<ns3::Ptr<ns3::UbPort>> ports_;
    uint64_t started_ = 0, arrived_ = 0;
    ns3::Time last_port_completion_;
    std::function<void()> finished_;
    bool stopped_ = false;
};
}
int main(int argc, char** argv)
{
    // Reserve the original stdout exclusively for protocol. Upstream cout and
    // printf diagnostics go to stderr, including initialization/destruction logs.
    auto close_file = [](FILE* stream) { std::fclose(stream); };
    std::unique_ptr<FILE, decltype(close_file)> protocol(nullptr, close_file);
    try
    {
        Require(argc == 8, "Usage: cosim-service RUN_ID SHARED_DIR SYSTEM.json UB_CASE OBSERVATION.json|- NEW_RUN_DIR MAX_NS");
        const int fd = dup(STDOUT_FILENO);
        Require(fd >= 0 && dup2(STDERR_FILENO, STDOUT_FILENO) >= 0, "Cannot reserve protocol stdout");
        protocol.reset(fdopen(fd, "w"));
        Require(bool(protocol), "Cannot open protocol stream");
        const std::string run_id = argv[1];
        Require(std::regex_match(run_id, std::regex("[A-Za-z0-9_.-]+")), "Invalid run identity");
        const auto shared = fs::canonical(argv[2]);
        const auto system_config = ReadJson(fs::absolute(argv[3]));
        Require(system_config.at("roofline-enabled") == 0 && system_config.at("replay-only") == 0 &&
                system_config.at("trace-enabled") == 1, "Service requires original ASTRA execution and trace");
        const auto network = fs::absolute(argv[4]);
        const auto run_dir = fs::absolute(argv[6]);
        const std::string bound = argv[7];
        Require(std::regex_match(bound, std::regex("[0-9]+")), "Invalid simulation bound");
        const auto max_ns = std::stoull(bound);
        Require(max_ns > 0 && max_ns <= INT64_MAX, "Invalid simulation bound");
        std::optional<moe_ub::ObservationConfig> config;
        if (std::string(argv[5]) != "-")
            config = moe_ub::ObservationConfig::Parse(ReadJson(fs::absolute(argv[5])), max_ns);
        const std::vector<std::string> files = {"network_attribute.txt", "node.csv", "topology.csv",
            "routing_table.csv", "transport_channel.csv"};
        for (const auto& name : files) Require(fs::is_regular_file(network / name), "Missing network file " + name);
        // Preflight every bootstrap before constructing any Sys or network.
        for (int rank = 0; rank < 4; ++rank)
        {
            Chakra::ETFeeder empty((shared / "bootstrap" / ("layer." + std::to_string(rank) + ".et")).string());
            Require(!empty.hasNodesToIssue(), "Bootstrap must have zero nodes");
        }
        Require(!fs::exists(run_dir) && fs::create_directory(run_dir), "Run directory must be new with an existing parent");
        fs::current_path(run_dir);
        fs::create_directory("network");
        for (const auto& name : files) fs::copy_file(network / name, fs::path("network") / name);
        std::ofstream("system.json") << system_config.dump(2) << '\n';
        ns3::Time::SetResolution(ns3::Time::NS);
        ns3::RngSeedManager::SetSeed(10);
        auto* utils = utils::UbUtils::Get();
        utils->SetComponentsAttribute("network/network_attribute.txt");
        ns3::BooleanValue cc;
        ns3::GlobalValue::GetValueByName("UB_CC_ENABLED", cc);
        Require(!cc.Get(), "M3.1 fixture requires CC disabled (no perpetual CC timers)");
        ns3::GlobalValue::Bind("UB_QUEUE_TRACE_ENABLE", ns3::BooleanValue(false));
        ns3::GlobalValue::Bind("UB_PARSE_TRACE_ENABLE", ns3::BooleanValue(false));
        ns3::Config::SetDefault("ns3::TpConnectionManager::RemoveUselessTp", ns3::BooleanValue(false));
        utils->CreateTraceDir();
        utils->CreateNode("network/node.csv");
        utils->CreateTopo("network/topology.csv");
        utils->AddRoutingTable("network/routing_table.csv");
        utils->CreateTp("network/transport_channel.csv");
        utils->TopoTraceConnect();
        Json report = {{"schema_version", "moe-ub-service-result/1"},
            {"calibration_status", "unvalidated_reference"}, {"sender_completion", "transaction_done"},
            {"network_initializations", 1}, {"graph_completions", Json::array()},
            {"activation_order", Json::array()}, {"graph_loads", Json::array()},
            {"step_submissions", Json::array()}, {"boundary_resources", Json::array()},
            {"wqe_starts", Json::array()}, {"wqe_completions", Json::array()},
            {"target_writes", Json::array()}, {"transfers", Json::array()},
            {"active_wqes", {0, 0, 0, 0}}, {"peak_wqes", {0, 0, 0, 0}},
            {"initial_tp_counts", Json::array()}, {"rank_resources", Json::array()},
            {"loaded_libraries", Json::array()}};
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
                        ns3::MakeBoundCallback(TargetWrite, &report["target_writes"])), "Cannot attach execution traces");
        }
        moe_ub::InitializeAstraLogging();
        {
            NetworkDrain drain;
            std::vector<std::unique_ptr<moe_ub::UbWriteTransfer>> transfers;
            std::vector<const moe_ub::UbWriteTransfer*> export_registry;
            std::unique_ptr<moe_ub::PortObservation> observation;
            if (config) observation = std::make_unique<moe_ub::PortObservation>(*config,
                std::vector<moe_ub::WriteDescriptor>{}, controllers);
            std::vector<std::unique_ptr<moe_ub::UbNetworkAdapter>> adapters;
            std::vector<std::unique_ptr<AstraSim::Sys>> systems;
            std::vector<moe_ub::LayerPlan> plans;
            std::set<std::string> graphs, waves, seen_operations;
            std::set<uint64_t> transfer_ids, tasks, tags;
            std::set<int> completed_ranks;
            unsigned bootstrap_done = 0, send_slots = 0;
            uint64_t decision_id = 0;
            bool bootstrap = true, service_finished = false;
            std::function<void()> decide;
            for (int rank = 0; rank < 4; ++rank)
            {
                adapters.push_back(std::make_unique<moe_ub::UbNetworkAdapter>(rank, [&, rank](uint64_t now) {
                    if (bootstrap) { ++bootstrap_done; return; }
                    Require(completed_ranks.insert(rank).second, "Duplicate rank completion");
                    report["graph_completions"].push_back({{"rank_id", rank}, {"time_ns", now},
                        {"graph_id", systems[rank]->workload->graph_id()}});
                    if (completed_ranks.size() == 4) ns3::Simulator::ScheduleNow([&]() { decide(); });
                }));
                systems.push_back(std::make_unique<AstraSim::Sys>(rank, (shared / "bootstrap/layer").string(),
                    "empty", "system.json", std::vector<AstraSim::AstraMemoryAPI*>{}, adapters.back().get(),
                    std::vector<int>{4}, std::vector<int>{1}, 1.0, 1.0, false));
                systems.back()->workload->bind_graph("_bootstrap");
            }
            for (auto& sys : systems) sys->workload->activate_graph();
            Require(bootstrap_done == 4 && ns3::Simulator::Now().GetNanoSeconds() == 0 &&
                    report["wqe_starts"].empty() && drain.StartedPackets() == 0,
                    "Bootstrap generated work or advanced time");
            bootstrap = false;
            report["bootstrap"] = {{"finished_ranks", bootstrap_done}, {"time_ns", 0}, {"business_nodes", 0}};
            report["loaded_ranks_before_activation"] = systems.size();
            // This is a failure bound, never a successful completion timestamp.
            auto watchdog = ns3::Simulator::Schedule(ns3::NanoSeconds(max_ns), []() {
                throw std::runtime_error("Service exceeded max_simulation_ns");
            });
            decide = [&]() {
                const uint64_t now = ns3::Simulator::Now().GetNanoSeconds();
                for (int rank = 0; rank < 4; ++rank)
                {
                    Require(systems[rank]->workload->can_load_graph() &&
                            apps[rank]->GetTransferExecutor().GetPendingCount() == 0,
                            "Decision before all graph work retired");
                    Require(controllers[rank] == ns3::NodeList::GetNode(rank)->GetObject<ns3::UbController>() &&
                            apps[rank]->GetNode() == ns3::NodeList::GetNode(rank), "Network object changed");
                    report["boundary_resources"].push_back({{"rank_id", rank}, {"time_ns", now},
                        {"pending_events", systems[rank]->pending_events}, {"pending_tasks", 0},
                        {"same_network_objects", true}});
                }
                for (const auto& transfer : transfers)
                    Require(!transfer->Completion().Active() && !transfer->ObservingTarget(), "Retired transfer still active");
                Json completed = Json::array();
                if (!plans.empty()) completed.push_back({{"kind", "WAVE_DONE"},
                    {"graph_id", plans.back().data.at("graph_id")}, {"wave_id", plans.back().data.at("wave_id")},
                    {"finished_ns", now}});
                const Json message = {{"schema_version", "cosim/1"}, {"type", "DECIDE"}, {"run_id", run_id},
                    {"decision_id", decision_id}, {"now_ns", now}, {"completed", completed},
                    {"available_rank_ids", {0, 1, 2, 3}}};
                Require(std::fprintf(protocol.get(), "%s\n", message.dump().c_str()) > 0 &&
                        std::fflush(protocol.get()) == 0, "Cannot send DECIDE");
                std::string line;
                Require(bool(std::getline(std::cin, line)), "Frontend EOF while waiting for COMMIT");
                const auto commit = Json::parse(line);
                std::set<std::string> keys;
                for (const auto& [key, value] : commit.items()) keys.insert(key);
                Require(keys == std::set<std::string>{"schema_version", "type", "run_id", "decision_id", "now_ns",
                    "submissions", "next_wakeup_ns", "input_exhausted", "service_finished"}, "Invalid COMMIT fields");
                Require(commit.at("schema_version") == "cosim/1" && commit.at("type") == "COMMIT" &&
                    commit.at("run_id") == run_id && commit.at("decision_id").is_number_unsigned() &&
                    commit.at("decision_id") == decision_id && commit.at("now_ns").is_number_unsigned() &&
                    commit.at("now_ns") == now, "Mismatched COMMIT envelope, decision or absolute time");
                Require(commit.at("next_wakeup_ns").is_null() && commit.at("input_exhausted").is_boolean() &&
                    commit.at("input_exhausted") == true && commit.at("service_finished").is_boolean() &&
                    commit.at("submissions").is_array(), "M3.1 requires exhausted input and no external wakeup");
                ++decision_id;
                service_finished = commit.at("service_finished").get<bool>();
                if (service_finished)
                {
                    Require(commit.at("submissions").empty(), "Finished service cannot submit more graphs");
                    report["service_finished_ns"] = now;
                    drain.Finish([&]() {
                        ns3::Simulator::Remove(watchdog);
                        ns3::Simulator::Stop();
                    });
                    return;
                }
                Require(commit.at("submissions").size() == 1, "M3.1 requires one active EP group submission");
                const auto& submission = commit.at("submissions")[0];
                keys.clear();
                for (const auto& [key, value] : submission.items()) keys.insert(key);
                Require(keys == std::set<std::string>{"graph_id", "wave_id", "rank_ids", "workload_prefix", "semantic_manifest"},
                        "Invalid submission fields");
                Require(submission.at("rank_ids") == Json::array({0, 1, 2, 3}), "Wrong submission ranks");
                for (const auto& rank : submission.at("rank_ids")) Require(rank.is_number_unsigned(), "Invalid rank type");
                const auto graph = submission.at("graph_id").get<std::string>();
                const auto wave = submission.at("wave_id").get<std::string>();
                Require(std::regex_match(graph, std::regex("[A-Za-z0-9_.-]+")) && graph != "_bootstrap" &&
                        !graphs.count(graph) && !waves.count(wave), "Invalid or reused graph/wave identity");
                auto resolve = [&](const char* field) {
                    fs::path relative(submission.at(field).get<std::string>());
                    Require(relative.is_relative() && !relative.empty(), "Submission path must be relative");
                    auto path = fs::canonical(shared / relative);
                    const auto within = path.lexically_relative(shared);
                    Require(!within.empty() && *within.begin() != "..", "Submission path escapes shared directory");
                    return path;
                };
                const auto semantic = resolve("semantic_manifest");
                // The prefix itself is not a file. Resolve its existing parent first.
                fs::path prefix(submission.at("workload_prefix").get<std::string>());
                Require(prefix.is_relative() && prefix.filename() == "layer" &&
                        fs::canonical(shared / prefix.parent_path()) == semantic.parent_path() &&
                        semantic.filename() == "plan.json", "Workload and semantic manifest disagree");
                const auto directory = semantic.parent_path();
                auto plan = moe_ub::LoadLayerPlan(directory);
                Require(plan.data.at("graph_id") == graph && plan.data.at("wave_id") == wave &&
                        plan.data.at("input").at("graph_id") == graph &&
                        plan.data.at("input").at("wave_id") == wave &&
                        plan.data.at("input").at("ready_ns").is_number_unsigned() &&
                        plan.data.at("ready_ns") == now && plan.data.at("input").at("ready_ns") == now,
                        "Submission identity/time differs from plan");
                const auto slots = plan.data.at("send_slots_per_rank").get<unsigned>();
                Require(send_slots == 0 || slots == send_slots, "Cannot change send slots during a run");
                for (const auto& op : plan.data.at("operations"))
                    Require(op.at("wave_id") == wave &&
                            !seen_operations.count(op.at("operation_id").get<std::string>()),
                            "Operation has wrong wave or reused identity");
                for (const auto& d : plan.descriptors)
                    Require(!transfer_ids.count(d.transfer_id) && !tasks.count(d.task_id) && !tags.count(d.tag),
                            "Reused transfer, UB task or comm_tag");
                std::vector<std::unique_ptr<moe_ub::UbWriteTransfer>> prepared;
                std::vector<moe_ub::UbWriteTransfer*> pointers;
                for (const auto& d : plan.descriptors)
                {
                    prepared.push_back(std::make_unique<moe_ub::UbWriteTransfer>(d, apps.at(d.src_rank)));
                    pointers.push_back(prepared.back().get());
                }
                moe_ub::InstallNextGraph(plan, directory, systems, adapters, pointers);
                if (observation) observation->Register(plan.descriptors);
                if (!send_slots)
                    for (auto& sys : systems) sys->workload->hw_resource->configure_p2p_send_slots(slots);
                send_slots = slots;
                graphs.insert(graph); waves.insert(wave);
                for (const auto& op : plan.data.at("operations")) seen_operations.insert(op.at("operation_id").get<std::string>());
                for (const auto& d : plan.descriptors)
                { transfer_ids.insert(d.transfer_id); tasks.insert(d.task_id); tags.insert(d.tag); }
                for (auto& transfer : prepared)
                { export_registry.push_back(transfer.get()); transfers.push_back(std::move(transfer)); }
                plans.push_back(std::move(plan));
                completed_ranks.clear();
                report["step_submissions"].push_back({{"graph_id", graph}, {"wave_id", wave}, {"time_ns", now}});
                for (int rank = 0; rank < 4; ++rank)
                    report["graph_loads"].push_back({{"graph_id", graph}, {"rank_id", rank}, {"time_ns", now}});
                for (int rank = 0; rank < 4; ++rank)
                {
                    report["activation_order"].push_back({{"graph_id", graph}, {"rank_id", rank}, {"time_ns", now}});
                    systems[rank]->workload->activate_graph();
                }
            };
            // Initial decision is an event after every Sys, adapter and UB object exists.
            ns3::Simulator::ScheduleNow([&]() { decide(); });
            ns3::Simulator::Run();
            Require(service_finished, "Event queue ended with unfinished service");
            Require(drain.Idle(), "Service stopped with link data or queued packets");
            report["network_drain"] = {{"started_packets", drain.StartedPackets()},
                {"arrived_packets", drain.ArrivedPackets()}, {"idle", true}};
            report["simulation_end_ns"] = ns3::Simulator::Now().GetNanoSeconds();
            report["decisions"] = decision_id;
            report["normal_exit"] = true;
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
                report["transfers"].back()["graph_id"] = d.graph_id;
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
                Require(hardware->active_p2p_sends() == 0, "Unreleased point-to-point send slot");
                {
                    state["stale_callbacks"] = workload->stale_callbacks();
                    state["graph_id"] = workload->graph_id(); state["iteration"] = workload->iteration;
                }
                report["rank_resources"].push_back(state);
            }
            for (const auto& event : report["wqe_starts"])
            {
                auto ctrl = controllers.at(event.at("node").get<unsigned>());
                auto jetty = event.at("jetty").get<uint32_t>();
                Require(!ctrl->GetUbFunction()->IsJettyExists(jetty) && !ctrl->GetUbTransaction()->HasJettyState(jetty), "Leaked source Jetty resources");
            }
            Require(report["graph_completions"].size() == 4 * plans.size() && report["wqe_starts"].size() == transfers.size() &&
                    report["wqe_completions"].size() == transfers.size() && report["active_wqes"] == Json::array({0,0,0,0}) &&
                    report["csv_completed_tasks"] == 0, "Service completion invariant failed");
            for (const auto& peak : report["peak_wqes"])
                Require(peak <= send_slots, "Layer SEND slot limit exceeded");
            moe_ub::FlushAstraLogging();
            Json endpoints = Json::array(), operations = Json::array();
            report["node_execution"] = Json::array();
            for (size_t step = 0; step < plans.size(); ++step)
            {
                auto execution = moe_ub::ReadLayerExecution(plans[step], "log/log.log", true);
                auto local = moe_ub::LayerEndpointOperations(plans[step], execution);
                auto semantic = moe_ub::LayerOperationRegistry(plans[step]);
                for (auto& event : execution)
                {
                    event["graph_id"] = plans[step].data.at("graph_id");
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
        std::cerr << "Result: " << (run_dir / "result.json") << '\n';
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << "cosim-service: " << error.what() << '\n';
        utils::UbUtils::Get()->Destroy(); ns3::Simulator::Destroy(); moe_ub::ShutdownAstraLogging();
        return 1;
    }
}
