#include "astra_logging.hh"
#include "ub_network_adapter.hh"
#include "ub_write_transfer.hh"

#include "astra-sim/system/Sys.hh"
#include "et_def.pb.h"
#include "extern/helper/json/json.hpp"
#include "protoio.hh"
#include "ns3/simulator.h"
#include "ns3/ub-function.h"
#include "ns3/ub-transfer-executor.h"
#include "ns3/ub-utils.h"

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
    if (!stream) throw std::runtime_error("Cannot open " + path.string());
    return Json::parse(stream);
}

uint64_t Number(const Json& input, const char* key, uint64_t maximum)
{
    if (!input.at(key).is_number_unsigned() || input.at(key).get<uint64_t>() > maximum)
        throw std::invalid_argument(std::string("Invalid unsigned field: ") + key);
    return input.at(key).get<uint64_t>();
}

void WriteGraph(const moe_ub::WriteDescriptor& descriptor)
{
    ProtoOutputStream output("write.0.et");
    ChakraProtoMsg::GlobalMetadata metadata;
    metadata.set_version("m1-write/1");
    output.write(metadata);
    ChakraProtoMsg::Node node;
    node.set_id(descriptor.send_node_id);
    node.set_name("synthetic-send");
    node.set_type(ChakraProtoMsg::COMM_SEND_NODE);
    auto* cpu = node.add_attr();
    cpu->set_name("is_cpu_op");
    cpu->set_bool_val(false);
    auto* size = node.add_attr();
    size->set_name("comm_size");
    size->set_int64_val(descriptor.bytes);
    for (const auto& [name, value] : std::vector<std::pair<std::string, int>>{
             {"comm_src", descriptor.src_rank}, {"comm_dst", descriptor.dst_rank},
             {"comm_tag", descriptor.tag}})
    {
        auto* attr = node.add_attr();
        attr->set_name(name);
        attr->set_int32_val(value);
    }
    output.write(node);
}

void RecordWqe(Json* entries, uint32_t node, uint32_t jetty, uint32_t task)
{
    entries->push_back({{"node", node}, {"jetty", jetty}, {"task", task},
                        {"time_ns", ns3::Simulator::Now().GetNanoSeconds()}});
}

int RecordLibrary(dl_phdr_info* info, std::size_t, void* data)
{
    if (info->dlpi_name && info->dlpi_name[0])
        static_cast<Json*>(data)->push_back(info->dlpi_name);
    return 0;
}

void ReceiveCallback(void* data) { ++*static_cast<uint64_t*>(data); }
}

int main(int argc, char** argv)
{
    try
    {
        if (argc != 5)
            throw std::invalid_argument("Usage: cosim-write INPUT.json SYSTEM.json UB_CASE NEW_RUN_DIR");
        const auto input = ReadJson(fs::absolute(argv[1]));
        const auto system_config = ReadJson(fs::absolute(argv[2]));
        const auto ub_case = fs::absolute(argv[3]);
        const auto run_dir = fs::absolute(argv[4]);
        const std::set<std::string> expected_keys = {"schema_version", "start_ns", "bytes",
            "transfer_id", "task_id", "tag", "receive_first", "reclaim_tps"};
        std::set<std::string> keys;
        for (auto it = input.begin(); it != input.end(); ++it) keys.insert(it.key());
        if (keys != expected_keys || input.at("schema_version") != "moe-ub-write/1" ||
            !input.at("receive_first").is_boolean() || !input.at("reclaim_tps").is_boolean())
            throw std::invalid_argument("Unsupported write input schema or fields");
        constexpr uint64_t horizon_ns = 1000000;
        const auto start = Number(input, "start_ns",
                                  std::numeric_limits<int64_t>::max() - horizon_ns);
        const auto bytes = Number(input, "bytes", UINT32_MAX);
        const auto task = Number(input, "task_id", UINT32_MAX);
        // ASTRA's NATIVE interface retains tags below 500000000 without remapping.
        const auto tag = Number(input, "tag", 499999999);
        const auto transfer_id = Number(input, "transfer_id", UINT64_MAX);
        if (bytes == 0 || system_config.at("roofline-enabled") != 0 ||
            system_config.at("replay-only") != 0)
            throw std::invalid_argument("Require positive bytes and original ASTRA execution");
        const std::vector<std::string> files = {"network_attribute.txt", "node.csv", "topology.csv",
                                               "routing_table.csv", "transport_channel.csv"};
        for (const auto& name : files)
            if (!fs::is_regular_file(ub_case / name))
                throw std::invalid_argument("Missing network input: " + name);
        if (fs::exists(run_dir) || !fs::create_directory(run_dir))
            throw std::invalid_argument("The run directory must be new and its parent must exist");
        fs::current_path(run_dir);
        fs::create_directory("network");
        for (const auto& name : files) fs::copy_file(ub_case / name, fs::path("network") / name);
        std::ofstream("input.json") << input.dump(2) << '\n';
        std::ofstream("system.json") << system_config.dump(2) << '\n';
        const moe_ub::WriteDescriptor descriptor{transfer_id, task, "m1-write", 1, 2, 0, 1,
                                                static_cast<int>(tag), bytes};
        WriteGraph(descriptor);
        ns3::Time::SetResolution(ns3::Time::NS);
        ns3::RngSeedManager::SetSeed(10);
        auto* utils = utils::UbUtils::Get();
        utils->SetComponentsAttribute("network/network_attribute.txt");
        // No periodic queue sampling or external trace parser is needed for this bounded test.
        ns3::GlobalValue::Bind("UB_QUEUE_TRACE_ENABLE", ns3::BooleanValue(false));
        ns3::GlobalValue::Bind("UB_PARSE_TRACE_ENABLE", ns3::BooleanValue(false));
        ns3::Config::SetDefault("ns3::TpConnectionManager::RemoveUselessTp",
                               ns3::BooleanValue(input.at("reclaim_tps").get<bool>()));
        utils->CreateTraceDir();
        utils->CreateNode("network/node.csv");
        utils->CreateTopo("network/topology.csv");
        utils->AddRoutingTable("network/routing_table.csv");
        utils->CreateTp("network/transport_channel.csv");
        utils->TopoTraceConnect();
        auto source_node = ns3::NodeList::GetNode(0);
        auto source_ctrl = source_node->GetObject<ns3::UbController>();
        auto target_ctrl = ns3::NodeList::GetNode(1)->GetObject<ns3::UbController>();
        if (!source_ctrl || !target_ctrl) throw std::invalid_argument("Ranks 0 and 1 need UB devices");
        auto app = ns3::CreateObject<ns3::UbApp>();
        source_node->AddApplication(app);
        utils->ClientTraceConnect(0);
        Json report = {{"schema_version", "moe-ub-write-result/1"},
            {"calibration_status", "unvalidated_reference"}, {"sender_completion", "transaction_done"},
            {"transfer_id", transfer_id}, {"ub_task_id", task}, {"bytes", bytes}, {"priority", 7},
            {"graph_id", descriptor.graph_id}, {"send_node_id", 1}, {"recv_node_id", 2},
            {"src_rank", 0}, {"dst_rank", 1}, {"tag", tag}, {"start_ns", start},
            {"test_stop_ns", start + horizon_ns}, {"later_event_executed", false},
            {"wqe_starts", Json::array()}, {"wqe_completions", Json::array()},
            {"source_graph_completions_ns", Json::array()}, {"loaded_libraries", Json::array()},
            {"initial_tp_counts", {source_ctrl->GetTransportCountForTest(),
                                    target_ctrl->GetTransportCountForTest()}}};
        if (!app->TraceConnectWithoutContext("WqeTaskStartsNotify",
                ns3::MakeBoundCallback(RecordWqe, &report["wqe_starts"])) ||
            !app->TraceConnectWithoutContext("WqeTaskCompletesNotify",
                ns3::MakeBoundCallback(RecordWqe, &report["wqe_completions"])))
            throw std::runtime_error("Cannot connect actual UB WQE trace sources");
        moe_ub::InitializeAstraLogging();
        {
            moe_ub::UbWriteTransfer transfer(descriptor, app);
            uint64_t receive_callbacks = 0;
            moe_ub::UbNetworkAdapter sender(0, [&](uint64_t now) {
                report["source_graph_completions_ns"].push_back(now);
            }, &transfer);
            moe_ub::UbNetworkAdapter receiver(1, [](uint64_t) {}, &transfer);
            auto system = std::make_unique<AstraSim::Sys>(
                0, "write", "empty", "system.json", std::vector<AstraSim::AstraMemoryAPI*>{},
                &sender, std::vector<int>{1}, std::vector<int>{1}, 1.0, 1.0, false);
            auto* workload = system->workload;
            report["initial_send_slots"] = workload->hw_resource->num_in_flight_gpu_comm_ops;
            ns3::Simulator::Schedule(ns3::NanoSeconds(start), [&]() {
                auto register_receive = [&]() {
                    AstraSim::sim_request request{};
                    receiver.sim_recv(nullptr, bytes, AstraSim::UINT8, 0, tag, &request,
                                      ReceiveCallback, &receive_callbacks);
                    // The stack request dies here; it is not needed for any future event.
                };
                if (input.at("receive_first").get<bool>()) register_receive();
                workload->fire();
                if (!input.at("receive_first").get<bool>()) register_receive();
                report["send_slots_after_issue"] = workload->hw_resource->num_in_flight_gpu_comm_ops;
                report["submitted_before_return"] = transfer.SubmittedTasks();
            });
            // UB allocators run a permanent periodic deadlock check. This bounded test owns
            // its stop horizon; neither adapter nor rank completion stops the simulator.
            ns3::Simulator::Schedule(ns3::NanoSeconds(start + horizon_ns / 2), [&]() {
                report["later_event_executed"] = true;
            });
            ns3::Simulator::Stop(ns3::NanoSeconds(start + horizon_ns));
            ns3::Simulator::Run();
            report["simulation_end_ns"] = ns3::Simulator::Now().GetNanoSeconds();
            report["transaction_done_ns"] = transfer.TransactionDoneNs() ?
                Json(*transfer.TransactionDoneNs()) : Json(nullptr);
            report["send_done_ns"] = transfer.SendDoneNs() ? Json(*transfer.SendDoneNs()) : Json(nullptr);
            report["submitted_tasks"] = transfer.SubmittedTasks();
            report["send_callbacks"] = transfer.SendCallbacks();
            report["receive_callbacks"] = receive_callbacks;
            report["receiver_waiting"] = transfer.ReceiverWaiting();
            report["source_graph_finished"] = workload->is_finished;
            report["remaining_send_nodes"] = workload->et_feeder->hasNodesToIssue();
            report["final_send_slots"] = workload->hw_resource->num_in_flight_gpu_comm_ops;
            report["pending_astra_events"] = system->pending_events;
            report["pending_executor_tasks"] = app->GetTransferExecutor().GetPendingCount();
            report["csv_completed_tasks"] = ns3::UbTrafficGen::Get()->GetCompletedTaskCount();
            report["final_tp_counts"] = {source_ctrl->GetTransportCountForTest(),
                                         target_ctrl->GetTransportCountForTest()};
            report["jetty_resources_released"] = !report["wqe_starts"].empty();
            for (const auto& entry : report["wqe_starts"])
            {
                const auto jetty = entry.at("jetty").get<uint32_t>();
                if (source_ctrl->GetUbFunction()->IsJettyExists(jetty) ||
                    source_ctrl->GetUbTransaction()->HasJettyState(jetty))
                    report["jetty_resources_released"] = false;
            }
            dl_iterate_phdr(RecordLibrary, &report["loaded_libraries"]);
            // M1.2 ends with an unconsumed receive wait; no fabricated receive completion.
        }
        utils->Destroy();
        ns3::Simulator::Destroy();
        moe_ub::ShutdownAstraLogging();
        std::ofstream output("result.json");
        output << report.dump(2) << '\n';
        output.close();
        if (!output) throw std::runtime_error("Cannot write result.json");
        if (report["submitted_tasks"] != 1 || report["send_callbacks"] != 1 ||
            report["source_graph_finished"] != true || report["final_send_slots"] != 0 ||
            report["pending_executor_tasks"] != 0 || report["jetty_resources_released"] != true ||
            report["receive_callbacks"] != 0 || report["receiver_waiting"] != true)
            throw std::runtime_error("M1.2 write did not finish as specified; inspect result.json");
        std::cout << "Result: " << (run_dir / "result.json") << '\n';
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << error.what() << '\n';
        utils::UbUtils::Get()->Destroy();
        ns3::Simulator::Destroy();
        moe_ub::ShutdownAstraLogging();
        return 1;
    }
}
