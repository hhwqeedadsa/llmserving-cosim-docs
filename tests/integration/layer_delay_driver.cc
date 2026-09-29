// Test double for spec 12.3: execute unchanged Chakra graphs in the real ASTRA
// executor, with explicit completion events and no UB network construction.
#include "astra_logging.hh"
#include "layer_plan.hh"
#include "ub_network_adapter.hh"
#include "write_completion.hh"
#include "astra-sim/system/Sys.hh"
#include "ns3/node-list.h"
#include "ns3/simulator.h"

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
    if (!ok) throw std::runtime_error("Delay test: " + message);
}
Json Read(const fs::path& path)
{
    std::ifstream stream(path);
    Require(bool(stream), "cannot read " + path.string());
    return Json::parse(stream);
}
uint64_t Number(const Json& value, uint64_t maximum)
{
    Require(value.is_number_unsigned() && value.get<uint64_t>() <= maximum,
            "invalid unsigned event identity/time");
    return value.get<uint64_t>();
}
uint64_t Now() { return ns3::Simulator::Now().GetNanoSeconds(); }

struct Transfer
{
    const moe_ub::WriteDescriptor descriptor;
    const uint64_t data_ns, transaction_ns;
    moe_ub::WriteCompletion completion;
    std::optional<uint64_t> send_start, receive_start;
    ns3::EventId data_event, transaction_event;
    Json& events;

    Transfer(const moe_ub::WriteDescriptor& d, const Json& times, Json& log)
        : descriptor(d), data_ns(times.at("data_ready_ns")),
          transaction_ns(times.at("transaction_done_ns")),
          completion("test transfer=" + std::to_string(d.transfer_id), d.bytes, {}), events(log)
    {}
    ~Transfer() { data_event.Cancel(); transaction_event.Cancel(); }
    void Record(const char* event)
    {
        events.push_back({{"transfer_id", descriptor.transfer_id}, {"event", event}, {"time_ns", Now()}});
    }
    void Register(bool send, uint64_t bytes, int type, void (*callback)(void*), void* arg)
    {
        Require(bytes == descriptor.bytes && type == AstraSim::UINT8 && callback,
                "SEND/RECV payload or callback mismatch");
        if (!send)
        {
            completion.RegisterReceive(callback, arg);
            receive_start = Now(); Record("receive_registered");
            return;
        }
        // Never clamp an impossible schedule to the current time.
        Require(data_ns >= Now() && transaction_ns >= Now(), "given event precedes SEND issue");
        completion.RegisterSend(callback, arg);
        send_start = Now(); Record("send_registered");
        data_event = ns3::Simulator::Schedule(ns3::NanoSeconds(data_ns - Now()), [this]() {
            Record("data_ready"); completion.WriteCommitted(0, 0, descriptor.bytes);
        });
        transaction_event = ns3::Simulator::Schedule(ns3::NanoSeconds(transaction_ns - Now()), [this]() {
            Record("transaction_done"); completion.TransactionDone();
        });
    }
};

// Composition reuses the verified ns-3 clock/scheduler; only transport calls are
// replaced. This class is not compiled into any production execution driver.
class GivenEvents final : public AstraSim::AstraNetworkAPI
{
  public:
    GivenEvents(int rank, moe_ub::UbNetworkAdapter::Finished finished,
                const std::vector<std::unique_ptr<Transfer>>& transfers)
        : AstraNetworkAPI(rank), clock_(rank, std::move(finished))
    {
        for (const auto& t : transfers)
            registry_.emplace(std::make_tuple(t->descriptor.src_rank, t->descriptor.dst_rank,
                                             t->descriptor.tag), t.get());
    }
    BackendType get_backend_type() override { return clock_.get_backend_type(); }
    AstraSim::timespec_t sim_get_time() override { return clock_.sim_get_time(); }
    void sim_schedule(AstraSim::timespec_t delta, void (*cb)(void*), void* arg) override
    { clock_.sim_schedule(delta, cb, arg); }
    void sim_notify_finished() override { clock_.sim_notify_finished(); }
    int sim_send(void*, uint64_t bytes, int type, int dst, int tag, AstraSim::sim_request*,
                 void (*cb)(void*), void* arg) override
    { return Issue(true, rank, dst, tag, bytes, type, cb, arg); }
    int sim_recv(void*, uint64_t bytes, int type, int src, int tag, AstraSim::sim_request*,
                 void (*cb)(void*), void* arg) override
    { return Issue(false, src, rank, tag, bytes, type, cb, arg); }
    const std::string& Failure() const { return failure_; }
  private:
    int Issue(bool send, int src, int dst, int tag, uint64_t bytes, int type,
              void (*cb)(void*), void* arg)
    {
        try { registry_.at({src, dst, tag})->Register(send, bytes, type, cb, arg); }
        catch (const std::exception& error)
        {
            // Locked Sys catches callback exceptions without rethrowing. Retain the
            // original cause for this driver's final failure diagnostic.
            if (failure_.empty()) failure_ = error.what();
            throw;
        }
        return 0;
    }
    moe_ub::UbNetworkAdapter clock_;
    std::map<std::tuple<int, int, int>, Transfer*> registry_;
    std::string failure_;
};
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
        Require(argc == 5, "usage: cosim-layer-delay-test PLAN_DIR SYSTEM.json EVENTS.json NEW_RUN_DIR");
        auto plan_dir = fs::absolute(argv[1]), run = fs::absolute(argv[4]);
        const auto plan = moe_ub::LoadLayerPlan(plan_dir);
        Require(plan.data.at("rank_ids") == Json::array({0, 1, 2, 3}), "Delay driver requires ranks 0..3");
        const auto system = Read(fs::absolute(argv[2])), schedule = Read(fs::absolute(argv[3]));
        Require(system.at("roofline-enabled") == 0 && system.at("replay-only") == 0 &&
                system.at("trace-enabled") == 1, "original ASTRA execution/trace required");
        Require(schedule.size() == 2 && schedule.at("schema_version") == "moe-ub-given-events/1" &&
                schedule.at("events").is_array(), "invalid event table schema");
        std::map<uint64_t, Json> times;
        const uint64_t ready = plan.data.at("ready_ns"), bound = ready + 1000000;
        for (const auto& event : schedule.at("events"))
        {
            Require(event.is_object() && event.size() == 3, "invalid event fields");
            auto id = Number(event.at("transfer_id"), UINT64_MAX);
            for (const char* field : {"data_ready_ns", "transaction_done_ns"})
                Require(Number(event.at(field), bound - 1) >= ready, "event precedes graph activation");
            Require(times.emplace(id, event).second, "duplicate transfer in event table");
        }
        Require(times.size() == plan.descriptors.size(), "event table must cover every transfer");
        for (const auto& d : plan.descriptors) Require(times.contains(d.transfer_id), "unknown/missing transfer in event table");
        Require(!fs::exists(run) && fs::create_directory(run), "run directory must be new with an existing parent");
        fs::current_path(run);
        for (int rank = 0; rank < 4; ++rank)
            fs::copy_file(plan_dir / ("layer." + std::to_string(rank) + ".et"), "layer." + std::to_string(rank) + ".et");
        std::ofstream("plan.json") << plan.data.dump(2) << '\n';
        std::ofstream("system.json") << system.dump(2) << '\n';
        std::ofstream("given-events.json") << schedule.dump(2) << '\n';
        ns3::Time::SetResolution(ns3::Time::NS);
        moe_ub::InitializeAstraLogging();
        Json report = {{"schema_version", "moe-ub-delay-test-result/1"}, {"event_source", "test_double"},
            {"graph_id", plan.data.at("graph_id")}, {"events", Json::array()}, {"transfers", Json::array()},
            {"graph_completions", Json::array()}, {"activation_order", Json::array()},
            {"rank_resources", Json::array()}, {"loaded_libraries", Json::array()}};
        {
            std::vector<std::unique_ptr<Transfer>> transfers;
            for (const auto& d : plan.descriptors)
                transfers.push_back(std::make_unique<Transfer>(d, times.at(d.transfer_id), report["events"]));
            std::vector<std::unique_ptr<GivenEvents>> adapters;
            std::vector<std::unique_ptr<AstraSim::Sys>> systems;
            for (int rank = 0; rank < 4; ++rank)
            {
                adapters.push_back(std::make_unique<GivenEvents>(rank, [&, rank](uint64_t now) {
                    report["graph_completions"].push_back({{"rank_id", rank}, {"time_ns", now}});
                }, transfers));
                systems.push_back(std::make_unique<AstraSim::Sys>(rank, "layer", "empty", "system.json",
                    std::vector<AstraSim::AstraMemoryAPI*>{}, adapters.back().get(),
                    std::vector<int>{4}, std::vector<int>{1}, 1.0, 1.0, false));
            }
            report["loaded_ranks_before_activation"] = systems.size();
            for (int rank = 0; rank < 4; ++rank)
                ns3::Simulator::Schedule(ns3::NanoSeconds(ready), [&, rank]() {
                    report["activation_order"].push_back({{"rank_id", rank}, {"time_ns", Now()}});
                    systems[rank]->workload->fire();
                });
            ns3::Simulator::Stop(ns3::NanoSeconds(bound));
            ns3::Simulator::Run();
            for (const auto& adapter : adapters) Require(adapter->Failure().empty(), adapter->Failure());
            for (const auto& t : transfers)
            {
                const auto& done = t->completion;
                Require(!done.Active() && done.SendCallbacks() == 1 && done.ReceiveCallbacks() == 1 &&
                        done.CommittedBytes() == t->descriptor.bytes, "incomplete test transfer");
                report["transfers"].push_back({{"transfer_id", t->descriptor.transfer_id},
                    {"send_start_ns", *t->send_start}, {"receive_start_ns", *t->receive_start},
                    {"data_ready_ns", *done.DataReadyNs()}, {"transaction_done_ns", *done.TransactionDoneNs()},
                    {"send_done_ns", *done.SendDoneNs()}, {"receive_done_ns", *done.ReceiveDoneNs()},
                    {"send_callbacks", done.SendCallbacks()}, {"receive_callbacks", done.ReceiveCallbacks()},
                    {"committed_bytes", done.CommittedBytes()}, {"context_active", done.Active()}});
            }
            for (int rank = 0; rank < 4; ++rank)
            {
                const auto* w = systems[rank]->workload;
                const auto* h = w->hw_resource;
                Require(w->is_finished && !w->et_feeder->hasNodesToIssue() && h->num_in_flight_cpu_ops == 0 &&
                        h->num_in_flight_gpu_comp_ops == 0 && h->num_in_flight_gpu_comm_ops == 0 &&
                        systems[rank]->pending_events == 0, "unfinished ASTRA rank/resources");
                report["rank_resources"].push_back({{"rank_id", rank}, {"finished", w->is_finished},
                    {"cpu_slots", h->num_in_flight_cpu_ops}, {"compute_slots", h->num_in_flight_gpu_comp_ops},
                    {"send_slots", h->num_in_flight_gpu_comm_ops}, {"pending_events", systems[rank]->pending_events}});
            }
            Require(report["graph_completions"].size() == 4 && ns3::NodeList::GetNNodes() == 0,
                    "expected four complete graphs and no network objects");
            report["network_nodes"] = ns3::NodeList::GetNNodes();
            moe_ub::FlushAstraLogging();
            report["node_execution"] = moe_ub::ReadLayerExecution(plan, "log/log.log");
            dl_iterate_phdr(Library, &report["loaded_libraries"]);
        }
        ns3::Simulator::Destroy(); moe_ub::ShutdownAstraLogging();
        std::ofstream output("result.json"); output << report.dump(2) << '\n'; output.close();
        Require(bool(output), "cannot save result");
        std::cout << "Test double result: " << (run / "result.json") << '\n';
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << "Delay test failure: " << error.what() << '\n';
        ns3::Simulator::Destroy(); moe_ub::ShutdownAstraLogging();
        return 1;
    }
}
