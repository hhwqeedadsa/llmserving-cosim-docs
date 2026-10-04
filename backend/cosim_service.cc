#include "graph_resources.hh"
#include "observation_resources.hh"
#include "astra_logging.hh"
#include "backend_config.hh"
#include "graph_sequence.hh"
#include "execution_archive.hh"
#include "result_records.hh"
#include "identity_ranges.hh"
#include "port_observation.hh"
#include "ns3/simulator.h"
#include "ns3/ub-function.h"
#include "ns3/ub-switch.h"
#include "ns3/ub-transfer-executor.h"
#include "ns3/ub-utils.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <chrono>
#if defined(__GLIBC__)
#include <malloc.h>
#endif
#include <filesystem>
#include <fstream>
#include <iostream>
#include <link.h>
#include <memory>
#include <map>
#include <limits>
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
bool HostPreparationTrace()
{
#ifdef COSIM_SERVICE_RESOURCE_PROBE
    return true;
#else
    const auto* value = std::getenv("MOE_UB_HOST_PREPARATION_TRACE");
    return value && std::string(value) == "1";
#endif
}
void PreparationStage(const fs::path& run, const std::string& stage)
{
    if (!HostPreparationTrace()) return;
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    std::ofstream out(run / "preparation-phases.jsonl", std::ios::app);
    out << Json({{"stage", stage}, {"steady_seconds", std::chrono::duration<double>(now).count()},
                 {"simulation_ns", ns3::Simulator::Now().GetNanoSeconds()}}).dump() << '\n';
    out.close(); Require(bool(out), "Cannot write preparation phase");
}
struct Group
{
    Json config;
    std::vector<int> ranks;
    std::string graph, wave;
    std::set<int> completed;
};
std::vector<Group> LoadGroups(const fs::path& shared, Json& batch_limits)
{
    // Compatibility for the original direct cosim-service protocol fixtures.
    Json deployment = {{"schema_version", "moe-ub-service-deployment/1"}, {"groups", Json::array({
        {{"group_id", "ep0"}, {"rank_ids", {0u,1u,2u,3u}}, {"rank_to_ub_node", {0u,1u,2u,3u}},
         {"expert_placement", {0u,0u,1u,1u,2u,2u,3u,3u}}}})}};
    if (fs::exists(shared / "deployment.json")) deployment = ReadJson(shared / "deployment.json");
    const bool batched = deployment.at("schema_version") == "moe-ub-service-deployment/3";
    const bool full = deployment.at("schema_version") == "moe-ub-service-deployment/5";
    const bool composed = full || deployment.at("schema_version") == "moe-ub-service-deployment/4";
    Require(deployment.size() == (batched ? 3 : 2) && (composed || batched || deployment.at("schema_version") == "moe-ub-service-deployment/2" ||
            deployment.at("schema_version") == "moe-ub-service-deployment/1") &&
            deployment.at("groups").is_array() && !deployment.at("groups").empty(), "Invalid service deployment");
    const bool physical = deployment.at("schema_version") == "moe-ub-service-deployment/2" ||
        ((batched || composed) && deployment.at("groups").at(0).contains("deployment"));
    Require(!composed || deployment.at("groups").size() <= 64, "Service exceeds host group budget");
    if (batched)
    {
        batch_limits = deployment.at("batch_limits");
        moe_ub::ValidateBatchLimits(batch_limits);
        Require(deployment.at("groups").size() == 1, "Batched service currently requires one group");
    }
    std::vector<Group> groups;
    std::set<int> members;
    std::set<uint32_t> nodes;
    std::set<std::string> names;
    std::set<std::string> dies;
    std::map<std::string, std::string> chips;
    for (const auto& config : deployment.at("groups"))
    {
        const auto name = config.at("group_id").get<std::string>();
        Require(config.size() == (physical ? 5 : 4) + (composed ? 2 : 0) + (full ? 2 : 0) && std::regex_match(name, std::regex("[A-Za-z0-9_.-]+")) &&
                names.insert(name).second && config.at("rank_ids").is_array() &&
                !config.at("rank_ids").empty() && config.at("rank_to_ub_node").is_array() &&
                config.at("rank_ids").size() == config.at("rank_to_ub_node").size(),
                "Invalid EP group declaration");
        std::vector<int> ranks;
        int previous = -1;
        for (const auto& value : config.at("rank_ids"))
        {
            Require(value.is_number_unsigned() && value.get<uint64_t>() < INT32_MAX, "Invalid group rank");
            int rank = value;
            Require(rank > previous && members.insert(rank).second, "Repeated, overlapping or unordered group rank");
            previous = rank; ranks.push_back(rank);
        }
        for (const auto& value : config.at("rank_to_ub_node"))
            Require(value.is_number_unsigned() && value.get<uint64_t>() <= UINT32_MAX &&
                    nodes.insert(value.get<uint32_t>()).second, "Invalid or overlapping UB mapping");
        Require(config.at("expert_placement").is_array() && !config.at("expert_placement").empty(),
                "Empty expert placement");
        for (const auto& value : config.at("expert_placement"))
            Require(value.is_number_unsigned() && value.get<uint64_t>() < INT32_MAX &&
                    std::find(ranks.begin(), ranks.end(), value.get<int>()) != ranks.end(),
                    "Expert outside group");
        Require(config.contains("deployment") == physical, "Service deployment schema mismatch");
        if (physical)
        {
            moe_ub::ValidateDeployment(config.at("deployment"), config.at("rank_ids"));
            for (const auto& row : config.at("deployment").at("ranks"))
            {
                Require(dies.insert(row.at("die_id").get<std::string>()).second, "Repeated global die across groups");
                const auto [it, added] = chips.emplace(row.at("chip_id").get<std::string>(), row.at("server_id").get<std::string>());
                Require(added || it->second == row.at("server_id"), "Global chip spans multiple servers");
            }
        }
        if (composed)
        {
            moe_ub::ValidateBatchLimits(config.at("batch_limits"));
            const auto& layers = config.at("layer_configs");
            Require(layers.is_array() && !layers.empty() && layers.size() <= 128, "Invalid declared layer segment");
            Json model_ids = Json::array();
            if (full)
            {
                moe_ub::ValidateFullModelProfile(config.at("model_spec"), config.at("full_model_profile"), config.at("rank_ids"));
                for (const auto& row : config.at("model_spec").at("layers"))
                    if (row.at("kind") == "moe") model_ids.push_back(row.at("layer_id"));
                Require(model_ids.size() == layers.size(), "Full model layer coverage mismatch");
            }
            uint64_t index = 0;
            for (const auto& layer : layers)
            {
                Require(layer.at("schema_version") == "moe-ub-layer-fixture/3" &&
                        layer.at("layer_id").is_number_unsigned() && layer.at("layer_id").get<uint64_t>() <= INT32_MAX &&
                        layer.at("layer_id").get<uint64_t>() == (full ? model_ids[index].get<uint64_t>() : layers[0].at("layer_id").get<uint64_t>() + index) &&
                        layer.at("batch_limits") == config.at("batch_limits") && layer.at("group_id") == name &&
                        layer.at("rank_ids") == config.at("rank_ids") && layer.at("rank_to_ub_node") == config.at("rank_to_ub_node"),
                        "Invalid static layer declaration");
                ++index;
                Require(layer.contains("deployment") == physical &&
                        (!physical || layer.at("deployment") == config.at("deployment")), "Static layer locality mismatch");
            }
            Require(layers[0].at("expert_placement") == config.at("expert_placement"), "First layer placement mismatch");
        }
        groups.push_back({config, ranks, {}, {}, {}});
    }
    Require(!physical || composed || groups.size() == 1, "Physical service currently requires one group");
    std::sort(groups.begin(), groups.end(), [](const auto& a, const auto& b) {
        return a.config.at("group_id").template get<std::string>() < b.config.at("group_id").template get<std::string>();
    });
    return groups;
}
void WqeStart(Json* report, moe_ub::ResultRecords* records, const std::map<uint32_t, size_t>* indices,
              const moe_ub::BackendConfig* backend, uint32_t node, uint32_t jetty, uint32_t task)
{
    Json row = {{"node", node}, {"jetty", jetty}, {"task", task},
        {"time_ns", ns3::Simulator::Now().GetNanoSeconds()}};
    if (backend)
    {
        auto live = ns3::NodeList::GetNode(node)->GetObject<ns3::UbController>()->GetUbFunction()->GetJetty(jetty);
        Require(bool(live), "Missing live Jetty at WQE start");
        ns3::UintegerValue inflight;
        live->GetAttribute("UbJettyInflightMax", inflight);
        const auto& attributes = backend->Data().at("attributes");
        Require(live->GetTaSegmentBytes() == std::stoull(attributes.at("ns3::UbJetty::TaSegmentBytes").get<std::string>()) &&
                inflight.Get() == std::stoull(attributes.at("ns3::UbJetty::UbJettyInflightMax").get<std::string>()),
                "Live Jetty differs from backend configuration");
        row["ta_segment_bytes"] = live->GetTaSegmentBytes();
        row["jetty_inflight_max"] = inflight.Get();
    }
    records->Add("wqe_starts", row);
    auto active = report->at("active_wqes").at(indices->at(node)).get<unsigned>() + 1;
    (*report)["active_wqes"][indices->at(node)] = active;
    (*report)["peak_wqes"][indices->at(node)] = std::max(active, report->at("peak_wqes").at(indices->at(node)).get<unsigned>());
}
void WqeEnd(Json* report, moe_ub::ResultRecords* records, const std::map<uint32_t, size_t>* indices, uint32_t node, uint32_t jetty, uint32_t task)
{
    records->Add("wqe_completions", {{"node", node}, {"jetty", jetty}, {"task", task},
        {"time_ns", ns3::Simulator::Now().GetNanoSeconds()}});
    auto active = report->at("active_wqes").at(indices->at(node)).get<unsigned>();
    Require(active > 0, "Unexpected WQE completion");
    (*report)["active_wqes"][indices->at(node)] = active - 1;
}
void TargetWrite(moe_ub::ResultRecords* records, ns3::Ptr<const ns3::UbWqeSegment> segment)
{
    records->Add("target_writes", {{"task", segment->GetTaskId()}, {"src", segment->GetSrc()},
        {"dst", segment->GetDest()}, {"jetty", segment->GetOriginJettyNum()},
        {"sequence", segment->GetRequestTassn()}, {"bytes", segment->GetSize()},
        {"time_ns", ns3::Simulator::Now().GetNanoSeconds()}});
}
Json TransferResult(const moe_ub::UbWriteTransfer& transfer)
{
    const auto& d = transfer.Descriptor(); const auto& done = transfer.Completion();
    Require(!done.Active() && done.SendCallbacks() == 1 && done.ReceiveCallbacks() == 1 &&
            done.CommittedBytes() == d.bytes && !transfer.ObservingTarget() && !transfer.HasPendingEvents() &&
            transfer.SourceJettyRetired(), "Incomplete layer transfer " + std::to_string(d.transfer_id));
    return {{"transfer_id", d.transfer_id}, {"ub_task_id", d.task_id},
        {"src_rank", d.src_rank}, {"dst_rank", d.dst_rank},
        {"src_ub_node", d.SourceNode()}, {"dst_ub_node", d.DestinationNode()}, {"bytes", d.bytes},
        {"submitted_tasks", transfer.SubmittedTasks()}, {"send_callbacks", done.SendCallbacks()},
        {"receive_callbacks", done.ReceiveCallbacks()}, {"send_start_ns", *transfer.SendStartNs()},
        {"receive_start_ns", *transfer.ReceiveStartNs()}, {"data_ready_ns", *done.DataReadyNs()},
        {"transaction_done_ns", *done.TransactionDoneNs()}, {"send_done_ns", *done.SendDoneNs()},
        {"receive_done_ns", *done.ReceiveDoneNs()}, {"committed_bytes", done.CommittedBytes()},
        {"committed_segments", done.CommittedSegments()}, {"context_active", done.Active()}, {"graph_id", d.graph_id}};
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
        {
            if (const auto controller = (*node)->GetObject<ns3::UbController>())
                controllers_.push_back(controller);
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
        }
        // A controller's live TP map is independent of which of its ports is
        // being checked. Query it once per controller, including later TPs.
        for (const auto& controller : controllers_)
            for (const auto& [id, tp] : controller->GetTpnMap())
                if (!tp->IsEmpty() || tp->GetActiveSendSegmentCount() != 0 ||
                    tp->GetOutstandingUnackedSegmentCount() != 0) return false;
        return true;
    }
    uint64_t StartedPackets() const { return started_; }
    uint64_t ArrivedPackets() const { return arrived_; }
    void SetProgressCallback(std::function<void()> callback) { progress_ = std::move(callback); }
    Json Snapshot() const
    {
        Json queues = Json::array();
        for (const auto& port : ports_)
        {
            const auto buffered = port->GetNode()->GetObject<ns3::UbSwitch>()->GetQueueManager()
                ->GetTotalOutPortBufferUsed(port->GetIfIndex());
            if (port->IsBusy() || buffered || !port->GetUbQueue()->IsEmpty())
                queues.push_back({{"node", port->GetNode()->GetId()}, {"port", port->GetIfIndex()},
                    {"busy", port->IsBusy()}, {"buffered_bytes", buffered},
                    {"egress_bytes", port->GetUbQueue()->GetCurrentBytes()}});
        }
        return {{"idle", Idle()}, {"started_packets", started_}, {"arrived_packets", arrived_}, {"ports", queues}};
    }
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
        if (progress_) progress_();
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
        if (finished_ || progress_) ProbeAfter(completion);
        if (progress_) progress_();
    }
    void Arrived(ns3::Ptr<ns3::Packet>)
    {
        Require(++arrived_ <= started_, "Arrival without successful link transmission");
        if (finished_ || progress_) ns3::Simulator::ScheduleNow([this]() { Probe(); });
    }
    std::vector<ns3::Ptr<ns3::UbPort>> ports_;
    std::vector<ns3::Ptr<ns3::UbController>> controllers_;
    uint64_t started_ = 0, arrived_ = 0;
    ns3::Time last_port_completion_;
    std::function<void()> finished_;
    std::function<void()> progress_;
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
        if (argc == 2 && std::string(argv[1]) == "--backend-catalog")
        { std::cout << moe_ub::BackendCatalog().dump(2) << '\n'; return 0; }
        if (argc == 3 && std::string(argv[1]) == "--check-backend")
        { moe_ub::BackendConfig config(ReadJson(argv[2])); std::cout << config.Data().dump(2) << '\n'; return 0; }
        Require(argc == 8, "Usage: cosim-service RUN_ID SHARED_DIR SYSTEM.json UB_CASE OBSERVATION.json|- NEW_RUN_DIR MAX_NS");
        const int fd = dup(STDOUT_FILENO);
        Require(fd >= 0 && dup2(STDERR_FILENO, STDOUT_FILENO) >= 0, "Cannot reserve protocol stdout");
        protocol.reset(fdopen(fd, "w"));
        Require(bool(protocol), "Cannot open protocol stream");
        const std::string run_id = argv[1];
        Require(std::regex_match(run_id, std::regex("[A-Za-z0-9_.-]+")), "Invalid run identity");
        const auto shared = fs::canonical(argv[2]);
        const auto storage_policy = fs::exists(shared / "record-storage.json") ? ReadJson(shared / "record-storage.json") : Json(nullptr);
        if (!storage_policy.is_null()) moe_ub::ValidateRecordPolicy(storage_policy);
        Json batch_limits;
        auto groups = LoadGroups(shared, batch_limits);
        std::map<int, size_t> owner, rank_index;
        std::map<int, uint32_t> rank_node;
        std::map<uint32_t, size_t> node_index;
        for (size_t index = 0; index < groups.size(); ++index)
            for (size_t i = 0; i < groups[index].ranks.size(); ++i)
            {
                const int rank = groups[index].ranks[i];
                owner[rank] = index;
                rank_node[rank] = groups[index].config.at("rank_to_ub_node").at(i);
            }
        std::vector<int> ranks;
        std::vector<uint32_t> nodes;
        for (const auto& [rank, node] : rank_node)
        {
            rank_index[rank] = ranks.size(); node_index[node] = ranks.size();
            ranks.push_back(rank); nodes.push_back(node);
        }
        const size_t rank_count = ranks.size();
        const auto system_config = ReadJson(fs::absolute(argv[3]));
        Require(system_config.at("roofline-enabled") == 0 && system_config.at("replay-only") == 0 &&
                system_config.at("trace-enabled") == 1, "Service requires original ASTRA execution and trace");
        const auto network = fs::absolute(argv[4]);
        const auto backend_config = moe_ub::BackendConfig::Load(network);
        if (backend_config)
        {
            Json declarations = Json::array();
            for (const auto& group : groups) declarations.push_back(group.config);
            backend_config->CheckDeployment(declarations);
        }
        const auto run_dir = fs::absolute(argv[6]);
        const std::string bound = argv[7];
        Require(std::regex_match(bound, std::regex("[0-9]+")), "Invalid simulation bound");
        const auto max_ns = std::stoull(bound);
        Require(max_ns > 0 && max_ns <= INT64_MAX, "Invalid simulation bound");
        std::vector<moe_ub::ObservationConfig> observation_configs;
        bool multiple_ports = false;
        if (std::string(argv[5]) != "-")
        {
            const auto input = ReadJson(fs::absolute(argv[5]));
            const bool streamed=input.at("schema_version")=="moe-ub-port-observation-set/2";
            multiple_ports = streamed || input.at("schema_version") == "moe-ub-port-observation-set/1";
            if(streamed) moe_ub::ValidateObservationBudget(input.at("resource_budget"));
            if (multiple_ports)
            {
                Require(input.size() == (streamed ? 3 : 2) && input.at("ports").is_array() &&
                        !input.at("ports").empty() && input.at("ports").size() <= (streamed ? input.at("resource_budget").at("max_ports").get<uint64_t>() : 16),
                        "Observation set exceeds port budget (legacy: 1..16)");
                std::set<std::pair<uint32_t, uint32_t>> points;
                uint64_t windows=0;
                for (const auto& port : input.at("ports"))
                {
                    const auto config = moe_ub::ObservationConfig::Parse(port, max_ns);
                    Require(config.resource_budget.is_null()!=streamed,"Mixed observation storage schemas");
                    if(streamed)
                    {
                        Require(config.resource_budget==input.at("resource_budget"),"Observation budget changed across ports");
                        const auto count=(config.end_ns-config.start_ns-1)/config.period_ns+1;
                        Require(count<=config.resource_budget.at("max_total_windows").get<uint64_t>()-windows,"Observation total window budget exceeded");windows+=count;
                    }
                    Require(points.emplace(config.switch_id, config.port_id).second,
                            "Duplicate observation port");
                    observation_configs.push_back(config);
                }
            }
            else observation_configs.push_back(moe_ub::ObservationConfig::Parse(input, max_ns));
        }
        const std::vector<std::string> files = {"network_attribute.txt", "node.csv", "topology.csv",
            "routing_table.csv", "transport_channel.csv"};
        for (const auto& name : files) Require(fs::is_regular_file(network / name), "Missing network file " + name);
        // Preflight every bootstrap before constructing any Sys or network.
        for (int rank : ranks)
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
        if (backend_config) backend_config->Apply();
        auto* utils = utils::UbUtils::Get();
        if (!backend_config) utils->SetComponentsAttribute("network/network_attribute.txt");
        else std::ofstream("network/backend.json") << backend_config->Data().dump(2) << '\n';
        ns3::BooleanValue cc;
        ns3::GlobalValue::GetValueByName("UB_CC_ENABLED", cc);
        Require(!cc.Get(), "Service fixture requires CC disabled (no perpetual CC timers)");
        ns3::GlobalValue::Bind("UB_QUEUE_TRACE_ENABLE", ns3::BooleanValue(false));
        ns3::GlobalValue::Bind("UB_PARSE_TRACE_ENABLE", ns3::BooleanValue(false));
        ns3::Config::SetDefault("ns3::TpConnectionManager::RemoveUselessTp", ns3::BooleanValue(false));
        utils->CreateTraceDir();
        utils->CreateNode("network/node.csv");
        for (uint32_t node : nodes)
        {
            Require(node < ns3::NodeList::GetNNodes(), "Mapped UB endpoint does not exist");
            Require(ns3::NodeList::GetNode(node)->GetObject<ns3::UbController>() != nullptr,
                    "Mapped UB node is not a compute endpoint");
        }
        utils->CreateTopo("network/topology.csv");
        utils->AddRoutingTable("network/routing_table.csv");
        utils->CreateTp("network/transport_channel.csv");
        utils->TopoTraceConnect();
        Json report = {{"schema_version", "moe-ub-service-result/1"},
            {"calibration_status", "unvalidated_reference"}, {"sender_completion", "transaction_done"},
            {"random", {{"seed", ns3::RngSeedManager::GetSeed()}, {"run", ns3::RngSeedManager::GetRun()}}},
            {"rank_ids", ranks}, {"rank_to_ub_node", nodes},
            {"network_initializations", 1}, {"graph_completions", Json::array()},
            {"activation_order", Json::array()}, {"graph_loads", Json::array()},
            {"step_submissions", Json::array()}, {"boundary_resources", Json::array()},
            {"wqe_starts", Json::array()}, {"wqe_completions", Json::array()},
            {"target_writes", Json::array()}, {"transfers", Json::array()},
            {"active_wqes", std::vector<unsigned>(rank_count, 0)}, {"peak_wqes", std::vector<unsigned>(rank_count, 0)},
            {"initial_tp_counts", Json::array()}, {"rank_resources", Json::array()},
            {"loaded_libraries", Json::array()}};
        if (backend_config) report["backend_parameters_active"] = true;
        moe_ub::ResultRecords records(report, storage_policy);
        std::vector<ns3::Ptr<ns3::UbController>> controllers;
        std::vector<ns3::Ptr<ns3::UbApp>> apps;
        for (int rank : ranks)
        {
            auto node = ns3::NodeList::GetNode(rank_node.at(rank));
            auto controller = node->GetObject<ns3::UbController>();
            Require(controller != nullptr, "Rank needs a UB endpoint");
            controllers.push_back(controller);
            report["initial_tp_counts"].push_back(controller->GetTransportCountForTest());
            auto app = ns3::CreateObject<ns3::UbApp>();
            node->AddApplication(app); apps.push_back(app); utils->ClientTraceConnect(rank_node.at(rank));
            Require(app->TraceConnectWithoutContext("WqeTaskStartsNotify", ns3::MakeBoundCallback(WqeStart, &report, &records, &node_index,
                    backend_config ? &*backend_config : nullptr)) &&
                    app->TraceConnectWithoutContext("WqeTaskCompletesNotify", ns3::MakeBoundCallback(WqeEnd, &report, &records, &node_index)) &&
                    controller->GetUbTransaction()->TraceConnectWithoutContext("RemoteWriteCommitted",
                        ns3::MakeBoundCallback(TargetWrite, &records)), "Cannot attach execution traces");
        }
        if (backend_config) backend_config->WriteEffective(controllers, apps);
        moe_ub::InitializeAstraLogging();
        if (records.Bounded()) moe_ub::EnableAstraExecutionArchive(records.Store());
        {
            NetworkDrain drain;
            std::vector<std::unique_ptr<moe_ub::UbWriteTransfer>> transfers;
            std::vector<std::unique_ptr<moe_ub::LocalDelivery>> local_deliveries;
            std::vector<const moe_ub::UbWriteTransfer*> export_registry;
            std::shared_ptr<moe_ub::WriteProvenance> provenance;
            if (!observation_configs.empty()) provenance = std::make_shared<moe_ub::WriteProvenance>(controllers);
            std::vector<std::unique_ptr<moe_ub::PortObservation>> observations;
            for (const auto& config : observation_configs)
                observations.push_back(std::make_unique<moe_ub::PortObservation>(config,
                    provenance, multiple_ports,
                    config.resource_budget.is_null() ? fs::path{} : fs::path("ports")/(std::to_string(config.switch_id)+"-"+std::to_string(config.port_id)), records.Store()));
            std::vector<std::unique_ptr<moe_ub::UbNetworkAdapter>> adapters;
            std::vector<std::unique_ptr<AstraSim::Sys>> systems;
            Json resource_budget=nullptr, resource_used={{"graphs",0u},{"transfers",0u},{"nodes",0u}};
            for (const auto& group : groups)
                if (group.config.contains("layer_configs"))
                {
                    const auto b=moe_ub::resources::Budget(group.config.at("layer_configs")[0]);
                    if (!b.is_null())
                    {
                        Require(resource_budget.is_null() || resource_budget==b,"Groups have different resource budgets");
                        resource_budget=b;
                    }
                }
            if (!resource_budget.is_null())
                for (const auto& group : groups)
                {
                    Require(group.config.contains("model_spec"),"Budgeted service requires complete model groups");
                    for (const auto& layer : group.config.at("layer_configs"))
                        Require(moe_ub::resources::Budget(layer)==resource_budget,"Missing or changed group resource budget");
                }
            for (const auto& config : observation_configs)
                Require(config.resource_budget.is_null() || !resource_budget.is_null(),
                        "Streaming observation requires complete model graph/run resource budget");
            std::vector<moe_ub::LayerPlan> plans;
            std::set<std::string> graphs, waves, seen_operations, kv_graphs;
            moe_ub::IdentityRanges transfer_ids, tasks, tags;
            moe_ub::ExecutionArchive execution_archive;
            std::set<std::string> provenance_retirement;
            std::set<int> transfer_ranks;
            std::map<std::string, uint64_t> endpoint_offsets;
            uint64_t next_endpoint_offset = 0, expected_completions = 0, submitted_transfers = 0;
            uint64_t peak_plans = 0, peak_transfers = 0, peak_local = 0, retired_graphs = 0;
            Json retirement_boundaries = Json::array();
            const bool record_truth = std::any_of(observation_configs.begin(), observation_configs.end(),
                [](const auto& config) { return config.provenance; });
            auto retire_provenance = [&]() {
                if (!records.Bounded() || provenance_retirement.empty() || !drain.Idle()) return;
                if (provenance) provenance->RetireCompletedGraphs(provenance_retirement);
                provenance_retirement.clear();
            };
            auto retire_graph = [&](const std::string& graph) {
                if (!records.Bounded()) return;
                execution_archive.Read(records.Store()->Directory(), moe_ub::DrainAstraExecutionArchive());
                auto found = std::find_if(plans.begin(), plans.end(), [&](const auto& p) { return p.data.at("graph_id") == graph; });
                Require(found != plans.end(), "Completed graph has no retained plan");
                auto timing = execution_archive.Take(*found);
                for (const auto& [key, node] : found->nodes)
                {
                    const auto& t = timing.at(key);
                    records.Add("node_execution", {{"rank_id", key.first}, {"node_id", key.second}, {"kind", node.kind},
                        {"start_ns", t.start_ns}, {"end_ns", t.end_ns}, {"graph_id", graph}}, graph);
                }
                auto endpoints = moe_ub::LayerEndpointOperations(*found, timing);
                auto operations = moe_ub::LayerOperationRegistry(*found);
                const auto offset = endpoint_offsets.at(graph);
                for (auto& row : endpoints)
                {
                    row["local_record_id"] = row.at("local_record_id").get<uint64_t>() + offset;
                    records.Add("endpoint_ops", row, graph);
                }
                if (record_truth) for (auto& op : operations)
                {
                    for (auto& row : op["endpoint_records"]) row["local_record_id"] = row.at("local_record_id").get<uint64_t>() + offset;
                    records.Add("operation_registry", op, graph);
                }
                for (const auto& transfer : transfers) if (transfer->Descriptor().graph_id == graph)
                {
                    records.Add("transfers", TransferResult(*transfer), graph);
                    if (record_truth) records.Add("transfer_registry", moe_ub::TransferRegistryRecord(*transfer), graph);
                }
                for (const auto& delivery : local_deliveries) if (delivery->Descriptor().graph_id == graph)
                {
                    Require(!delivery->Active() && !delivery->HasPendingEvents(), "Live delivery at graph retirement");
                    records.Add("local_deliveries", delivery->Result(), graph);
                }
                records.FinishGraph(graph);
                for (auto& adapter : adapters) adapter->RetireGraph(graph);
                std::erase_if(transfers, [&](const auto& t) { return t->Descriptor().graph_id == graph; });
                std::erase_if(local_deliveries, [&](const auto& d) { return d->Descriptor().graph_id == graph; });
                plans.erase(found); endpoint_offsets.erase(graph); ++retired_graphs;
                provenance_retirement.insert(graph); retire_provenance();
                retirement_boundaries.push_back({{"graph_id", graph}, {"time_ns", ns3::Simulator::Now().GetNanoSeconds()},
                    {"resident_graphs", plans.size()}, {"resident_transfers", transfers.size()}, {"resident_local", local_deliveries.size()},
                    {"pending_provenance_graphs", provenance_retirement.size()}, {"same_network_objects", true}});
            };
            std::map<size_t, uint64_t> completed_groups;
            ns3::EventId wakeup;
            bool decision_pending = false;
            unsigned bootstrap_done = 0, send_slots = 0;
            uint64_t decision_id = 0;
            bool bootstrap = true, service_finished = false;
            std::string stage = "bootstrap";
            Json last_commit;
            std::function<void()> decide;
            auto request_decision = [&]() {
                if (!decision_pending)
                {
                    decision_pending = true;
                    ns3::Simulator::ScheduleNow([&]() { decide(); });
                }
            };
            for (int rank : ranks)
            {
                adapters.push_back(std::make_unique<moe_ub::UbNetworkAdapter>(rank, [&, rank](uint64_t now) {
                    if (bootstrap) { ++bootstrap_done; return; }
                    auto& group = groups[owner[rank]];
                    Require(!group.graph.empty() && group.completed.insert(rank).second &&
                            systems.at(rank_index.at(rank))->workload->graph_id() == group.graph, "Unexpected rank completion");
                    report["graph_completions"].push_back({{"rank_id", rank}, {"time_ns", now},
                        {"graph_id", group.graph}});
                    if (group.completed.size() == group.ranks.size())
                    {
                        // A KV graph reserves both EP groups until all members
                        // finish. Never expose a receive-only group as available.
                        size_t first = groups.size();
                        bool all = true;
                        for (size_t i = 0; i < groups.size(); ++i)
                            if (groups[i].graph == group.graph)
                            {
                                first = std::min(first, i);
                                all = all && groups[i].completed.size() == groups[i].ranks.size();
                            }
                        if (all)
                        {
                            Require(completed_groups.emplace(first, now).second, "Duplicate graph completion");
                            request_decision();
                        }
                    }
                }));
                systems.push_back(std::make_unique<AstraSim::Sys>(rank, (shared / "bootstrap/layer").string(),
                    "empty", "system.json", std::vector<AstraSim::AstraMemoryAPI*>{}, adapters.back().get(),
                    std::vector<int>{ranks.back()+1}, std::vector<int>{1}, 1.0, 1.0, false));
                systems.back()->workload->bind_graph("_bootstrap");
            }
            for (auto& sys : systems) sys->workload->activate_graph();
            Require(bootstrap_done == unsigned(rank_count) && ns3::Simulator::Now().GetNanoSeconds() == 0 &&
                    records.Count("wqe_starts") == 0 && drain.StartedPackets() == 0,
                    "Bootstrap generated work or advanced time");
            bootstrap = false;
            report["bootstrap"] = {{"finished_ranks", bootstrap_done}, {"time_ns", 0}, {"business_nodes", 0}};
            report["loaded_ranks_before_activation"] = systems.size();
            // This is a failure bound, never a successful completion timestamp.
            auto watchdog = ns3::Simulator::Schedule(ns3::NanoSeconds(max_ns), []() {
                throw std::runtime_error("Service exceeded max_simulation_ns");
            });
            // Check only after actual execution progress, at the same ns time.
            // Native recurring diagnostics are not business progress. Outstanding
            // network state remains protected by the explicit failure bound.
            bool probe_pending = false;
            auto progress = [&]() {
                if (probe_pending) return;
                probe_pending = true;
                ns3::Simulator::ScheduleNow([&]() { ns3::Simulator::ScheduleNow([&]() {
                    probe_pending = false;
                    retire_provenance();
                    if (service_finished || decision_pending || wakeup.IsPending()) return;
                    for (const auto& adapter : adapters)
                        if (adapter->ScheduledEvents() != adapter->ExecutedCallbacks()) return;
                    // UB may have accepted a task whose deferred WQE work has
                    // not reached a TP or port yet. It is still business work.
                    for (const auto& app : apps)
                        if (app->GetTransferExecutor().GetPendingCount() != 0) return;
                    // These predicates only read state. Pending compute/WQE
                    // work already disproves deadlock; avoid a full idle fabric
                    // scan (and live TP-map copies) in that common case.
                    if (!drain.Idle()) return;
                    for (const auto& transfer : transfers) if (transfer->HasPendingEvents()) return;
                    for (const auto& delivery : local_deliveries) if (delivery->HasPendingEvents()) return;
                    if (std::any_of(groups.begin(), groups.end(), [](const auto& g) { return !g.graph.empty(); }))
                        throw std::runtime_error("Service deadlock: active graph waits without a business event or future wakeup; see failure.json");
                }); });
            };
            for (auto& adapter : adapters) adapter->SetProgressCallback(progress);
            drain.SetProgressCallback(progress);
            decide = [&]() {
                stage = "waiting_for_commit";
                decision_pending = false;
                const uint64_t now = ns3::Simulator::Now().GetNanoSeconds();
                Json completed = Json::array(), available = Json::array();
                // groups are ordered by group_id; one completion per group's wave.
                for (const auto& [index, finished] : completed_groups)
                {
                    auto& group = groups[index];
                    Require(finished == now, "Deferred group completion changed absolute time");
                    for (const auto& participant : groups)
                        if (participant.graph == group.graph)
                            for (int rank : participant.ranks)
                                Require(systems.at(rank_index.at(rank))->workload->can_load_graph() &&
                                        apps.at(rank_index.at(rank))->GetTransferExecutor().GetPendingCount() == 0,
                                        "Graph completion before participant work retired");
                    for (const auto& transfer : transfers)
                        if (transfer->Descriptor().graph_id == group.graph)
                            Require(!transfer->Completion().Active() && !transfer->ObservingTarget() && !transfer->HasPendingEvents(),
                                    "Completed group still has an active transfer");
                    for (const auto& delivery : local_deliveries)
                        if (delivery->Descriptor().graph_id == group.graph)
                            Require(!delivery->Active() && !delivery->HasPendingEvents(), "Completed group retains local delivery");
                    const auto done_graph = group.graph;
                    retire_graph(done_graph);
                    completed.push_back({{"kind", kv_graphs.count(done_graph) ? "KV_DONE" : "WAVE_DONE"}, {"graph_id", done_graph},
                        {"wave_id", group.wave}, {"finished_ns", finished}});
                    for (auto& participant : groups)
                        if (participant.graph == done_graph)
                        { participant.graph.clear(); participant.wave.clear(); participant.completed.clear(); }
                }
                completed_groups.clear();
                for (int rank : ranks)
                {
                    Require(controllers.at(rank_index.at(rank)) == ns3::NodeList::GetNode(rank_node.at(rank))->GetObject<ns3::UbController>() &&
                            apps.at(rank_index.at(rank))->GetNode() == ns3::NodeList::GetNode(rank_node.at(rank)), "Network object changed");
                    if (!groups[owner[rank]].graph.empty()) continue;
                    Require(systems.at(rank_index.at(rank))->workload->can_load_graph() &&
                            apps.at(rank_index.at(rank))->GetTransferExecutor().GetPendingCount() == 0,
                            "Idle group has pending work");
                    available.push_back(rank);
                    report["boundary_resources"].push_back({{"rank_id", rank}, {"time_ns", now},
                        {"pending_events", systems.at(rank_index.at(rank))->pending_events}, {"pending_tasks", 0},
                        {"same_network_objects", true}});
                }
                const Json message = {{"schema_version", "cosim/1"}, {"type", "DECIDE"}, {"run_id", run_id},
                    {"decision_id", decision_id}, {"now_ns", now}, {"completed", completed},
                    {"available_rank_ids", available}};
                Require(std::fprintf(protocol.get(), "%s\n", message.dump().c_str()) > 0 &&
                        std::fflush(protocol.get()) == 0, "Cannot send DECIDE");
                std::string line;
                Require(bool(std::getline(std::cin, line)), "Frontend EOF while waiting for COMMIT");
                const auto commit = Json::parse(line);
                last_commit = commit;
                stage = "validating_commit";
                std::set<std::string> keys;
                for (const auto& [key, value] : commit.items()) keys.insert(key);
                Require(keys == std::set<std::string>{"schema_version", "type", "run_id", "decision_id", "now_ns",
                    "submissions", "next_wakeup_ns", "input_exhausted", "service_finished"}, "Invalid COMMIT fields");
                Require(commit.at("schema_version") == "cosim/1" && commit.at("type") == "COMMIT" &&
                    commit.at("run_id") == run_id && commit.at("decision_id").is_number_unsigned() &&
                    commit.at("decision_id") == decision_id && commit.at("now_ns").is_number_unsigned() &&
                    commit.at("now_ns") == now, "Mismatched COMMIT envelope, decision or absolute time");
                Require(commit.at("input_exhausted").is_boolean() && commit.at("service_finished").is_boolean() &&
                        commit.at("submissions").is_array(), "Invalid service flags/submissions");
                const auto& next = commit.at("next_wakeup_ns");
                Require(next.is_null() || (next.is_number_unsigned() && next.get<uint64_t>() > now &&
                        next.get<uint64_t>() < uint64_t(INT64_MAX)), "Wakeup must be a future absolute ns time");
                Require(!commit.at("input_exhausted").get<bool>() || next.is_null(), "Exhausted input has future wakeup");
                Require(commit.at("input_exhausted").get<bool>() || !next.is_null(), "Future input needs its next arrival");
                // Each COMMIT replaces the previous external wakeup; graph completion
                // never loses or duplicates a pending arrival.
                ns3::Simulator::Remove(wakeup);
                if (!next.is_null()) wakeup = ns3::Simulator::Schedule(
                    ns3::NanoSeconds(next.get<uint64_t>() - now), request_decision);
                ++decision_id;
                service_finished = commit.at("service_finished").get<bool>();
                if (service_finished)
                {
                    Require(commit.at("submissions").empty() && next.is_null() && commit.at("input_exhausted") == true &&
                            std::all_of(groups.begin(), groups.end(), [](const auto& g) { return g.graph.empty(); }),
                            "Finished service still has input, submissions or active groups");
                    report["service_finished_ns"] = now;
                    drain.Finish([&]() {
                        ns3::Simulator::Remove(watchdog);
                        ns3::Simulator::Stop();
                    });
                    return;
                }
                Require(commit.at("submissions").size() <= groups.size(), "Too many group submissions");
                for (const auto& submission : commit.at("submissions"))
                {
                    stage = "validating_submission";
                    keys.clear();
                    for (const auto& [key, value] : submission.items()) keys.insert(key);
                    Require(keys == std::set<std::string>{"graph_id", "wave_id", "rank_ids", "workload_prefix", "semantic_manifest"},
                            "Invalid submission fields");
                    const auto& selected_ranks = submission.at("rank_ids");
                    Require(selected_ranks.is_array(), "Invalid submission rank array");
                    for (const auto& rank : selected_ranks) Require(rank.is_number_unsigned(), "Invalid rank type");
                    std::vector<size_t> selected;
                    std::vector<int> covered;
                    for (size_t i = 0; i < groups.size(); ++i)
                        if (std::all_of(groups[i].ranks.begin(), groups[i].ranks.end(), [&](int rank) {
                            return std::find(selected_ranks.begin(), selected_ranks.end(), rank) != selected_ranks.end();
                        }))
                        {
                            selected.push_back(i);
                            covered.insert(covered.end(), groups[i].ranks.begin(), groups[i].ranks.end());
                        }
                    std::sort(covered.begin(), covered.end());
                    Require(!selected.empty() && selected.size() <= 2 && selected_ranks == Json(covered),
                            "Unknown or partial EP group submission");
                    for (auto i : selected) Require(groups[i].graph.empty(), "Unknown or busy EP group submission");
                    auto& group = groups[selected.front()];
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
                    if (!resource_budget.is_null())
                        Require(fs::file_size(directory / "plan.json") <= resource_budget.at("max_plan_bytes").get<uint64_t>(),"Plan exceeds declared max_plan_bytes");
                    PreparationStage(run_dir, "load_and_prepare_begin");
                    moe_ub::LoadedGraph loaded(directory);
                    PreparationStage(run_dir, "load_and_prepare_end");
                    const auto& plan = loaded.Plan();
                    Require(moe_ub::resources::Budget(plan.data.at("input"))==resource_budget,"Plan/run resource budget mismatch");
                    if (!resource_budget.is_null())
                        resource_used=moe_ub::resources::AddRun(resource_used,
                            plan.validated_usage.is_null() ? moe_ub::resources::Actual(plan.data) : plan.validated_usage,resource_budget);
                    Require((plan.data.at("schema_version") == "moe-ub-execution-plan/3") == !batch_limits.is_null(),
                            "Plan/service batching schema mismatch");
                    if (!batch_limits.is_null())
                        Require(plan.data.at("batch_limits") == batch_limits, "Batch limits changed during run");
                    const bool composed = group.config.contains("layer_configs");
                    const bool full = group.config.contains("model_spec");
                    Require((plan.data.at("schema_version") == (full ? "moe-ub-full-model-plan/1" : "moe-ub-multilayer-plan/2")) == composed,
                            "Plan/service layer-segment schema mismatch");
                    if (full)
                        Require(plan.data.at("model_spec") == group.config.at("model_spec") &&
                                plan.data.at("full_model_profile") == group.config.at("full_model_profile"), "Full model declaration changed during run");
                    if (composed)
                    {
                        Require(selected.size() == 1 && plan.data.at("batch_limits") == group.config.at("batch_limits") &&
                                plan.data.at("layers").size() == group.config.at("layer_configs").size(),
                                "Layer segment or batch limits changed during run");
                        for (size_t i = 0; i < plan.data.at("layers").size(); ++i)
                        {
                            Json current = plan.data.at("layers")[i].at("input");
                            for (const auto* field : {"graph_id", "wave_id", "ready_ns", "tokens"}) current.erase(field);
                            Require(current == group.config.at("layer_configs")[i], "Static layer configuration changed during run");
                        }
                    }
                    if (backend_config) backend_config->CheckPlan(plan.data);
                    Require(plan.data.at("graph_id") == graph && plan.data.at("wave_id") == wave &&
                            plan.data.at("input").at("graph_id") == graph &&
                            plan.data.at("input").at("wave_id") == wave &&
                            plan.data.at("input").at("ready_ns").is_number_unsigned() &&
                            plan.data.at("ready_ns") == now && plan.data.at("input").at("ready_ns") == now,
                            "Submission identity/time differs from plan");
                    Require(plan.data.at("rank_ids") == selected_ranks, "Plan members differ from submission");
                    Json selected_nodes = Json::array();
                    for (int rank : covered) selected_nodes.push_back(rank_node.at(rank));
                    Require(plan.data.at("rank_to_ub_node") == selected_nodes,
                            "Service plan mapping differs from current deployment");
                    Require(plan.data.contains("deployment") == group.config.contains("deployment"), "Plan/deployment locality schema mismatch");
                    if (group.config.contains("deployment"))
                        Require(plan.data.at("deployment") == group.config.at("deployment"), "Physical deployment changed during run");
                    const bool kv = plan.data.at("communication_template") == "kv_direct_v1";
                    if (kv)
                    {
                        Require(selected.size() == 2, "KV handoff must reserve both complete EP groups");
                        for (const auto* side : {"source_group", "destination_group"})
                        {
                            const auto& declared = plan.data.at("input").at(side);
                            Require(std::any_of(groups.begin(), groups.end(), [&](const auto& g) {
                                return declared.at("group_id") == g.config.at("group_id") &&
                                    declared.at("rank_ids") == g.config.at("rank_ids");
                            }), "KV group differs from deployment");
                        }
                    }
                    else
                    {
                        Require(selected.size() == 1 &&
                            plan.data.at("input").at("group_id") == group.config.at("group_id") &&
                            (composed || plan.data.at("input").at("expert_placement") == group.config.at("expert_placement")),
                            "Plan group/placement differs from deployment");
                        for (const auto& op : plan.data.at("operations"))
                            Require(op.at("group_id") == group.config.at("group_id"), "Operation group differs from deployment");
                    }
                    const auto slots = plan.data.at("send_slots_per_rank").get<unsigned>();
                    Require(send_slots == 0 || slots == send_slots, "Cannot change send slots during a run");
                    for (const auto& op : plan.data.at("operations"))
                        Require(op.at("wave_id") == wave &&
                                !seen_operations.count(op.at("operation_id").get<std::string>()),
                                "Operation has wrong wave or reused identity");
                    for (const auto& d : plan.descriptors)
                        Require(!transfer_ids.count(d.transfer_id) && !tasks.count(d.task_id) && !tags.count(d.tag),
                                "Reused transfer, UB task or comm_tag");
                    for (const auto& d : plan.local_descriptors)
                        Require(!transfer_ids.count(d.transfer_id) && !tags.count(d.tag), "Reused local transfer/tag");
                    records.BeginGraph(graph);
                    if (records.Bounded())
                    {
                        execution_archive.Register(plan);
                        endpoint_offsets[graph] = next_endpoint_offset;
                        next_endpoint_offset += plan.data.contains("layers") ? 2 * plan.data.at("layers").size() : 2;
                        const auto usage = plan.validated_usage.is_null() ? moe_ub::resources::Actual(plan.data) : plan.validated_usage;
                        for (const auto& transfer : plan.data.at("transfers")) records.Add("planned_transfers", transfer, graph);
                        records.SealPlannedTransfers(graph, plan.data.at("transfers").size());
                        records.Add("plan_summary", {{"graph_id", graph}, {"operations", plan.data.at("operations")},
                            {"resource_budget", resource_budget}, {"resource_usage", usage}}, graph);
                        loaded.DiscardArchivedTransfers();
#if defined(__GLIBC__)
                        // Return the no-longer-needed JSON allocation pages
                        // before constructing millions of execution objects.
                        malloc_trim(0);
#endif
                    }
                    PreparationStage(run_dir, "archived_transfer_json_released");
                    std::vector<std::unique_ptr<moe_ub::UbWriteTransfer>> prepared;
                    std::vector<moe_ub::UbWriteTransfer*> pointers;
                    for (const auto& d : plan.descriptors)
                    {
                        prepared.push_back(std::make_unique<moe_ub::UbWriteTransfer>(d, apps.at(rank_index.at(d.src_rank))));
                        pointers.push_back(prepared.back().get());
                    }
                    std::vector<std::unique_ptr<moe_ub::LocalDelivery>> prepared_local;
                    std::vector<moe_ub::LocalDelivery*> local_pointers;
                    for (const auto& d : plan.local_descriptors)
                    {
                        prepared_local.push_back(std::make_unique<moe_ub::LocalDelivery>(d));
                        local_pointers.push_back(prepared_local.back().get());
                    }
                    loaded.Install(systems, adapters, pointers, local_pointers);
                    PreparationStage(run_dir, "graph_and_transfers_installed");
                    if (provenance) provenance->Register(plan.descriptors);
                    PreparationStage(run_dir, "provenance_registered");
                    expected_completions += plan.data.at("rank_ids").size();
                    submitted_transfers += plan.descriptors.size();
                    if (!send_slots)
                        for (auto& sys : systems) sys->workload->hw_resource->configure_p2p_send_slots(slots);
                    send_slots = slots;
                    graphs.insert(graph); waves.insert(wave);
                    for (const auto& op : plan.data.at("operations")) seen_operations.insert(op.at("operation_id").get<std::string>());
                    for (const auto& d : plan.descriptors)
                    {
                        transfer_ids.insert(d.transfer_id); tasks.insert(d.task_id); tags.insert(d.tag);
                        transfer_ranks.insert(d.src_rank); transfer_ranks.insert(d.dst_rank);
                    }
                    for (auto& transfer : prepared)
                    {
                        if (!records.Bounded()) export_registry.push_back(transfer.get());
                        transfers.push_back(std::move(transfer));
                    }
                    for (const auto& d : plan.local_descriptors)
                    { transfer_ids.insert(d.transfer_id); tags.insert(d.tag); }
                    for (auto& delivery : prepared_local) local_deliveries.push_back(std::move(delivery));
                    plans.push_back(loaded.ReleasePlan());
#if defined(__GLIBC__)
                    if (records.Bounded()) malloc_trim(0);
#endif
                    peak_plans = std::max(peak_plans, uint64_t(plans.size()));
                    peak_transfers = std::max(peak_transfers, uint64_t(transfers.size()));
                    peak_local = std::max(peak_local, uint64_t(local_deliveries.size()));
                    if (kv) kv_graphs.insert(graph);
                    for (auto i : selected)
                    { groups[i].graph = graph; groups[i].wave = wave; groups[i].completed.clear(); }
                    stage = "activating_graph:" + graph;
                    report["step_submissions"].push_back({{"graph_id", graph}, {"wave_id", wave}, {"time_ns", now}});
                    for (int rank : selected_ranks)
                        report["graph_loads"].push_back({{"graph_id", graph}, {"rank_id", rank}, {"time_ns", now}});
                    PreparationStage(run_dir, "planned_records_ready");
                    if (HostPreparationTrace())
                    {
                    std::ofstream probe(run_dir / "resource-preparation.json");
                    probe << Json({{"schema_version", "moe-ub-service-preparation-probe/1"},
                        {"preparation_completed", true}, {"business_activated", false},
                        {"normal_service_exit_verified", false}, {"graph_id", graph},
                        {"ranks", selected_ranks.size()}, {"fabric_transfers", transfers.size()},
                        {"local_transfers", local_deliveries.size()}, {"observed_ports", observations.size()},
                        {"validated_nodes", plans.back().nodes.size()},
                        {"timing_nodes", execution_archive.ResidentNodes()},
                        {"archived_transfer_data", plans.back().archived_transfer_data},
                        {"retained_transfer_descriptors", plans.back().descriptors.size()},
                        {"simulation_ns", now}}).dump(2) << '\n';
                    probe.close(); Require(bool(probe), "Cannot write preparation probe");
                    }
#ifdef COSIM_SERVICE_RESOURCE_PROBE
                    // No graph is activated. OS process teardown releases this
                    // isolated resource probe; it is not normal service cleanup.
                    std::_Exit(0);
#endif
                    for (int rank : selected_ranks)
                    {
                        report["activation_order"].push_back({{"graph_id", graph}, {"rank_id", rank}, {"time_ns", now}});
#ifdef COSIM_SERVICE_DEADLOCK_TEST
                        // Deliberately leave one participant unactivated, only in
                        // the separate failure-test target after full validation.
                        if (plans.size() == 1 && rank == group.ranks.back()) continue;
#endif
                        systems.at(rank_index.at(rank))->workload->activate_graph();
                    }
                }
                Require(!next.is_null() || std::any_of(groups.begin(), groups.end(),
                    [](const auto& g) { return !g.graph.empty(); }),
                    "Service deadlock: unfinished frontend, no active group and no future wakeup");
                stage = "executing";
                progress();
            };
            // Initial decision is an event after every Sys, adapter and UB object exists.
            request_decision();
            try
            {
                ns3::Simulator::Run();
                Require(service_finished, "Event queue ended with unfinished service");
            }
            catch (const std::exception& error)
            {
                Json failure = {{"normal_exit", false}, {"error", error.what()}, {"stage", stage},
                    {"now_ns", ns3::Simulator::Now().GetNanoSeconds()}, {"decision_id", decision_id},
                    {"last_commit", last_commit}, {"future_wakeup_pending", wakeup.IsPending()},
                    {"groups", Json::array()}, {"ranks", Json::array()}, {"transfers", Json::array()},
                    {"network", drain.Snapshot()}};
                for (const auto& group : groups)
                    failure["groups"].push_back({{"group_id", group.config.at("group_id")}, {"graph_id", group.graph},
                        {"wave_id", group.wave}, {"completed_ranks", group.completed}});
                for (int rank : ranks)
                {
                    const auto* workload = systems.at(rank_index.at(rank))->workload;
                    failure["ranks"].push_back({{"rank_id", rank}, {"graph_id", workload->graph_id()},
                        {"finished", workload->is_finished}, {"remaining_nodes", workload->et_feeder->hasNodesToIssue()},
                        {"compute_slots", workload->hw_resource->num_in_flight_gpu_comp_ops},
                        {"send_slots", workload->hw_resource->num_in_flight_gpu_comm_ops},
                        {"pending_events", systems.at(rank_index.at(rank))->pending_events},
                        {"pending_adapter_events", adapters.at(rank_index.at(rank))->ScheduledEvents() - adapters.at(rank_index.at(rank))->ExecutedCallbacks()},
                        {"pending_tasks", apps.at(rank_index.at(rank))->GetTransferExecutor().GetPendingCount()}});
                }
                for (const auto& transfer : transfers)
                {
                    if (!transfer->Completion().Active()) continue;
                    const auto& d = transfer->Descriptor();
                    failure["transfers"].push_back({{"graph_id", d.graph_id}, {"transfer_id", d.transfer_id},
                        {"src_rank", d.src_rank}, {"dst_rank", d.dst_rank},
                    {"src_ub_node", d.SourceNode()}, {"dst_ub_node", d.DestinationNode()}, {"send_node_id", d.send_node_id},
                        {"recv_node_id", d.recv_node_id}, {"send_registered", bool(transfer->SendStartNs())},
                        {"receive_registered", bool(transfer->ReceiveStartNs())},
                        {"send_callbacks", transfer->SendCallbacks()}, {"receive_callbacks", transfer->Completion().ReceiveCallbacks()},
                        {"pending_callback_or_submit", transfer->HasPendingEvents()}});
                }
                failure["local_deliveries"] = Json::array();
                for (const auto& delivery : local_deliveries)
                    if (delivery->Active()) failure["local_deliveries"].push_back(delivery->Result());
                std::ofstream("failure.json") << failure.dump(2) << '\n';
                moe_ub::FlushAstraLogging();
                throw;
            }
            report["local_deliveries"] = Json::array();
            for (const auto& delivery : local_deliveries)
            {
                Require(!delivery->Active() && !delivery->HasPendingEvents(), "Incomplete local delivery");
                report["local_deliveries"].push_back(delivery->Result());
            }
            Require(drain.Idle(), "Service stopped with link data or queued packets");
            report["network_drain"] = {{"started_packets", drain.StartedPackets()},
                {"arrived_packets", drain.ArrivedPackets()}, {"idle", true}};
            report["simulation_end_ns"] = ns3::Simulator::Now().GetNanoSeconds();
            report["decisions"] = decision_id;
            report["normal_exit"] = true;
            report["csv_completed_tasks"] = ns3::UbTrafficGen::Get()->GetCompletedTaskCount();
            for (const auto& transfer : transfers)
            {
                report["transfers"].push_back(TransferResult(*transfer));
            }
            for (int rank : ranks)
            {
                const auto* workload = systems.at(rank_index.at(rank))->workload;
                const auto* hardware = workload->hw_resource;
                Json state = {{"rank_id", rank}, {"finished", workload->is_finished},
                    {"remaining_nodes", workload->et_feeder->hasNodesToIssue()},
                    {"cpu_slots", hardware->num_in_flight_cpu_ops}, {"compute_slots", hardware->num_in_flight_gpu_comp_ops},
                    {"send_slots", hardware->num_in_flight_gpu_comm_ops}, {"pending_events", systems.at(rank_index.at(rank))->pending_events},
                    {"pending_tasks", apps.at(rank_index.at(rank))->GetTransferExecutor().GetPendingCount()},
                    {"tp_count", controllers.at(rank_index.at(rank))->GetTransportCountForTest()}};
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
                auto ctrl = controllers.at(node_index.at(event.at("node").get<uint32_t>()));
                auto jetty = event.at("jetty").get<uint32_t>();
                Require(!ctrl->GetUbFunction()->IsJettyExists(jetty) && !ctrl->GetUbTransaction()->HasJettyState(jetty), "Leaked source Jetty resources");
            }
            Require(report["graph_completions"].size() == expected_completions && records.Count("wqe_starts") == submitted_transfers &&
                    records.Count("wqe_completions") == submitted_transfers && records.Count("transfers") == submitted_transfers &&
                    report["active_wqes"] == Json(std::vector<unsigned>(rank_count, 0)) &&
                    report["csv_completed_tasks"] == 0, "Service completion invariant failed");
            for (const auto& peak : report["peak_wqes"])
                Require(peak <= send_slots, "Layer SEND slot limit exceeded");
            moe_ub::FlushAstraLogging();
            Json endpoints = Json::array(), operations = Json::array();
            if (!resource_budget.is_null()) report["graph_resources"]={{"budget",resource_budget},{"used",resource_used}};
            report["node_execution"] = Json::array();
            uint64_t endpoint_offset = 0;
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
                    record["local_record_id"] = record.at("local_record_id").get<uint64_t>() + endpoint_offset;
                    endpoints.push_back(record);
                }
                for (auto& operation : semantic)
                {
                    for (auto& record : operation["endpoint_records"])
                        record["local_record_id"] = record.at("local_record_id").get<uint64_t>() + endpoint_offset;
                    operations.push_back(operation);
                }
                endpoint_offset += plans[step].data.contains("layers") ? 2 * plans[step].data.at("layers").size() : 2;
            }
            std::optional<moe_ub::ObservationRegistry> registry;
            if (records.Bounded())
            {
                retire_provenance();
                execution_archive.Read(records.Store()->Directory(), moe_ub::DrainAstraExecutionArchive());
                Require(plans.empty() && transfers.empty() && local_deliveries.empty() && provenance_retirement.empty() &&
                        execution_archive.ActiveGraphs() == 0 && execution_archive.ResidentNodes() == 0,
                        "Completed service retains graph or execution state");
                for (const auto& adapter : adapters)
                    Require(adapter->RegisteredTransfers() == 0 && adapter->RegisteredLocalDeliveries() == 0,
                            "Completed service retains borrowed transfer pointers");
                report["resource_lifecycle"] = {{"schema_version", "moe-ub-service-lifecycle/1"},
                    {"retired_graphs", retired_graphs}, {"peak_resident_graphs", peak_plans},
                    {"peak_resident_transfers", peak_transfers}, {"peak_resident_local", peak_local},
                    {"resident_graphs_at_exit", 0}, {"resident_transfers_at_exit", 0}, {"adapter_transfers_at_exit", 0},
                    {"execution_events_read", execution_archive.EventsRead()}, {"boundaries", retirement_boundaries},
                    {"transfer_identity_intervals", transfer_ids.Intervals()}, {"task_identity_intervals", tasks.Intervals()},
                    {"tag_identity_intervals", tags.Intervals()}};
                if (provenance) report["resource_lifecycle"]["provenance"] = provenance->Ledger().RetirementAudit();
            }
            if (!observations.empty())
                registry = records.Bounded() ? moe_ub::ObservationRegistry::ReferToResult(
                    std::vector<int>(transfer_ranks.begin(), transfer_ranks.end()), submitted_transfers, "registry", record_truth) :
                    moe_ub::ObservationRegistry::Write(export_registry, endpoints, operations, "registry", record_truth);
            for (size_t index = 0; index < observations.size(); ++index)
            {
                const auto& config = observation_configs[index];
                const auto directory = multiple_ports ? "ports/" + std::to_string(config.switch_id) +
                    "-" + std::to_string(config.port_id) : ".";
                observations[index]->Export(export_registry, endpoints, operations, directory, &*registry);
            }
            if (provenance)
            {
                std::set<const moe_ub::WriteProvenance*> owners;
                for (const auto& observation : observations) owners.insert(observation->Provenance());
                Require(owners.size() == 1 && *owners.begin() == provenance.get(), "Ports must share run provenance");
                auto audit = provenance->Audit();
                audit["ledger_instances"] = owners.size();
                audit["port_observers"] = observations.size();
                std::ofstream output("provenance-audit.json"); output << audit.dump(2) << '\n'; output.close();
                Require(bool(output), "Cannot write provenance resource audit");
            }
            dl_iterate_phdr(Library, &report["loaded_libraries"]);
            records.Finish();
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
